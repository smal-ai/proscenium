"""Local image input preparation and bounded read-only recipe discovery. No inference."""

import itertools
import math
import time
from typing import Any

import httpx

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.providers import klein, sd_turbo, sd_turbo_fp16
from proscenium.providers.comfy import endpoint
from proscenium.providers.plugins import plugin
from proscenium.providers.targets import _request_context, resolve_target
from proscenium.sources.common import active, admitted, binding, replay, sha

AMBIGUOUS = "Multiple compatible image model tuples exist; configure provider_options.image_recipes"
UNAVAILABLE = "Image recipe nodes or models are unavailable"
MAX_CATALOG_BYTES = 2 * 1024**2


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
                raise DomainError("capability_unavailable", 501, UNAVAILABLE)
            data.extend(chunk)
    import json

    body = json.loads(data)
    if not isinstance(body, dict):
        raise DomainError("capability_unavailable", 501, UNAVAILABLE)
    return body


def workflow(
    recipe: dict[str, Any], reference: str | None = None, *, catalog: dict[str, Any] | None = None
) -> dict[str, Any]:
    revision = recipe["profile_revision"]
    if revision != "comfy-image-1":
        if reference is None:
            raise DomainError(
                "capability_unavailable",
                501,
                "Selected image profile requires one exact 512x512 reference",
            )
        return (sd_turbo if revision == sd_turbo.REVISION else sd_turbo_fp16).graph(reference)
    return klein.graph(
        unet=recipe["unet"],
        clip=recipe["clip"],
        vae=recipe["vae"],
        width=recipe.get("width", 512),
        height=recipe.get("height", 512),
        steps=recipe.get("steps", 4),
        reference_artifact_id=reference,
        tiled_vae=bool(catalog and "VAEDecodeTiled" in catalog and "VAEEncodeTiled" in catalog),
        reference_megapixels=min(
            1.0, max(0.01, round(recipe.get("max_pixels", 1_048_576) / 1_000_000, 4))
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
            raise DomainError("capability_unavailable", 501, AMBIGUOUS)
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
    def __init__(self, services: Any, provider: Any):
        self.services, self.provider = services, provider

    def replayable(self, caller: Caller, body: dict[str, Any]) -> dict[str, Any] | None:
        project_id = body.get("project_id") or caller.project_id
        if project_id is None:
            raise DomainError("invalid_request")
        with self.services.registry.project_store(project_id).transaction(write=False) as tx:
            return replay(
                tx,
                "providers.prepare_image",
                body["operation_id"],
                binding(caller, {**body, "project_id": project_id}),
            )

    def prepare(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        validate("ImageGenerationPrepare", body)
        project_id = body.get("project_id") or caller.project_id
        if project_id is None:
            raise DomainError("invalid_request")
        body = {**body, "project_id": project_id}
        immutable = binding(caller, body)
        active(self.services, caller, "prepareImageGeneration", project_id)
        previous = self.replayable(caller, body)
        if previous:
            return {**previous, "replayed": True}, True
        store = self.services.registry.project_store(body["project_id"])
        if len(body["seed"]) > 16 or int(body["seed"]) > 2**53 - 1:
            raise DomainError(
                "invalid_request", message="An exact JSON-safe decimal seed is required"
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
                )
            body = {
                **body,
                "generation_task_id": body.get("generation_task_id") or work["generation_task_id"],
            }
            context = _request_context(tx, caller, work) or {}
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
        provider = self.provider.provider(body.get("provider_id"), "image")
        try:
            catalog = bounded_get(self.provider, provider, "/object_info")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise DomainError(
                "capability_unavailable", 501, "Provider catalogue could not be read"
            ) from exc
        try:
            recipes = compatible_recipes(self.provider, provider["provider_id"], catalog)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            raise DomainError("capability_unavailable", 501, UNAVAILABLE) from exc
        if len(recipes) != 1:
            raise DomainError("capability_unavailable", 501, AMBIGUOUS if recipes else UNAVAILABLE)
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
            )
        max_pixels = recipe.get(
            "max_pixels", 1_048_576 if revision == "comfy-image-1" else 512 * 512
        )
        if width * height > max_pixels:
            raise DomainError(
                "invalid_request",
                message="Requested image dimensions exceed the configured provider pixel limit",
            )
        if revision != "comfy-image-1" and any(
            recipe.get(k, v) != v for k, v in (("width", 512), ("height", 512), ("steps", 4))
        ):
            raise DomainError(
                "invalid_request", message="SD-Turbo image recipes require 512x512 and four steps"
            )
        references = body.get("reference_artifact_ids", [])
        if len(references) > 1:
            raise DomainError(
                "invalid_request", message="Image preparation accepts at most one reference"
            )
        capability = plugin("image", revision)
        for identity in references:
            artifact, _ = self.provider.image_bytes(body["project_id"], identity)
            if revision != "comfy-image-1" and artifact.get("dimensions") != {
                "width": 512,
                "height": 512,
            }:
                raise DomainError(
                    "invalid_request",
                    message="Selected image profile requires one exact 512x512 reference",
                )
        graph = workflow(recipe, references[0] if references else None, catalog=catalog)
        model_ids = models(recipe)
        bound = capability.bind_graph(
            graph, body["prompt"], body["seed"], {k: "artifact:" + k for k in references}, model_ids
        )
        capability.preflight(bound, catalog, model_ids)
        digest = sha(body["operation_id"].encode())[:24]
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
            capability="image",
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
            dimensions={"width": recipe.get("width", 512), "height": recipe.get("height", 512)},
            workflow_artifact_id=request["workflow_artifact_id"],
            prompt_artifact_id=request["prompt_artifact_id"],
            input_artifact_ids=references,
            submit_request=request,
        )
        validate("ImageGenerationPrepared", result)
        with store.transaction() as tx:

            def effect():
                admitted(self.services, tx, caller)
                for artifact in promoted:
                    artifacts.put(tx, artifact)
                tx.put("providers.prepared", result["workflow_artifact_id"], result)
                return result

            receipt, repeated = tx.operation(
                "providers.prepare_image", body["operation_id"], immutable, effect
            )
            return {**receipt, "replayed": repeated}, repeated
