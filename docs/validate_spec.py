"""Read-only checks for the specification, wire examples and reference identities.

This does not run application, browser, model, Blender or Unity acceptance tests.
Use an existing Python environment with docs/requirements-validation.txt packages.
"""

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    import yaml
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
except ImportError as exc:
    raise SystemExit(
        "Documentation validation needs jsonschema and PyYAML. Use an existing "
        "suitable environment; see docs/requirements-validation.txt."
    ) from exc

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


def read_json(path):
    return json.loads(path.read_text(), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


class UniqueLoader(yaml.SafeLoader):
    pass


def yaml_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        require(key not in result, f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_mapping)


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def pointer(document, fragment):
    if not fragment:
        return document
    require(fragment.startswith("/"), f"Expected JSON pointer: #{fragment}")
    current = document
    for part in fragment[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def resolve_ref(reference, base, documents):
    uri = urlsplit(reference)
    require(not uri.scheme and not uri.netloc, f"Unexpected remote $ref: {reference}")
    target = (base.parent / unquote(uri.path)).resolve() if uri.path else base
    require(target in documents, f"Missing reference document: {target}")
    return pointer(documents[target], unquote(uri.fragment))


def anchors(path):
    result, counts, fenced = set(), {}, False
    for line in path.read_text().splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if fenced or not match:
            continue
        text = re.sub(r"<[^>]+>", "", match[1]).lower()
        slug = re.sub(r"[^\w\- ]", "", text).replace(" ", "-")
        count = counts.get(slug, 0)
        result.add(slug if count == 0 else f"{slug}-{count}")
        counts[slug] = count + 1
    return result


def check_links(documents, pins):
    checked, external = 0, 0
    allowed_siblings = {ROOT.parent / row["repository"] for row in pins["references"]}
    for source in [ROOT / "README.md", ROOT / "AGENTS.md", *DOCS.rglob("*.md")]:
        for target in re.findall(r"\[[^\]\n]+\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)", source.read_text()):
            uri = urlsplit(target)
            if uri.scheme or uri.netloc:
                continue
            path = (source.parent / unquote(uri.path)).resolve() if uri.path else source
            if not path.is_relative_to(ROOT):
                require(any(path.is_relative_to(root) for root in allowed_siblings),
                        f"Unpinned external reference in {source}: {target}")
                external += 1
                continue
            require(path.exists(), f"Broken local link in {source.relative_to(ROOT)}: {target}")
            if uri.fragment:
                fragment = unquote(uri.fragment)
                if path.suffix == ".md":
                    require(fragment in anchors(path), f"Missing anchor in {source.relative_to(ROOT)}: {target}")
                elif path in documents and fragment.startswith("/"):
                    pointer(documents[path], fragment)
            checked += 1
    return checked, external


def check_vectors():
    data = read_json(DOCS / "acceptance/coordinate-vectors.json")
    for case in data["basis_cases"]:
        x, y, z = case["blender"]
        require(case["proxy"] == [x, z, -y], f"Proxy vector mismatch: {case['id']}")
        require(case["unity"] == [x, z, y], f"Unity vector mismatch: {case['id']}")
    for name in ("perspective", "orthographic", "shifted_perspective"):
        camera = data[name]
        world, projection = camera["camera_to_world"], camera["projection"]
        width, height = camera["dimensions"]
        for landmark in camera["landmarks"]:
            delta = [landmark["world"][i] - world[i * 4 + 3] for i in range(3)]
            local = [sum(world[i * 4 + j] * delta[i] for i in range(3)) for j in range(3)] + [1]
            clip = [sum(projection[i * 4 + j] * local[j] for j in range(4)) for i in range(4)]
            normalised = [(clip[0] / clip[3] + 1) / 2, (1 - clip[1] / clip[3]) / 2]
            pixels = [normalised[0] * width, normalised[1] * height]
            require(all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(pixels, landmark["pixel"])),
                    f"Camera landmark vector is inconsistent: {name}")
            require(all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(normalised, landmark["normalised"])),
                    f"Normalised landmark vector is inconsistent: {name}")
    for case in data["image_mapping"]:
        x, y, width, height = case["content_rect_css"]
        px, py = case["pointer_css"]
        result = [(px-x)/width, (py-y)/height] if x <= px <= x+width and y <= py <= y+height else None
        require(result == case["normalised"], f"Image vector mismatch: {case['id']}")
    for case in data["temporal_cases"]:
        fps = case["fps"]
        duration = Fraction(fps["denominator"], fps["numerator"])
        offset = case["source_frame"] - case["segment_start"]
        require(duration * offset == Fraction(*case["start_seconds"]), f"Start time mismatch: {case['id']}")
        require(duration * (offset+1) == Fraction(*case["end_seconds"]), f"End time mismatch: {case['id']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-references", action="store_true")
    args = parser.parse_args()
    documents = {path.resolve(): read_json(path) for path in DOCS.rglob("*.json")}
    for path in DOCS.rglob("*.yaml"):
        documents[path.resolve()] = yaml.load(path.read_text(), Loader=UniqueLoader)
    schema_path = DOCS / "contracts/schema.json"
    schema = documents[schema_path]
    Draft202012Validator.check_schema(schema)
    registry = Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    formats = FormatChecker()

    @formats.checks("date-time", raises=ValueError)
    def timestamp(value):
        if not isinstance(value, str):
            return True
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
            return False
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None

    refs = 0
    for path in (schema_path, DOCS / "contracts/openapi.yaml"):
        for node in walk(documents[path]):
            if "$ref" in node:
                resolve_ref(node["$ref"], path, documents)
                refs += 1
    index = documents[DOCS / "contracts/examples/index.json"]["examples"]
    require(len({row["file"] for row in index}) == len(index), "Duplicate example entry")
    for row in index:
        require(row["definition"] in schema["$defs"], f"Unknown example definition: {row}")
        validator = Draft202012Validator(
            {"$ref": f'{schema["$id"]}#/$defs/{row["definition"]}'},
            registry=registry, format_checker=formats,
        )
        payload = documents[DOCS / "contracts/examples" / row["file"]]
        errors = list(validator.iter_errors(payload))
        require((not errors) == row["valid"], f"Example {row['file']} expected valid={row['valid']}: "
                + "; ".join(error.message for error in errors[:3]))

    api_path = DOCS / "contracts/openapi.yaml"
    api = documents[api_path]
    require(api["openapi"] == "3.1.0" and api["info"]["version"] == "1.0.0", "Unexpected API version")
    operations = set()
    for route, path_item in api["paths"].items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            require(operation["operationId"] not in operations, "Duplicate operationId")
            operations.add(operation["operationId"])
            parameters = path_item.get("parameters", []) + operation.get("parameters", [])
            parameters = [resolve_ref(p["$ref"], api_path, documents) if "$ref" in p else p for p in parameters]
            actual = {p["name"] for p in parameters if p["in"] == "path" and p.get("required") is True}
            require(actual == set(re.findall(r"\{([^}]+)\}", route)), f"Path parameters mismatch: {method} {route}")
            require(bool(operation.get("responses")), f"Missing responses: {route}")

    pins = documents[DOCS / "reference-pins.json"]
    for reference in pins["references"]:
        require(bool(re.fullmatch(r"[a-f0-9]{40}", reference["commit"])), "Invalid reference commit")
        for row in reference["files"]:
            require(bool(re.fullmatch(r"[a-f0-9]{64}", row["sha256"])), "Invalid reference file hash")
            if args.check_references:
                root = Path(reference["local_hint"]).expanduser()
                content = subprocess.check_output(["git", "-C", str(root), "show", f'{reference["commit"]}:{row["path"]}'])
                require(hashlib.sha256(content).hexdigest() == row["sha256"], f"Reference bytes mismatch: {root}/{row['path']}")
    links, external = check_links(documents, pins)
    design = documents[DOCS / "design/manifest.json"]
    for row in design["files"]:
        content = (DOCS / "design" / row["path"]).read_bytes()
        require(len(content) == row["bytes"] and hashlib.sha256(content).hexdigest() == row["sha256"], f"Design reference changed: {row['path']}")
    scenarios = documents[DOCS / "acceptance/scenarios.json"]["scenarios"]
    ids = {row["id"] for row in scenarios}
    require(len(ids) == len(scenarios) and ids == {f"C{i:02d}" for i in range(1, 21)}, "Scenario catalogue has missing/duplicate IDs")
    for row in scenarios:
        require(all(row.get(key) for key in ("name", "owners", "milestones", "given", "when", "then")), f"Incomplete scenario: {row['id']}")
    traces = documents[DOCS / "acceptance/adapter-traces.json"]["traces"]
    require(len({trace["id"] for trace in traces}) == len(traces), "Duplicate adapter trace ID")
    for trace in traces:
        require(trace["steps"] and set(trace["scenarios"]).issubset(ids), f"Invalid adapter trace: {trace['id']}")
        for step in trace["steps"]:
            for key, value in step.items():
                if key == "fixture" or key.endswith("_fixture"):
                    require((DOCS / "acceptance" / value).resolve() in documents, f"Missing trace fixture: {value}")
    check_vectors()
    print(f"PASS: {len(documents)} JSON/YAML documents; {len(schema['$defs'])} schema definitions; {refs} references; {len(index)} positive/negative schema examples.")
    print(f"PASS: {len(operations)} HTTP operations; {links} local links; {external} pinned sibling-reference links; 2 design hashes; coordinate/time vectors.")
    if args.check_references:
        print(f"PASS: committed source hashes in {len(pins['references'])} reference repositories.")
    print(f"Specified, not executed: {len(scenarios)} future application fault scenarios and native/browser fixture acceptance.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, yaml.YAMLError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error
