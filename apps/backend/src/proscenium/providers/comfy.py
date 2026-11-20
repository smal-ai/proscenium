"""Endpoint-bound ComfyUI transport. No lifecycle management or inference retries.

Preflight adapted from the pinned SMAL check.py, substantially changed for host admission.
See tools/comfy/NOTICE for upstream attribution.
"""

import copy
import math
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from proscenium.api.errors import DomainError


def endpoint(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise DomainError("invalid_request", message="A credential-free service URL is required")
    return value.rstrip("/")


def preflight(graph: dict[str, Any], catalog: dict[str, Any], models: list[str]) -> None:
    if not graph or "nodes" in graph:
        raise DomainError("invalid_request", message="Expected a non-empty API graph")
    schemas: dict[str, Any] = {}
    for identity, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            raise DomainError("invalid_request", message="Invalid graph node")
        kind = node.get("class_type")
        if kind not in catalog:
            raise DomainError("capability_unavailable", 501, "Graph node is not installed")
        schemas[identity] = catalog[kind]
    selected_models: set[str] = set()
    for identity, schema in schemas.items():
        inputs = graph[identity]["inputs"]
        required = schema.get("input", {}).get("required", {})
        optional = schema.get("input", {}).get("optional", {})
        if set(required) - set(inputs):
            raise DomainError("invalid_request", message="Missing graph input")
        for name, value in inputs.items():
            spec = required.get(name, optional.get(name))
            if not spec:
                raise DomainError("invalid_request", message="Unknown graph input")
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                source, slot = value
                outputs = schemas.get(source, {}).get("output", [])
                if type(slot) is not int or not 0 <= slot < len(outputs):
                    raise DomainError("invalid_request", message="Invalid graph link")
                if (
                    isinstance(spec[0], str)
                    and outputs[slot] != "*"
                    and not (set(spec[0].split(",")) & {outputs[slot], "*"})
                ):
                    raise DomainError("invalid_request", message="Graph link type mismatch")
            elif isinstance(spec[0], list) and value not in spec[0]:
                # LoadImage choices are populated only after the admitted upload.
                if not (graph[identity]["class_type"] == "LoadImage" and name == "image"):
                    raise DomainError("capability_unavailable", 501, "Model/choice is unavailable")
            elif isinstance(spec[0], str):
                kind = spec[0]
                valid = (
                    (kind == "INT" and type(value) is int)
                    or (kind == "FLOAT" and type(value) in {int, float} and math.isfinite(value))
                    or (kind == "BOOLEAN" and type(value) is bool)
                    or (kind == "STRING" and isinstance(value, str))
                    or (
                        kind == "COMBO"
                        and len(spec) > 1
                        and isinstance(spec[1], dict)
                        and value in spec[1].get("options", [])
                    )
                )
                if not valid:
                    raise DomainError("invalid_request", message="Invalid graph input type")
                bounds = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                if kind in {"INT", "FLOAT"} and (
                    value < bounds.get("min", value) or value > bounds.get("max", value)
                ):
                    raise DomainError("invalid_request", message="Graph input outside node bounds")
            if name in {
                "unet_name",
                "ckpt_name",
                "clip_name",
                "vae_name",
                "lora_name",
                "bg_removal_name",
            }:
                selected_models.add(value)
    if set(models) != selected_models or not any(s.get("output_node") for s in schemas.values()):
        raise DomainError("invalid_request", message="Model manifest/output graph mismatch")


def bind_graph(
    graph: dict[str, Any], prompt: str, seed: str | None, references: dict[str, str]
) -> dict[str, Any]:
    """Explicit tokens only. References must feed a real reference-latent conditioning path."""
    graph = copy.deepcopy(graph)
    if seed is None or not 0 <= int(seed) <= 2**53 - 1:
        raise DomainError("invalid_request", message="An exact JSON-safe decimal seed is required")
    used: set[str] = set()
    has_prompt = any(
        n.get("class_type") == "CLIPTextEncode" and n.get("inputs", {}).get("text") == prompt
        for n in graph.values()
        if isinstance(n, dict)
    )
    has_seed = False
    # Follow the graph used by an actual saved output, not an unused conditioning branch.
    reachable: set[str] = set()

    def visit(identity: str) -> None:
        if identity in reachable:
            return
        reachable.add(identity)
        for value in graph[identity]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and value[0] in graph:
                visit(value[0])

    for identity, node in graph.items():
        if node["class_type"] == "SaveImage":
            visit(identity)

    def output_zero(link: Any) -> str | None:
        if (
            isinstance(link, list)
            and len(link) == 2
            and isinstance(link[0], str)
            and type(link[1]) is int
            and link[1] == 0
        ):
            return link[0]
        return None

    # Each ReferenceLatent appends to the conditioning inherited from its predecessor.
    active_positive: set[str] = set()
    for identity in reachable:
        guider = graph[identity]
        if guider["class_type"] != "CFGGuider":
            continue
        link = guider["inputs"].get("positive")
        chain: set[str] = set()
        while (source := output_zero(link)) is not None:
            node = graph.get(source)
            if not node or node["class_type"] != "ReferenceLatent":
                break
            if source in chain:
                raise DomainError("invalid_request", message="Invalid graph link")
            if source in active_positive:
                break
            chain.add(source)
            link = node["inputs"].get("conditioning")
        active_positive.update(chain)

    for identity, node in graph.items():
        for key, value in node["inputs"].items():
            if key in {"seed", "noise_seed"}:
                if value not in {"$seed", int(seed)}:
                    raise DomainError("invalid_request", message="Graph/request seed mismatch")
                has_seed = True
            if value == "$prompt":
                if node["class_type"] != "CLIPTextEncode" or key != "text":
                    raise DomainError("invalid_request")
                node["inputs"][key] = prompt
                has_prompt = True
            elif value == "$seed":
                if seed is None or key not in {"noise_seed", "seed"}:
                    raise DomainError("invalid_request")
                node["inputs"][key] = int(seed)
            elif isinstance(value, str) and value.startswith("artifact:"):
                artifact_id = value.removeprefix("artifact:")
                if (
                    node["class_type"] != "LoadImage"
                    or key != "image"
                    or artifact_id not in references
                ):
                    raise DomainError("invalid_request", message="Unbound conditioning image")
                if artifact_id in used:
                    raise DomainError("invalid_request", message="Duplicate conditioning image")
                node["inputs"][key] = references[artifact_id]
                # Retain admitted unscaled graphs alongside scaled/tiled reference paths.
                pixels = {identity} | {
                    k
                    for k, n in graph.items()
                    if n["class_type"] == "ImageScaleToTotalPixels"
                    and n["inputs"].get("image") == [identity, 0]
                }
                encodes = {
                    k
                    for k, n in graph.items()
                    if n["class_type"] in {"VAEEncode", "VAEEncodeTiled"}
                    and any(n["inputs"].get("pixels") == [source, 0] for source in pixels)
                }
                if not any(
                    output_zero(graph[k]["inputs"].get("latent")) in encodes
                    for k in active_positive
                ):
                    raise DomainError("invalid_request", message="Reference is not conditioning")
                used.add(artifact_id)
    if used != set(references) or not has_prompt or not has_seed:
        raise DomainError("invalid_request", message="Unused input image or missing prompt binding")
    if references and not any(
        n["class_type"] == "CLIPLoader" and n["inputs"].get("type") == "flux2"
        for n in graph.values()
    ):
        raise DomainError("capability_unavailable", 501, "Reference workflow requires FLUX.2")
    return graph


class ComfyClient:
    def __init__(self, base: str, client: httpx.Client):
        self.base, self.client = endpoint(base), client

    def get(self, path: str) -> Any:
        response = self.client.get(self.base + path)
        response.raise_for_status()
        return response.json()

    def capabilities(self) -> dict[str, Any]:
        return dict(self.get("/object_info"))

    def busy(self, minimum_free_bytes: int = 0) -> bool:
        queue = self.get("/queue")
        if queue.get("queue_running") or queue.get("queue_pending"):
            return True
        stats = self.get("/system_stats")
        devices = stats.get("devices", [])
        if minimum_free_bytes and not devices:
            raise DomainError("capability_unavailable", 501, "GPU memory telemetry unavailable")
        return bool(
            minimum_free_bytes and all(d.get("vram_free", 0) < minimum_free_bytes for d in devices)
        )

    def upload(self, data: bytes, digest: str) -> str:
        response = self.client.post(
            self.base + "/upload/image",
            files={"image": (digest + ".png", data, "image/png")},
            data={"type": "input", "overwrite": "false"},
        )
        response.raise_for_status()
        body = response.json()
        return "/".join(filter(None, (body.get("subfolder"), body["name"])))

    def submit(self, graph: dict[str, Any], client_id: str) -> str:
        response = self.client.post(
            self.base + "/prompt", json={"prompt": graph, "client_id": client_id}
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body.get("prompt_id"), str):
            raise DomainError("provider_unavailable", 503, "Provider did not acknowledge a prompt")
        return str(body["prompt_id"])

    def status(self, prompt_id: str) -> dict[str, Any] | None:
        return self.get("/history/" + quote(prompt_id, safe="")).get(prompt_id)

    def reconcile(self, client_id: str) -> str | None:
        queue = self.get("/queue")
        entries = queue.get("queue_running", []) + queue.get("queue_pending", [])
        entries += [r.get("prompt", []) for r in self.get("/history").values()]
        matches = {r[1] for r in entries if len(r) > 3 and r[3].get("client_id") == client_id}
        if len(matches) > 1:
            raise DomainError("source_conflict", 409)
        return next(iter(matches), None)

    def wait(self, prompt_id: str, *, timeout: float = 120, poll: float = 1) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.status(prompt_id)
            if result and result.get("status", {}).get("completed"):
                return result
            time.sleep(poll)
        raise TimeoutError("Known provider prompt remains pending")

    def download(self, item: dict[str, Any], *, maximum_bytes: int = 256 * 1024**2) -> bytes:
        with self.client.stream(
            "GET",
            self.base + "/view",
            params={
                "filename": item["filename"],
                "subfolder": item.get("subfolder", ""),
                "type": item.get("type", "output"),
            },
        ) as response:
            response.raise_for_status()
            if int(response.headers.get("content-length", 0)) > maximum_bytes:
                raise DomainError("unsupported_media_type", 413, "Provider output is too large")
            data = bytearray()
            for block in response.iter_bytes(chunk_size=1024**2):
                if len(data) + len(block) > maximum_bytes:
                    raise DomainError("unsupported_media_type", 413, "Provider output is too large")
                data.extend(block)
            return bytes(data)
