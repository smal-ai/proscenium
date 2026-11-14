"""Pure graph binding/preflight and exact SaveGLB history inspection."""

import math

from proscenium.providers.hunyuan import bind as bind
from proscenium.providers.hunyuan import output_files as output_files

from .candidate import FILENAME, graph


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
