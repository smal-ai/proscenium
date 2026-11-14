"""Pure graph binding/preflight and exact SaveGLB history inspection."""

import copy
import math
from pathlib import PurePosixPath

from .candidate import FILENAME, graph


def bind(template: dict, reference_artifact_id: str, uploaded_filename: str, seed: str) -> dict:
    """For a future admitted provider plugin, not an alternative inference route."""
    if template != graph(reference_artifact_id):
        raise ValueError("only the pinned trusted Hunyuan image-to-mesh graph is supported")
    if not seed.isascii() or not seed.isdecimal() or len(seed) > 16 or int(seed) > 2**53 - 1:
        raise ValueError("seed must be an exact JSON-safe decimal integer")
    path = PurePosixPath(uploaded_filename)
    if (
        not uploaded_filename
        or path.is_absolute()
        or any(part in {"..", "."} for part in uploaded_filename.split("/"))
        or "\\" in uploaded_filename
        or "\x00" in uploaded_filename
    ):
        raise ValueError("unsafe uploaded image filename")
    bound = copy.deepcopy(template)
    bound["56"]["inputs"]["image"] = uploaded_filename
    bound["3"]["inputs"]["seed"] = int(seed)
    return bound


def preflight(bound: dict, catalog: dict) -> None:
    """Validate actual core schemas, typed sockets, choices and primitive bounds.

    Does not insert a missing checkpoint into the catalogue or contact a service.
    A missing model therefore fails, including on the independent :8188 service.
    """
    if set(bound) != set(graph("reference")):
        raise ValueError("unexpected graph nodes")
    for identity, node in bound.items():
        schema = catalog.get(node["class_type"])
        if not schema:
            raise ValueError(f"missing node {node['class_type']}")
        required = schema["input"].get("required", {})
        specs = {**required, **schema["input"].get("optional", {})}
        inputs = node["inputs"]
        if set(required) - set(inputs) or set(inputs) - set(specs):
            raise ValueError(f"input schema mismatch for {identity}")
        for name, value in inputs.items():
            spec = specs[name]
            kind = spec[0]
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if isinstance(value, list):
                if len(value) != 2 or value[0] not in bound or type(value[1]) is not int:
                    raise ValueError("invalid socket link")
                outputs = catalog[bound[value[0]]["class_type"]]["output"]
                if not 0 <= value[1] < len(outputs) or not isinstance(kind, str):
                    raise ValueError("invalid socket index/type")
                if outputs[value[1]] not in kind.split(","):
                    raise ValueError("socket type mismatch")
            elif isinstance(kind, list):
                if value not in kind and not (
                    node["class_type"] == "LoadImage" and name == "image"
                ):
                    raise ValueError(f"unavailable choice {name}: {value}")
            elif kind == "COMBO":
                if value not in options.get("options", []):
                    raise ValueError("unavailable combo choice")
            elif kind in {"INT", "FLOAT"}:
                if type(value) not in (
                    {int} if kind == "INT" else {int, float}
                ) or not math.isfinite(value):
                    raise ValueError("invalid numeric input")
                if not options.get("min", value) <= value <= options.get("max", value):
                    raise ValueError("numeric input outside node bounds")
            elif kind != "STRING" or not isinstance(value, str):
                raise ValueError("invalid primitive input")
    if bound["54"]["inputs"]["ckpt_name"] != FILENAME:
        raise ValueError("unexpected checkpoint")


def output_files(history_entry: dict) -> list[dict]:
    """Inspect an already reconciled exact-prompt history entry, without HTTP."""
    status = history_entry.get("status", {})
    if status.get("status_str") != "success" or status.get("completed") is not True:
        raise ValueError("provider has not reported successful terminal completion")
    entries = history_entry.get("outputs", {}).get("82", {}).get("3d", [])
    if len(entries) != 1:
        raise ValueError("expected exactly one SaveGLB 3d output")
    for entry in entries:
        name, folder = entry.get("filename", ""), entry.get("subfolder", "")
        if (
            entry.get("type") != "output"
            or not name.endswith(".glb")
            or PurePosixPath(name).name != name
            or "\\" in name
            or "\x00" in name
            or PurePosixPath(folder).is_absolute()
            or "\\" in folder
            or "\x00" in folder
            or any(p in {"..", "."} for p in folder.split("/"))
        ):
            raise ValueError("unsafe or non-GLB provider output descriptor")
    return copy.deepcopy(entries)
