"""Local generation preparation and bounded read-only recipe discovery. No inference."""

import itertools
import math
import time
from collections.abc import Sequence
from typing import Any

import httpx

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.providers import (
    budget3d,
    hunyuan,
    klein,
    minimax_h3,
    sd_turbo,
    sd_turbo_fp16,
    stable_audio,
)
from proscenium.providers.comfy import endpoint
from proscenium.providers.comfy import supports_bf16_vae as supports_bf16_vae
from proscenium.providers.plugins import plugin
from proscenium.providers.targets import _request_context, resolve_target
from proscenium.sources.common import active, admitted, binding, replay, sha

AMBIGUOUS = "Multiple compatible image model tuples exist; configure provider_options.image_recipes"
UNAVAILABLE = "Image recipe nodes or models are unavailable"
MAX_CATALOG_BYTES = 16 * 1024**2
TILE_THRESHOLD_PIXELS = 1_048_576
SD_TURBO_REFERENCE = "Selected image profile requires one exact 512x512 reference"
BUDGET3D_OVERRIDES = frozenset({"mode", "budget", "steps", "num_gaussians", "mesh_resolution"})
HUNYUAN_REVISION = "hunyuan3d-native-2.0-1"


def mesh_revision(options: dict[str, Any], identity: str) -> str:
    recipe = options.get("mesh_recipes", {}).get(identity, {})
    return options.get("mesh_profiles", {}).get(
        identity, recipe.get("profile_revision", budget3d.TURBO_REVISION)
    )


def validate_recipes(options: dict[str, Any]) -> None:
    if not isinstance(options.get("image_profiles", {}), dict):
        raise ValueError("image_profiles must map provider IDs to installed profiles")
    recipes = options.get("image_recipes", {})
    if not isinstance(recipes, dict):
        raise ValueError("image_recipes must map provider IDs to recipe objects")
    for identity, recipe in recipes.items():
        validate("Id", identity)
        if not isinstance(recipe, dict):
            raise ValueError("Image recipe must be an object")
        revision = recipe.get("profile_revision")
        if not isinstance(revision, str) or revision != options.get("image_profiles", {}).get(
            identity, "comfy-image-1"
        ):
            raise ValueError("Image recipe profile_revision must agree with image_profiles")
        plugin("image", revision)
        keys = {"unet", "clip", "vae"} if revision == "comfy-image-1" else {"checkpoint"}
        if set(recipe) - (
            keys | {"profile_revision", "width", "height", "steps", "max_pixels"}
        ) or any(not isinstance(recipe.get(key), str) or not recipe[key].strip() for key in keys):
            raise ValueError("Image recipe requires exact model roles")
        if "max_pixels" in recipe:
            maximum = recipe["max_pixels"]
            if type(maximum) is not int or not 4096 <= maximum <= 16_777_216:
                raise ValueError("Image recipe max_pixels is outside supported bounds")
            if revision != "comfy-image-1" and maximum != 512 * 512:
                raise ValueError("SD-Turbo image recipes require a 512x512 pixel limit")
        for key in ("width", "height", "steps"):
            value = recipe.get(key, 4 if key == "steps" else 512)
            if type(value) is not int or not (
                1 <= value <= 50 if key == "steps" else 64 <= value <= 4096 and value % 16 == 0
            ):
                raise ValueError("Image recipe dimensions/steps are outside supported bounds")
            if revision != "comfy-image-1" and value != (4 if key == "steps" else 512):
                raise ValueError("SD-Turbo image recipes require 512x512 and four steps")
        if revision != "comfy-image-1" and recipe["checkpoint"] != (
            sd_turbo.FILENAME if revision == sd_turbo.REVISION else sd_turbo_fp16.FILENAME
        ):
            raise ValueError("SD-Turbo image recipe must use the pinned checkpoint")

    validate_audio_recipes(options)
    validate_video_recipes(options)
    mesh_profiles = options.get("mesh_profiles", {})
    if not isinstance(mesh_profiles, dict):
        raise ValueError("mesh_profiles must map provider IDs to installed mesh profiles")
    for identity, revision in mesh_profiles.items():
        validate("Id", identity)
        if not isinstance(revision, str):
            raise ValueError("Mesh profile revision must be a string")
        plugin("image_to_3d", revision)
    mesh_recipes = options.get("mesh_recipes", {})
    if not isinstance(mesh_recipes, dict):
        raise ValueError("mesh_recipes must map provider IDs to mesh recipe objects")
    for identity, recipe in mesh_recipes.items():
        validate("Id", identity)
        if not isinstance(recipe, dict):
            raise ValueError("Mesh recipe must be an object")
        revision = recipe.get("profile_revision")
        if not isinstance(revision, str) or revision != mesh_profiles.get(identity, revision):
            raise ValueError("Mesh recipe profile_revision must agree with mesh_profiles")
        plugin("image_to_3d", revision)
        if revision == HUNYUAN_REVISION:
            if set(recipe) != {"profile_revision"}:
                raise ValueError("Hunyuan mesh recipe accepts profile_revision only")
            continue
        if recipe.keys() - BUDGET3D_OVERRIDES - {"profile_revision"}:
            raise ValueError("Unknown mesh recipe fields")
        budget3d.recipe_graph("reference", revision, recipe)


def audio_revision(options: dict[str, Any], identity: str) -> str:
    recipe = options.get("audio_recipes", {}).get(identity, {})
    return options.get("audio_profiles", {}).get(
        identity, recipe.get("profile_revision", stable_audio.REVISION)
    )


def validate_audio_recipes(options: dict[str, Any]) -> None:
    profiles = options.get("audio_profiles", {})
    recipes = options.get("audio_recipes", {})
    if not isinstance(profiles, dict) or not isinstance(recipes, dict):
        raise ValueError("Audio profiles and recipes must map provider IDs to their settings")
    for identity, revision in profiles.items():
        validate("Id", identity)
        if not isinstance(revision, str):
            raise ValueError("Audio profile revision must be a string")
        plugin("audio", revision)
    for identity, recipe in recipes.items():
        validate("Id", identity)
        if not isinstance(recipe, dict):
            raise ValueError("Audio recipe must be an object")
        revision = recipe.get("profile_revision")
        if not isinstance(revision, str) or revision != profiles.get(identity, revision):
            raise ValueError("Audio recipe profile_revision must agree with audio_profiles")
        plugin("audio", revision)
        checkpoints = {"music_checkpoint", "sfx_checkpoint"} & recipe.keys()
        if (
            not checkpoints
            or set(recipe)
            - {
                "profile_revision",
                "clip",
                "music_checkpoint",
                "sfx_checkpoint",
                "steps",
                "max_duration_seconds",
            }
            or any(
                not isinstance(recipe.get(k), str) or not recipe[k].strip()
                for k in checkpoints | {"clip"}
            )
        ):
            raise ValueError("Audio recipe requires an exact CLIP and at least one checkpoint")
        steps = recipe.get("steps", stable_audio.DEFAULT_STEPS)
        duration = recipe.get("max_duration_seconds", stable_audio.MAX_MUSIC_SECONDS)
        if type(steps) is not int or not 1 <= steps <= 50:
            raise ValueError("Audio recipe steps are outside supported bounds")
        if (
            type(duration) not in (int, float)
            or not 1 <= duration <= stable_audio.MAX_MUSIC_SECONDS
        ):
            raise ValueError("Audio recipe duration is outside supported bounds")


def compatible_audio_recipes(
    provider: Any, identity: str, catalog: dict[str, Any]
) -> list[dict[str, Any]]:
    revision = audio_revision(provider.options, identity)
    recipe = provider.options.get("audio_recipes", {}).get(
        identity,
        {
            "clip": stable_audio.DEFAULT_CLIP,
            "music_checkpoint": stable_audio.DEFAULT_MUSIC_CHECKPOINT,
            "sfx_checkpoint": stable_audio.DEFAULT_SFX_CHECKPOINT,
        },
    )
    steps = recipe.get("steps", stable_audio.DEFAULT_STEPS)
    adapter = plugin("audio", revision)
    accepted = []
    for mode, mode_limit in stable_audio.MODE_LIMITS.items():
        checkpoint = recipe.get(f"{mode}_checkpoint")
        if not checkpoint:
            continue
        model_ids = [checkpoint, recipe["clip"]]
        try:
            workflow = stable_audio.graph(
                checkpoint=checkpoint, clip=recipe["clip"], seconds=1, steps=steps
            )
            adapter.preflight(
                adapter.bind_graph(workflow, "Preflight", "0", {}, model_ids), catalog, model_ids
            )
        except (DomainError, KeyError, TypeError, ValueError, IndexError, AttributeError):
            continue
        accepted.append(
            dict(
                mode=mode,
                profile_revision=revision,
                model_ids=model_ids,
                steps=steps,
                max_duration_seconds=min(
                    mode_limit, recipe.get("max_duration_seconds", mode_limit)
                ),
            )
        )
    return accepted


VIDEO_RECIPE_DEFAULTS = dict(
    profile_revision=minimax_h3.REVISION,
    fl2va_model=minimax_h3.DEFAULT_FL2VA_MODEL,
    ref2va_model=minimax_h3.DEFAULT_REF2VA_MODEL,
    clip=minimax_h3.DEFAULT_CLIP,
    video_vae=minimax_h3.DEFAULT_VIDEO_VAE,
    audio_vae=minimax_h3.DEFAULT_AUDIO_VAE,
    fl2va_lora_4step=minimax_h3.DEFAULT_FL2VA_LORA_4STEP,
    fl2va_lora_8step=minimax_h3.DEFAULT_FL2VA_LORA_8STEP,
    ref2va_lora_4step=minimax_h3.DEFAULT_REF2VA_LORA_4STEP,
    width=minimax_h3.DEFAULT_WIDTH,
    height=minimax_h3.DEFAULT_HEIGHT,
    steps=minimax_h3.DEFAULT_STEPS,
    max_pixels=minimax_h3.MAX_PIXELS,
    max_duration_seconds=minimax_h3.MAX_DURATION_SECONDS,
    tiled_vae=False,
    shift_video=minimax_h3.DEFAULT_SHIFT_VIDEO,
    shift_audio=minimax_h3.DEFAULT_SHIFT_AUDIO,
)


def video_recipe(options: dict[str, Any], identity: str) -> dict[str, Any]:
    return {**VIDEO_RECIPE_DEFAULTS, **options.get("video_recipes", {}).get(identity, {})}


def validate_video_recipes(options: dict[str, Any]) -> None:
    recipes = options.get("video_recipes", {})
    if not isinstance(recipes, dict):
        raise ValueError("video_recipes must map provider IDs to recipe objects")
    for identity, overrides in recipes.items():
        validate("Id", identity)
        if not isinstance(overrides, dict) or overrides.keys() - VIDEO_RECIPE_DEFAULTS.keys():
            raise ValueError("Invalid or unknown video recipe fields")
        recipe = video_recipe(options, identity)
        if recipe["profile_revision"] != minimax_h3.REVISION:
            raise ValueError("Video recipe profile_revision must be minimax-h3-1")
        width, height, maximum = (recipe[k] for k in ("width", "height", "max_pixels"))
        if (
            type(width) is not int
            or type(height) is not int
            or type(maximum) is not int
            or not width * height <= maximum <= minimax_h3.MAX_PIXELS
        ):
            raise ValueError("Video recipe dimensions exceed the supported pixel limit")
        duration = recipe["max_duration_seconds"]
        if (
            type(duration) not in (int, float)
            or not 1 <= duration <= minimax_h3.MAX_DURATION_SECONDS
            or type(recipe["tiled_vae"]) is not bool
        ):
            raise ValueError("Invalid video recipe duration or tiled_vae")
        try:
            for mode, steps in (
                ("fl2va", 4),
                ("fl2va", 8),
                ("ref2va", 4),
                ("fl2va", recipe["steps"]),
            ):
                workflow, _ = video_workflow(recipe, mode, steps, {})
                if steps in (4, 8) and "lora" not in workflow:
                    raise ValueError("Video recipe requires an exact turbo LoRA filename")
        except DomainError as exc:
            raise ValueError(exc.message) from exc


def video_workflow(
    recipe: dict[str, Any], mode: str, steps: int, catalog: dict[str, Any], **inputs: Any
) -> tuple[dict[str, Any], list[str]]:
    width = inputs.pop("width", recipe["width"])
    height = inputs.pop("height", recipe["height"])
    frame_count = inputs.pop(
        "frame_count",
        minimax_h3.align_frame_count(
            min(minimax_h3.DEFAULT_DURATION_SECONDS, recipe["max_duration_seconds"])
        ),
    )
    unet = recipe[f"{mode}_model"]
    lora = recipe.get(f"{mode}_lora_{steps}step") if type(steps) is int else None
    needs_tiled = bool(
        recipe["tiled_vae"] or minimax_h3.requires_tiled_vae(width, height, frame_count)
    )
    graph = minimax_h3.graph(
        mode=mode,
        unet=unet,
        clip=recipe["clip"],
        video_vae=recipe["video_vae"],
        audio_vae=recipe["audio_vae"],
        lora=lora,
        width=width,
        height=height,
        frame_count=frame_count,
        steps=steps,
        shift_video=recipe["shift_video"],
        shift_audio=recipe["shift_audio"],
        video_vae_dtype="bf16" if supports_bf16_vae(catalog) else None,
        tiled_vae=needs_tiled,
        **inputs,
    )
    return graph, [unet, recipe["clip"], recipe["video_vae"], recipe["audio_vae"]] + (
        [lora] if lora else []
    )


def compatible_video_recipes(
    provider: Any, identity: str, catalog: dict[str, Any]
) -> list[dict[str, Any]]:
    recipe = video_recipe(provider.options, identity)
    accepted = []
    for mode, candidates in (("fl2va", (recipe["steps"], 4, 8)), ("ref2va", (4,))):
        for steps in dict.fromkeys(candidates):
            try:
                graph, model_ids = video_workflow(
                    recipe,
                    mode,
                    steps,
                    catalog,
                    **({"reference_artifact_ids": ["reference"]} if mode == "ref2va" else {}),
                )
                references = {"reference": "artifact:reference"} if mode == "ref2va" else {}
                minimax_h3.preflight(
                    minimax_h3.bind(graph, "Preflight", "0", references, model_ids),
                    catalog,
                    model_ids,
                )
            except (DomainError, KeyError, TypeError, ValueError, IndexError, AttributeError):
                continue
            accepted.append({**recipe, "mode": mode, "steps": steps, "model_ids": model_ids})
    return accepted


def bounded_get(provider: Any, service: dict[str, Any], path: str) -> dict[str, Any]:
    # Stream to bound both transfer and JSON allocation. Never follow a redirect.
    deadline = time.monotonic() + 3
    with provider.client.stream(
        "GET", endpoint(service["endpoint"]) + path, timeout=3, follow_redirects=False
    ) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes():
            if time.monotonic() > deadline or len(data) + len(chunk) > MAX_CATALOG_BYTES:
                raise DomainError("capability_unavailable", 501, UNAVAILABLE, safe_for_model=True)
            data.extend(chunk)
    import json

    body = json.loads(data)
    if not isinstance(body, dict):
        raise DomainError("capability_unavailable", 501, UNAVAILABLE, safe_for_model=True)
    return body


def workflow(
    recipe: dict[str, Any],
    reference: str | Sequence[str] | None = None,
    *,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    revision = recipe["profile_revision"]
    refs = [reference] if isinstance(reference, str) else list(reference or ())
    if revision != "comfy-image-1":
        if not refs:
            raise DomainError(
                "capability_unavailable", 501, SD_TURBO_REFERENCE, safe_for_model=True
            )
        if len(refs) > 1:
            raise DomainError("invalid_request", message=SD_TURBO_REFERENCE, safe_for_model=True)
        return (sd_turbo if revision == sd_turbo.REVISION else sd_turbo_fp16).graph(refs[0])
    width = recipe.get("width", 512)
    height = recipe.get("height", 512)
    return klein.graph(
        unet=recipe["unet"],
        clip=recipe["clip"],
        vae=recipe["vae"],
        width=width,
        height=height,
        steps=recipe.get("steps", 4),
        reference_artifact_ids=refs,
        tiled_vae=bool(
            width * height > TILE_THRESHOLD_PIXELS
            and catalog
            and "VAEDecodeTiled" in catalog
            and "VAEEncodeTiled" in catalog
        ),
        vae_dtype="bf16" if supports_bf16_vae(catalog) else None,
        reference_megapixels=min(
            1.0,
            max(0.01, round(recipe.get("max_pixels", TILE_THRESHOLD_PIXELS) / 1_000_000, 4)),
        ),
    )


def models(recipe: dict[str, Any]) -> list[str]:
    return (
        [recipe[k] for k in ("unet", "clip", "vae")]
        if recipe["profile_revision"] == "comfy-image-1"
        else [recipe["checkpoint"]]
    )


def compatible_recipes(
    provider: Any, identity: str, catalog: dict[str, Any]
) -> list[dict[str, Any]]:
    revision = provider.options.get("image_profiles", {}).get(identity, "comfy-image-1")
    explicit = provider.options.get("image_recipes", {}).get(identity)
    if explicit:
        candidates = [dict(explicit)]
    elif revision != "comfy-image-1":
        candidates = [
            dict(
                profile_revision=revision,
                checkpoint=sd_turbo.FILENAME
                if revision == sd_turbo.REVISION
                else sd_turbo_fp16.FILENAME,
            )
        ]
    else:
        choices = []
        truncated = False
        for node, field in (
            ("UNETLoader", "unet_name"),
            ("CLIPLoader", "clip_name"),
            ("VAELoader", "vae_name"),
        ):
            spec = catalog.get(node, {}).get("input", {}).get("required", {}).get(field, [[]])
            values = (
                spec[0] if isinstance(spec, list) and spec and isinstance(spec[0], list) else []
            )
            names = sorted({v for v in values if isinstance(v, str)})
            truncated = truncated or len(names) > 32
            choices.append(names[:32])
        if all(choices) and (truncated or math.prod(map(len, choices)) > 256):
            raise DomainError("capability_unavailable", 501, AMBIGUOUS, safe_for_model=True)
        candidates = [
            dict(profile_revision=revision, unet=u, clip=c, vae=v)
            for u, c, v in itertools.islice(itertools.product(*choices), 256)
        ]
    accepted = []
    for recipe in candidates:
        try:
            capability = plugin("image", revision)
            graph = workflow(
                recipe, "reference" if revision != "comfy-image-1" else None, catalog=catalog
            )
            bound = capability.bind_graph(
                graph,
                "Preflight",
                "0",
                {"reference": "artifact:reference"} if revision != "comfy-image-1" else {},
                models(recipe),
            )
            capability.preflight(bound, catalog, models(recipe))
            accepted.append(recipe)
        except (DomainError, KeyError, TypeError, ValueError, IndexError, AttributeError):
            continue
    return accepted


class Preparation:
    capability = "image"
    operation = "providers.prepare_image"
    request_schema = "ImageGenerationPrepare"
    result_schema = "ImageGenerationPrepared"
    permission = "prepareImageGeneration"

    def __init__(self, services: Any, provider: Any):
        self.services, self.provider = services, provider

    def replayable(self, caller: Caller, body: dict[str, Any]) -> dict[str, Any] | None:
        project_id = body.get("project_id") or caller.project_id
        if project_id is None:
            raise DomainError("invalid_request")
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            return replay(
                tx,
                self.operation,
                body["operation_id"],
                binding(caller, {**body, "project_id": project_id}),
            )

    def prepare(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        validate(self.request_schema, body)
        project_id = body.get("project_id") or caller.project_id
        if project_id is None:
            raise DomainError("invalid_request")
        body = {**body, "project_id": project_id}
        immutable = binding(caller, body)
        active(self.services, caller, self.permission, project_id)
        previous = self.replayable(caller, body)
        if previous:
            return {**previous, "replayed": True}, True
        store = self.services.registry.project_store(body["project_id"])
        if self.capability == "image" and (len(body["seed"]) > 16 or int(body["seed"]) > 2**53 - 1):
            raise DomainError(
                "invalid_request",
                message="An exact JSON-safe decimal seed is required",
                safe_for_model=True,
            )
        with store.transaction(write=False) as tx:
            work = admitted(self.services, tx, caller)
            if (
                "generation_task_id" in body
                and body["generation_task_id"] != work["generation_task_id"]
            ):
                raise DomainError(
                    "scope_denied",
                    403,
                    "generation_task_id does not match the active work; omit generation_task_id to use the active task",
                    safe_for_model=True,
                )
            body = {
                **body,
                "generation_task_id": body.get("generation_task_id") or work["generation_task_id"],
            }
            context = _request_context(tx, caller, work) or {}
            if (
                self.capability == "image_to_3d"
                and context.get("execution_scope") == "planning_only"
            ):
                raise DomainError(
                    "scope_denied",
                    403,
                    "Generation is unavailable while execution_scope is planning_only",
                    safe_for_model=True,
                )
            explicit_target = body.get("target_id")
            policy = tx.get("providers.tasks", work["generation_task_id"], required=False)
            bound_explicit_target = bool(
                explicit_target
                and (
                    tx.get("items", explicit_target, required=False) is not None
                    or (policy and explicit_target in policy["targets"])
                    or explicit_target == work["generation_task_id"]
                    or (
                        (context.get("request_target") or {}).get("kind") == "new_item"
                        and explicit_target
                        == (context.get("request_target") or {}).get("intent_id")
                    )
                )
            )
        if self.capability == "audio":
            stable_audio.resolve_audio_verifier(self.services.settings.rendering)
        elif self.capability == "video":
            minimax_h3.resolve_video_verifier(self.services.settings.rendering)
        provider = self.provider.provider(body.get("provider_id"), self.capability)
        try:
            catalog = bounded_get(self.provider, provider, "/object_info")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise DomainError(
                "capability_unavailable",
                501,
                "Provider catalogue could not be read",
                safe_for_model=True,
            ) from exc
        graph, model_ids, references, revision, metadata = self.recipe(body, provider, catalog)
        digest = sha(
            (
                body["operation_id"]
                if self.capability == "image"
                else self.capability + ":" + body["operation_id"]
            ).encode()
        )[:24]
        target = context.get("request_target") or {}
        default_target = (
            target["intent_id"]
            if target.get("kind") == "new_item"
            else target["version"]["item_id"]
            if target.get("kind") == "item"
            else body["generation_task_id"]
        )
        target_id = (
            body["target_id"]
            if (body.get("target_slot") is not None or bound_explicit_target)
            and body.get("target_id")
            else default_target
        )
        request = dict(
            schema_version="1.0",
            job_id="generation-" + digest,
            project_id=body["project_id"],
            generation_task_id=body["generation_task_id"],
            target_id=target_id,
            provider_id=provider["provider_id"],
            capability=self.capability,
            workflow_artifact_id="workflow-" + digest,
            prompt_artifact_id="prompt-" + digest,
            input_artifact_ids=references,
            seed=body["seed"],
            model_ids=model_ids,
            settings={"target_slot": body["target_slot"]} if body.get("target_slot") else {},
        )
        validate("GenerationSubmit", request)
        resolve_target(self.services, caller, request)
        artifacts = self.services.extension("artifacts")
        promoted = [
            artifacts.promote(
                body["project_id"],
                request[field],
                data,
                kind="document",
                purpose="metadata",
                media_type=media,
            )
            for field, data, media in (
                ("workflow_artifact_id", canonical(graph), "application/json"),
                ("prompt_artifact_id", body["prompt"].encode(), "text/plain; charset=utf-8"),
            )
        ]
        result = dict(
            schema_version="1.0",
            operation_id=body["operation_id"],
            project_id=body["project_id"],
            replayed=False,
            provider_id=provider["provider_id"],
            profile_revision=revision,
            model_ids=model_ids,
            workflow_artifact_id=request["workflow_artifact_id"],
            prompt_artifact_id=request["prompt_artifact_id"],
            submit_request=request,
            **metadata,
        )
        if self.capability == "image_to_3d":
            result["input_artifact_ids"] = references
        validate(self.result_schema, result)
        with store.transaction() as tx:

            def effect():
                admitted(self.services, tx, caller)
                for artifact in promoted:
                    artifacts.put(tx, artifact)
                tx.put("providers.prepared", result["workflow_artifact_id"], result)
                return result

            receipt, repeated = tx.operation(
                self.operation, body["operation_id"], immutable, effect
            )
            return {**receipt, "replayed": repeated}, repeated

    def recipe(
        self, body: dict[str, Any], provider: dict[str, Any], catalog: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], list[str], str, dict[str, Any]]:
        try:
            recipes = compatible_recipes(self.provider, provider["provider_id"], catalog)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            raise DomainError(
                "capability_unavailable", 501, UNAVAILABLE, safe_for_model=True
            ) from exc
        if not recipes:
            raise DomainError("capability_unavailable", 501, UNAVAILABLE, safe_for_model=True)
        if len(recipes) > 1:
            raise DomainError("capability_unavailable", 501, AMBIGUOUS, safe_for_model=True)
        recipe = {**recipes[0], **{k: body[k] for k in ("width", "height", "steps") if k in body}}
        revision = recipe["profile_revision"]
        width, height = recipe.get("width", 512), recipe.get("height", 512)
        if revision == "comfy-image-1" and (
            width % 16 != 0
            or height % 16 != 0
            or not (64 <= width <= 4096 and 64 <= height <= 4096)
        ):
            raise DomainError(
                "invalid_request",
                message="Image width and height must be multiples of 16 between 64 and 4096",
                safe_for_model=True,
            )
        max_pixels = recipe.get(
            "max_pixels", 1_048_576 if revision == "comfy-image-1" else 512 * 512
        )
        if width * height > max_pixels:
            raise DomainError(
                "invalid_request",
                message="Requested image dimensions exceed the configured provider pixel limit",
                safe_for_model=True,
            )
        if revision != "comfy-image-1" and any(
            recipe.get(k, v) != v for k, v in (("width", 512), ("height", 512), ("steps", 4))
        ):
            raise DomainError(
                "invalid_request",
                message="SD-Turbo image recipes require 512x512 and four steps",
                safe_for_model=True,
            )
        references = list(body.get("reference_artifact_ids", []))
        if len(set(references)) != len(references):
            raise DomainError(
                "invalid_request", message="Duplicate conditioning image", safe_for_model=True
            )
        if revision != "comfy-image-1" and len(references) > 1:
            raise DomainError("invalid_request", message=SD_TURBO_REFERENCE, safe_for_model=True)
        if revision == "comfy-image-1" and len(references) > klein.MAX_REFERENCE_IMAGES:
            raise DomainError(
                "invalid_request", message=klein.REFERENCE_LIMIT_MESSAGE, safe_for_model=True
            )
        capability = plugin("image", revision)
        for identity in references:
            artifact, _ = self.provider.image_bytes(body["project_id"], identity)
            if revision != "comfy-image-1" and artifact.get("dimensions") != {
                "width": 512,
                "height": 512,
            }:
                raise DomainError(
                    "invalid_request", message=SD_TURBO_REFERENCE, safe_for_model=True
                )
        graph = workflow(recipe, references, catalog=catalog)
        model_ids = models(recipe)
        bound = capability.bind_graph(
            graph, body["prompt"], body["seed"], {k: "artifact:" + k for k in references}, model_ids
        )
        capability.preflight(bound, catalog, model_ids)
        return (
            graph,
            model_ids,
            references,
            revision,
            dict(
                dimensions={"width": width, "height": height},
                input_artifact_ids=references,
            ),
        )


class AudioPreparation(Preparation):
    capability = "audio"
    operation = "providers.prepare_audio"
    request_schema = "AudioGenerationPrepare"
    result_schema = "AudioGenerationPrepared"
    permission = "prepareAudioGeneration"

    def recipe(
        self, body: dict[str, Any], provider: dict[str, Any], catalog: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], list[str], str, dict[str, Any]]:
        mode = body.get("mode", "music")
        recipes = compatible_audio_recipes(self.provider, provider["provider_id"], catalog)
        recipe = next((candidate for candidate in recipes if candidate["mode"] == mode), None)
        if recipe is None:
            raise DomainError(
                "capability_unavailable", 501, stable_audio.UNAVAILABLE, safe_for_model=True
            )
        seconds = body["duration_seconds"]
        if not 1 <= seconds <= recipe["max_duration_seconds"]:
            raise DomainError(
                "invalid_request", message=stable_audio.INVALID_DURATION, safe_for_model=True
            )
        checkpoint, clip = model_ids = recipe["model_ids"]
        revision = recipe["profile_revision"]
        adapter = plugin("audio", revision)
        workflow = stable_audio.graph(
            checkpoint=checkpoint,
            clip=clip,
            seconds=seconds,
            steps=body.get("steps", recipe["steps"]),
        )
        adapter.preflight(
            adapter.bind_graph(workflow, body["prompt"], body["seed"], {}, model_ids),
            catalog,
            model_ids,
        )
        return workflow, model_ids, [], revision, dict(mode=mode, duration_seconds=seconds)


class VideoPreparation(Preparation):
    capability = "video"
    operation = "providers.prepare_video"
    request_schema = "VideoGenerationPrepare"
    result_schema = "VideoGenerationPrepared"
    permission = "prepareVideoGeneration"

    def recipe(
        self, body: dict[str, Any], provider: dict[str, Any], catalog: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], list[str], str, dict[str, Any]]:
        mode = body.get("mode", "fl2va")
        recipe = video_recipe(self.provider.options, provider["provider_id"])
        width, height = (body.get(k, recipe[k]) for k in ("width", "height"))
        if width * height > recipe["max_pixels"]:
            raise DomainError(
                "invalid_request",
                message="Requested video dimensions exceed the provider pixel limit",
                safe_for_model=True,
            )
        seconds = body.get(
            "duration_seconds",
            min(minimax_h3.DEFAULT_DURATION_SECONDS, recipe["max_duration_seconds"]),
        )
        if not 1 <= seconds <= recipe["max_duration_seconds"]:
            raise DomainError(
                "invalid_request",
                message="Video duration is outside supported bounds",
                safe_for_model=True,
            )
        count = minimax_h3.align_frame_count(seconds)
        steps = body.get("steps", recipe["steps"] if mode == "fl2va" else 4)
        if "steps" not in body and mode == "fl2va":
            # Use the configured default when installed. If only one turbo variant is
            # installed, preparation must agree with the available default from discovery.
            observed = compatible_video_recipes(self.provider, provider["provider_id"], catalog)
            steps = next(
                (candidate["steps"] for candidate in observed if candidate["mode"] == mode), steps
            )
        if not (steps == 4 or (mode == "fl2va" and (steps in (8, recipe["steps"]) or steps >= 15))):
            raise DomainError(
                "invalid_request",
                message="Unsupported sampling steps for MiniMax H3 mode",
                safe_for_model=True,
            )
        incompatible = (
            {"reference_artifact_ids", "ref_image_size"}
            if mode == "fl2va"
            else {"last_frame_artifact_id", "guide_frames"}
        )
        if incompatible & body.keys():
            raise DomainError(
                "invalid_request",
                message="Invalid MiniMax H3 conditioning artefacts",
                safe_for_model=True,
            )
        graph, model_ids = video_workflow(
            recipe,
            mode,
            steps,
            catalog,
            width=width,
            height=height,
            frame_count=count,
            **{
                k: body[k]
                for k in (
                    "first_frame_artifact_id",
                    "last_frame_artifact_id",
                    "guide_frames",
                    "reference_artifact_ids",
                    "ref_image_size",
                )
                if k in body
            },
        )
        references = list(
            dict.fromkeys(
                node["inputs"]["image"].removeprefix("artifact:")
                for node in graph.values()
                if node["class_type"] == "LoadImage"
            )
        )
        bound = minimax_h3.bind(
            graph, body["prompt"], body["seed"], {k: "artifact:" + k for k in references}, model_ids
        )
        minimax_h3.preflight(bound, catalog, model_ids)
        for identity in references:
            self.provider.image_bytes(body["project_id"], identity)
        return (
            graph,
            model_ids,
            references,
            minimax_h3.REVISION,
            dict(
                mode=mode,
                dimensions={"width": width, "height": height},
                duration_seconds=round(count / minimax_h3.FPS, 6),
                frame_count=count,
                fps={"numerator": minimax_h3.FPS, "denominator": 1},
                input_artifact_ids=references,
            ),
        )


class MeshPreparation(Preparation):
    capability = "image_to_3d"
    operation = "providers.prepare_mesh"
    request_schema = "MeshGenerationPrepare"
    result_schema = "MeshGenerationPrepared"
    permission = "prepareMeshGeneration"

    def recipe(
        self, body: dict[str, Any], provider: dict[str, Any], catalog: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], list[str], str, dict[str, Any]]:
        identity = provider["provider_id"]
        options = self.provider.options
        revision = mesh_revision(options, identity)
        adapter = plugin(self.capability, revision)
        reference = body["reference_artifact_id"]
        overrides = {key: body[key] for key in BUDGET3D_OVERRIDES & body.keys()}
        if revision == HUNYUAN_REVISION:
            if overrides:
                raise DomainError(
                    "invalid_request",
                    message="Hunyuan3D uses a pinned recipe; omit mode, budget, steps, num_gaussians and mesh_resolution",
                    safe_for_model=True,
                )
            workflow = hunyuan.graph(reference)
            model_ids = [hunyuan.FILENAME]
        else:
            configured = options.get("mesh_recipes", {}).get(identity, {})
            # JSON Schema accepts integral numbers such as 5.0 as integers.
            for key in {"steps", "num_gaussians", "mesh_resolution"} & overrides.keys():
                overrides[key] = int(overrides[key])
            workflow = budget3d.recipe_graph(reference, revision, {**configured, **overrides})
            model_ids = budget3d.models(turbo=revision == budget3d.TURBO_REVISION)
        self.provider.image_bytes(body["project_id"], reference)
        adapter.preflight(
            adapter.bind_graph(
                workflow,
                body["prompt"],
                body["seed"],
                {reference: "artifact:" + reference},
                model_ids,
            ),
            catalog,
            model_ids,
        )
        return workflow, model_ids, [reference], revision, {}
