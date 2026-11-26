"""Supported host discovery and non-materialising authored-document validation."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from proscenium.api.errors import DomainError
from proscenium.catalogue import validation
from proscenium.catalogue.service import CHECKPOINT_SOURCE_CONFLICT
from proscenium.catalogue.validation import SAFE_VALIDATION_REASONS
from proscenium.contracts import canonical, validate
from proscenium.db.store import now
from proscenium.previews.journal import (
    UNSUPPORTED_BLENDER_FEATURE_REASON,
    unsupported_blender_features,
)
from proscenium.providers import budget3d, hunyuan, minimax_h3, stable_audio
from proscenium.providers.plugins import plugin
from proscenium.providers.preparation import (
    AMBIGUOUS,
    audio_revision,
    bounded_get,
    compatible_audio_recipes,
    compatible_recipes,
    compatible_video_recipes,
    models,
    video_recipe,
)
from proscenium.sources.common import git, relative
from proscenium.sources.read_only import read_authored_json, read_closure_files

from .native import (
    Configuration,
    is_blender_mcp_configured,
    is_blendswap_mcp_configured,
    resolve_mcp_servers,
)

RECOMMENDED_TOOLS = frozenset(
    {
        "view",
        "bash",
        "skill",
        "get_project_capabilities",
        "search_web",
        "web_fetch",
        "validate_authored_document",
        "prepare_image_generation",
        "prepare_audio_generation",
        "prepare_video_generation",
        "checkpoint_sources",
        "publish_outputs",
        "request_preview",
        "set_preview_order",
        "register_artifact",
        "submit_generation",
        "request_vlm_review",
        "get_vlm_review",
        "escalate_vlm_review",
        "get_job",
        "get_artifact",
        "get_version",
        "list_job_frames",
        "list_items",
        "assemble_sequence",
        "run_unity_workflow",
        "get_unity_result",
    }
)
KINDS = {
    "authored_shot_plan": "AuthoredShotPlan",
    "sequence_map": "SequenceMap",
    "publication_submit": "PublicationSubmit",
}
ProbeKey = tuple[str, str, str]


def probe_provider_capabilities(services: Any) -> list[dict[str, Any]]:
    provider = services.extension("providers")
    rows: list[dict[str, Any]] = []
    unique_fetches: dict[ProbeKey, dict[str, Any]] = {}
    planned: list[tuple[dict[str, Any], list[tuple[dict[str, Any], ProbeKey]]]] = []
    for service in services.settings.services:
        pending: list[tuple[dict[str, Any], ProbeKey]] = []
        planned.append((service, pending))
        mesh_recipe = provider.options.get("mesh_recipes", {}).get(service["provider_id"])
        for capability in service["capabilities"]:
            revision = (
                provider.options.get("image_profiles", {}).get(
                    service["provider_id"], "comfy-image-1"
                )
                if capability == "image"
                else (
                    provider.options.get("mesh_profiles", {}).get(service["provider_id"])
                    or (
                        mesh_recipe.get("profile_revision")
                        if isinstance(mesh_recipe, dict)
                        else None
                    )
                    or plugin("image_to_3d").revision
                )
                if capability == "image_to_3d"
                else audio_revision(provider.options, service["provider_id"])
                if capability == "audio"
                else minimax_h3.REVISION
                if capability == "video"
                else None
            )
            row = dict(
                provider_id=service["provider_id"],
                capability=capability,
                status="unavailable",
                profile_revision=revision,
                selected_models=[],
                compatible_models=[],
                reference_dimensions=getattr(
                    plugin("image", revision), "reference_dimensions", None
                )
                if capability == "image"
                else None,
                reason_code=None,
                summary="",
                default_dimensions=None,
                default_steps=None,
                max_pixels=None,
            )
            if capability == "image":
                explicit = provider.options.get("image_recipes", {}).get(service["provider_id"])
                if isinstance(explicit, dict):
                    row.update(
                        default_dimensions={
                            "width": explicit.get("width", 512),
                            "height": explicit.get("height", 512),
                        },
                        default_steps=explicit.get("steps", 4),
                        max_pixels=explicit.get(
                            "max_pixels", 1_048_576 if revision == "comfy-image-1" else 512 * 512
                        ),
                    )
            elif capability == "image_to_3d":
                default_mesh_steps = (
                    20
                    if revision == "hunyuan3d-native-2.0-1"
                    else 5
                    if revision == budget3d.TURBO_REVISION
                    else 50
                )
                if isinstance(mesh_recipe, dict):
                    row["default_steps"] = mesh_recipe.get("steps", default_mesh_steps)
            elif capability == "video":
                configured = video_recipe(provider.options, service["provider_id"])
                row.update(
                    default_dimensions={
                        "width": configured["width"],
                        "height": configured["height"],
                    },
                    default_steps=configured["steps"],
                    max_pixels=configured["max_pixels"],
                )
            rows.append(row)
            if service["available"] is False:
                row.update(
                    reason_code="configured_unavailable",
                    summary="Provider is configured unavailable",
                )
                continue
            if capability == "vlm" and not service["model_id"]:
                row.update(
                    reason_code="model_not_configured", summary="Configure a vision model ID"
                )
                continue
            if capability == "audio":
                try:
                    stable_audio.resolve_audio_verifier(services.settings.rendering)
                except DomainError as exc:
                    row.update(reason_code="audio_verifier_missing", summary=exc.message)
                    continue
            elif capability == "video":
                try:
                    minimax_h3.resolve_video_verifier(services.settings.rendering)
                except DomainError as exc:
                    row.update(reason_code="video_verifier_missing", summary=exc.message)
                    continue
            path = "/v1/models" if capability == "vlm" else "/object_info"
            key = (service["provider_id"], service["endpoint"], path)
            unique_fetches.setdefault(key, service)
            pending.append((row, key))

    def fetch(key: ProbeKey) -> dict[str, Any] | None:
        try:
            return bounded_get(provider, unique_fetches[key], key[2])
        except (httpx.HTTPError, DomainError, ValueError, TypeError):
            return None

    if len(unique_fetches) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(unique_fetches))) as pool:
            catalogues = dict(zip(unique_fetches, pool.map(fetch, unique_fetches), strict=True))
    else:
        catalogues = {key: fetch(key) for key in unique_fetches}

    for service, pending in planned:
        mesh_recipe = provider.options.get("mesh_recipes", {}).get(service["provider_id"])
        for row, key in pending:
            capability, revision = row["capability"], row["profile_revision"]
            catalog = catalogues[key]
            if catalog is None:
                row.update(
                    reason_code="provider_unreachable",
                    summary="Provider catalogue could not be read",
                )
                continue
            try:
                if capability == "vlm":
                    listed = any(
                        isinstance(m, dict) and m.get("id") == service["model_id"]
                        for m in catalog.get("data", [])
                    )
                    row.update(
                        status="configured" if listed else "unavailable",
                        selected_models=[service["model_id"]] if listed else [],
                        reason_code=None if listed else "model_unavailable",
                        summary="Model listing does not prove vision inference"
                        if listed
                        else "Configured model is not listed",
                    )
                elif capability == "image":
                    recipes = compatible_recipes(provider, service["provider_id"], catalog)
                    row["compatible_models"] = sorted({m for r in recipes for m in models(r)})[:64]
                    if len(recipes) == 1:
                        recipe = recipes[0]
                        width, height = recipe.get("width", 512), recipe.get("height", 512)
                        steps = recipe.get("steps", 4)
                        row.update(
                            status="ready",
                            selected_models=models(recipe),
                            default_dimensions={"width": width, "height": height},
                            default_steps=steps,
                            max_pixels=recipe.get(
                                "max_pixels",
                                1_048_576 if revision == "comfy-image-1" else 512 * 512,
                            ),
                            summary=f"Observed recipe preflight passed. Defaults: {width}x{height}, {steps} steps. Inference remains untested",
                        )
                    elif recipes:
                        row.update(
                            status="ambiguous",
                            reason_code="recipe_selection_required",
                            summary=AMBIGUOUS,
                        )
                    else:
                        row.update(
                            reason_code="nodes_or_models_missing",
                            summary="Image recipe nodes or models are unavailable",
                        )
                elif capability == "audio":
                    recipes = compatible_audio_recipes(provider, service["provider_id"], catalog)
                    if recipes:
                        selected = list(
                            dict.fromkeys(
                                model for recipe in recipes for model in recipe["model_ids"]
                            )
                        )
                        modes = ", ".join(recipe["mode"] for recipe in recipes)
                        row.update(
                            status="ready",
                            selected_models=selected,
                            compatible_models=sorted(selected),
                            default_steps=recipes[0]["steps"],
                            summary=f"Stable Audio preflight passed for {modes}. Inference remains untested",
                        )
                    else:
                        row.update(
                            reason_code="nodes_or_models_missing", summary=stable_audio.UNAVAILABLE
                        )
                elif capability == "video":
                    recipes = compatible_video_recipes(provider, service["provider_id"], catalog)
                    if recipes:
                        modes = ", ".join(dict.fromkeys(recipe["mode"] for recipe in recipes))
                        row.update(
                            status="ready",
                            selected_models=recipes[0]["model_ids"],
                            compatible_models=sorted(
                                {model for recipe in recipes for model in recipe["model_ids"]}
                            ),
                            default_steps=recipes[0]["steps"],
                            summary=f"MiniMax H3 preflight passed for {modes}. Inference remains untested",
                        )
                    else:
                        row.update(
                            reason_code="nodes_or_models_missing", summary=minimax_h3.UNAVAILABLE
                        )
                else:
                    assert revision is not None
                    adapter = plugin("image_to_3d", revision)
                    if revision == "hunyuan3d-native-2.0-1":
                        graph = hunyuan.graph("reference")
                        mesh_models = [hunyuan.FILENAME]
                        steps = 20
                        summary = "Observed Hunyuan preflight passed. Inference remains untested"
                    else:
                        cfg = mesh_recipe if isinstance(mesh_recipe, dict) else {}
                        turbo = revision == budget3d.TURBO_REVISION
                        graph = budget3d.recipe_graph("reference", revision, cfg)
                        mesh_models = budget3d.models(turbo=turbo)
                        steps = cfg.get("steps", 5 if turbo else 50)
                        mode = cfg.get("mode", "synthetic")
                        budget_val = cfg.get("budget", 1.0)
                        summary = (
                            f"Observed Budget3D preflight passed. Defaults: {mode}, "
                            f"budget={budget_val}, {steps} steps. Inference remains untested"
                        )
                    adapter.preflight(
                        adapter.bind_graph(
                            graph, "", "0", {"reference": "artifact:reference"}, mesh_models
                        ),
                        catalog,
                        mesh_models,
                    )
                    row.update(
                        status="ready",
                        selected_models=mesh_models,
                        compatible_models=sorted(mesh_models),
                        default_steps=steps,
                        summary=summary,
                    )
            except (
                DomainError,
                ValueError,
                TypeError,
                KeyError,
                IndexError,
                AttributeError,
            ) as exc:
                ambiguous = isinstance(exc, DomainError) and exc.message == AMBIGUOUS
                row.update(
                    status="ambiguous" if ambiguous else "unavailable",
                    reason_code="recipe_selection_required"
                    if ambiguous
                    else "nodes_or_models_missing",
                    summary=AMBIGUOUS if ambiguous else "Required nodes or models are unavailable",
                )
    return rows


def get_configuration_status(services: Any) -> dict[str, Any]:
    runtime = services.settings.runtime
    raw_servers = runtime.get("mcp_servers", {})
    servers = resolve_mcp_servers(
        raw_servers if isinstance(raw_servers, dict) else {}, services.settings.secret_env
    )
    editor = runtime.get("unity_editor")
    return validate(
        "ConfigurationStatus",
        dict(
            schema_version="1.0",
            observed_at=now(),
            providers=probe_provider_capabilities(services),
            rendering_ready=services.settings.rendering.get("profile_id", "blender-cpu")
            in services.settings.execution_profiles,
            blender_mcp_configured=is_blender_mcp_configured(servers),
            blendswap_mcp_configured=is_blendswap_mcp_configured(servers),
            unity_configured="unity-local" in services.settings.execution_profiles
            and bool(editor and Path(editor).is_file()),
        ),
    )


def get_project_capabilities(services: Any, project_id: str) -> dict[str, Any]:
    config = Configuration.load(services.settings.runtime)
    skills: set[str] = set()
    for directory in config.skill_directories:
        root = Path(directory)
        if root.is_dir():
            skills.update(
                child.name
                for child in root.iterdir()
                if child.is_dir() and (child / "SKILL.md").is_file()
            )
    result = dict(
        schema_version="1.0",
        project_id=project_id,
        observed_at=now(),
        exposed_tools=list(config.worker_tools),
        missing_worker_tools=sorted(RECOMMENDED_TOOLS - set(config.worker_tools)),
        available_skills=sorted(skills),
        skill_loader_exposed="skill" in config.worker_tools,
        providers=probe_provider_capabilities(services),
        rendering_ready=services.settings.rendering.get("profile_id", "blender-cpu")
        in services.settings.execution_profiles,
        blender_mcp_configured=is_blender_mcp_configured(
            resolve_mcp_servers(config.mcp_servers, services.settings.secret_env)
        ),
        unity_configured="unity-local" in services.settings.execution_profiles
        and config.unity_editor is not None
        and config.unity_editor.is_file(),
    )
    return validate("ProjectCapabilities", result)


def load_document(root: Path, path: str) -> Any:
    source = relative(root, path, exists=True)
    if not source.is_file() or source.stat().st_size > 2 * 1024**2:
        raise DomainError("source_unavailable", 422)
    with source.open("rb") as stream:
        data = stream.read(2 * 1024**2 + 1)
    if len(data) > 2 * 1024**2:
        raise DomainError("source_unavailable", 422)
    return json.loads(data)


def impact(plan: dict[str, Any], changed: set[str]) -> dict[str, Any]:
    global_ids = {plan["plan_id"], *plan["brief_artifact_ids"]}
    for dependency in plan["asset_dependencies"]:
        global_ids.update(dependency.values())
    affected: list[str] = []
    frames: list[str] = []
    artifacts: set[str] = set()
    scenes: set[str] = set()
    for shot in plan["shots"]:
        local_ids = {shot["shot_id"]}
        if shot["scene_version"]:
            local_ids.update(shot["scene_version"].values())
        for frame in shot["frames"]:
            local_ids.add(frame["frame_id"])
            if frame["artifact_id"]:
                local_ids.add(frame["artifact_id"])
        if changed & (global_ids | local_ids):
            affected.append(shot["shot_id"])
            frames.extend(frame["frame_id"] for frame in shot["frames"])
            artifacts.update(
                frame["artifact_id"] for frame in shot["frames"] if frame["artifact_id"]
            )
            if shot["scene_version"]:
                scenes.add(shot["scene_version"]["version_id"])
    return dict(
        affected_shot_ids=affected,
        affected_frame_ids=frames,
        downstream_artifact_ids=sorted(artifacts),
        downstream_scene_version_ids=sorted(scenes),
    )


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old = {s["shot_id"]: s for s in before["shots"]}
    new = {s["shot_id"]: s for s in after["shots"]}
    changed = {
        key for key in old.keys() | new.keys() if canonical(old.get(key)) != canonical(new.get(key))
    }
    reordered = list(old) != list(new)
    if reordered or any(
        canonical(before.get(k)) != canonical(after.get(k))
        for k in ("plan_id", "project_id", "treatment", "brief_artifact_ids", "asset_dependencies")
    ):
        changed.update(old.keys() | new.keys())
    reports = [impact(plan, changed) for plan in (before, after)]
    return dict(
        changed_shot_ids=sorted(changed),
        removed_shot_ids=sorted(old.keys() - new.keys()),
        removed_frame_ids=sorted(
            {f["frame_id"] for s in old.values() for f in s["frames"]}
            - {f["frame_id"] for s in new.values() for f in s["frames"]}
        ),
        shot_order_changed=reordered,
        stable_plan_id=before["plan_id"] == after["plan_id"],
        **{key: sorted(set(reports[0][key]) | set(reports[1][key])) for key in reports[0]},
    )


def validate_authored_document(
    services: Any, project_id: str, request: dict[str, Any]
) -> dict[str, Any]:
    try:
        validate("AuthoredDocumentValidationRequest", request)
    except DomainError as exc:
        if exc.code == "invalid_request" and not exc.details.get("issues"):
            issues = validation.schema_issues("AuthoredDocumentValidationRequest", request)
            if issues:
                raise DomainError(exc.code, exc.status, exc.message, issues=issues) from exc
        raise
    if request["project_id"] != project_id:
        raise DomainError("scope_denied", 403)
    root = Path(services.registry.get(project_id)["path"])
    kind = request["document_kind"]
    result: dict[str, Any] = dict(
        schema_version="1.0",
        project_id=project_id,
        document_kind=kind,
        valid=False,
        schema_valid=False,
        semantic_valid=False,
        issues=[],
    )
    try:
        document = (
            request["document"] if "document" in request else load_document(root, request["path"])
        )
        if len(canonical(document)) > 2 * 1024**2:
            raise DomainError("source_unavailable", 422)
        if not isinstance(document, dict):
            raise ValueError("not object")
    except (ValueError, UnicodeError):
        result["issues"] = [
            dict(
                phase="json",
                code="not_json_object",
                path=request.get("path", "$"),
                message="Storyboard source must be AuthoredShotPlan JSON"
                if kind == "authored_shot_plan"
                else "Authored document must be a JSON object",
            )
        ]
        return validate("AuthoredDocumentValidationResult", result)
    except (DomainError, OSError):
        result["issues"] = [
            dict(
                phase="source",
                code="source_unavailable",
                path="$",
                message="Authored source is unavailable within the active project",
            )
        ]
        return validate("AuthoredDocumentValidationResult", result)
    result["issues"] = validation.schema_issues(KINDS[kind], document)
    result["schema_valid"] = not result["issues"]
    if result["schema_valid"]:
        try:
            catalogue = services.extension("catalogue")
            store = services.registry.project_store(project_id)
            if kind == "publication_submit":
                if document["project_id"] != project_id:
                    raise DomainError("scope_denied", 403)
                documents, maps = {}, {}
                with store.transaction(write=False) as tx:
                    document = catalogue.resolve_checkpoint_sources(tx, document)
                    for index, item in enumerate(document["items"]):
                        runtime = item["source"]["required_runtime"]
                        if runtime[
                            "application"
                        ].strip().lower() == "blender" and unsupported_blender_features(
                            runtime["features"]
                        ):
                            result["issues"].append(
                                dict(
                                    phase="preflight",
                                    code="capability_unavailable",
                                    path=f"items[{index}].source.required_runtime.features",
                                    message=UNSUPPORTED_BLENDER_FEATURE_REASON,
                                )
                            )
                            if len(result["issues"]) >= 25:
                                break
                    if result["issues"]:
                        return validate("AuthoredDocumentValidationResult", result)
                    catalogue.validate_batch(tx, document)
                batch = {
                    r["version_id"]: {**r, "project_id": project_id} for r in document["items"]
                }
                for row in batch.values():
                    git(root, "cat-file", "-e", row["source_commit"] + "^{commit}")
                    files = read_closure_files(services, project_id, row["source"], batch=batch)
                    entry = next(
                        f
                        for f in row["source"]["files"]
                        if f["destination"] == row["source"]["entry_path"]
                    )
                    if (
                        entry["origin"]["kind"] == "git"
                        and entry["origin"]["commit"] != row["source_commit"]
                    ):
                        raise DomainError("source_conflict", 409)
                    if row["kind"] in {"storyboard", "sequence"}:
                        authored = read_authored_json(services, project_id, row, batch=batch)
                        issues = validation.schema_issues(
                            "AuthoredShotPlan" if row["kind"] == "storyboard" else "SequenceMap",
                            authored,
                        )
                        if issues:
                            raise DomainError("invalid_request", 422, issues=issues)
                        documents[row["version_id"]] = authored
                        if row["kind"] == "sequence":
                            maps.update(catalogue.frame_maps(project_id, authored))
                    if row["source"]["entry_path"] not in files:
                        raise DomainError("dependency_missing", 422)
                    for identity in row["artifact_ids"]:
                        catalogue.artifacts.ready_path(project_id, identity)
                with store.transaction(write=False) as tx:
                    catalogue.validate_documents(tx, document, documents, maps)
            elif kind == "sequence_map":
                maps = catalogue.frame_maps(project_id, document)
                with store.transaction(write=False) as tx:
                    validation.sequence_map(tx, document, document["sequence_version_id"], maps)
            else:
                with store.transaction(write=False) as tx:
                    validation.shot_plan(tx, document, {})
                if request.get("compare_to_path"):
                    before = load_document(root, request["compare_to_path"])
                    validate("AuthoredShotPlan", before)
                    with store.transaction(write=False) as tx:
                        validation.shot_plan(tx, before, {})
                    result["impact"] = compare(before, document)
                if "changed_ids" in request:
                    changed = set(request["changed_ids"])
                    report = impact(document, changed)
                    if "impact" not in result:
                        result["impact"] = dict(
                            changed_shot_ids=sorted(report["affected_shot_ids"]),
                            removed_shot_ids=[],
                            removed_frame_ids=[],
                            shot_order_changed=False,
                            stable_plan_id=True,
                            **report,
                        )
                    else:
                        for key, values in report.items():
                            result["impact"][key] = sorted(set(result["impact"][key]) | set(values))
            result.update(semantic_valid=True, valid=True)
        except (DomainError, OSError, ValueError, KeyError, StopIteration) as exc:
            code = exc.code if isinstance(exc, DomainError) else "source_unavailable"
            result["issues"] = exc.details.get("issues", []) if isinstance(exc, DomainError) else []
            if not result["issues"]:
                result["issues"] = [
                    dict(
                        phase="semantic",
                        code=code,
                        path="$",
                        message=exc.message
                        if isinstance(exc, DomainError)
                        and (
                            exc.message in SAFE_VALIDATION_REASONS
                            or exc.message == CHECKPOINT_SOURCE_CONFLICT
                        )
                        else "Authored document relationships or pinned sources failed validation",
                    )
                ]
    return validate("AuthoredDocumentValidationResult", result)
