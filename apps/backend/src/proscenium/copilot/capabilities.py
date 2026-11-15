"""Supported host discovery and non-materialising authored-document validation."""

import json
from pathlib import Path
from typing import Any

import httpx

from proscenium.api.errors import DomainError
from proscenium.catalogue import validation
from proscenium.contracts import canonical, validate
from proscenium.db.store import now
from proscenium.providers import hunyuan
from proscenium.providers.plugins import plugin
from proscenium.providers.preparation import AMBIGUOUS, bounded_get, compatible_recipes, models
from proscenium.sources.common import git, relative
from proscenium.sources.read_only import read_authored_json, read_closure_files

from .native import Configuration

RECOMMENDED_TOOLS = frozenset(
    {
        "view",
        "bash",
        "skill",
        "get_project_capabilities",
        "validate_authored_document",
        "prepare_image_generation",
        "checkpoint_sources",
        "publish_outputs",
        "request_preview",
        "register_artifact",
        "submit_generation",
        "request_vlm_review",
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


def get_project_capabilities(services: Any, project_id: str) -> dict[str, Any]:
    config = Configuration.load(services.settings.runtime)
    provider = services.extension("providers")
    skills: set[str] = set()
    for directory in config.skill_directories:
        root = Path(directory)
        if root.is_dir():
            skills.update(
                child.name
                for child in root.iterdir()
                if child.is_dir() and (child / "SKILL.md").is_file()
            )
    rows = []
    for service in services.settings.services:
        for capability in service["capabilities"]:
            revision = (
                provider.options.get("image_profiles", {}).get(
                    service["provider_id"], "comfy-image-1"
                )
                if capability == "image"
                else plugin("image_to_3d").revision
                if capability == "image_to_3d"
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
            try:
                catalog = bounded_get(
                    provider, service, "/v1/models" if capability == "vlm" else "/object_info"
                )
            except (httpx.HTTPError, DomainError, ValueError, TypeError):
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
                else:
                    graph = hunyuan.graph("reference")
                    adapter = plugin("image_to_3d")
                    adapter.preflight(
                        adapter.bind_graph(
                            graph, "", "0", {"reference": "artifact:reference"}, [hunyuan.FILENAME]
                        ),
                        catalog,
                        [hunyuan.FILENAME],
                    )
                    row.update(
                        status="ready",
                        selected_models=[hunyuan.FILENAME],
                        summary="Observed Hunyuan preflight passed. Inference remains untested",
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
    result = dict(
        schema_version="1.0",
        project_id=project_id,
        observed_at=now(),
        exposed_tools=list(config.worker_tools),
        missing_worker_tools=sorted(RECOMMENDED_TOOLS - set(config.worker_tools)),
        available_skills=sorted(skills),
        skill_loader_exposed="skill" in config.worker_tools,
        providers=rows,
        rendering_ready=services.settings.rendering.get("profile_id", "blender-cpu")
        in services.settings.execution_profiles,
        blender_mcp_configured=bool(config.mcp_servers),
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
    validate("AuthoredDocumentValidationRequest", request)
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
                batch = {
                    r["version_id"]: {**r, "project_id": project_id} for r in document["items"]
                }
                documents, maps = {}, {}
                with store.transaction(write=False) as tx:
                    catalogue.validate_batch(tx, document)
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
                        message="Authored document relationships or pinned sources failed validation",
                    )
                ]
    return validate("AuthoredDocumentValidationResult", result)
