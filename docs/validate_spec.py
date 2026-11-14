"""Read-only checks for the specification, wire examples and reference identities.

This does not run application, browser, model, Blender or Unity acceptance tests.
Use an existing Python environment with docs/requirements-validation.txt packages.
"""

import argparse
import copy
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

from semantic_checks import semantic_errors

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
    for name in ("perspective", "orthographic", "shifted_perspective", "rotated_perspective"):
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
    for case in data["transform_cases"]:
        matrix = case["matrix"]
        linear = [matrix[i:i + 3] for i in (0, 4, 8)]

        def cross(a, b):
            return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]

        def unit(vector):
            length = math.sqrt(sum(value*value for value in vector))
            return [value/length for value in vector]

        def transform(point, linear, matrix):
            return [sum(linear[i][j]*point[j] for j in range(3)) + matrix[i*4+3] for i in range(3)]

        cofactors = [cross(linear[1], linear[2]), cross(linear[2], linear[0]), cross(linear[0], linear[1])]
        determinant = sum(linear[0][j]*cofactors[0][j] for j in range(3))
        require(math.isclose(determinant, case["determinant"], abs_tol=1e-9), f"Determinant mismatch: {case['id']}")
        point = transform(case["point"], linear, matrix)
        require(all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(point, case["world_point"])), f"Instance point mismatch: {case['id']}")
        x, y, z = point
        require(case["proxy_point"] == [x, z, -y] and case["unity_point"] == [x, z, y], f"Instance basis mismatch: {case['id']}")
        normal = unit([sum(cofactors[i][j]*case["normal"][j] for j in range(3))/determinant for i in range(3)])
        require(all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(normal, case["world_normal"])), f"Inverse-transpose normal mismatch: {case['id']}")
        require(case["reverse_winding"] == (determinant < 0), f"Object winding mismatch: {case['id']}")
        require(case["unity_reverse_winding"] == (-determinant < 0), f"Unity winding mismatch: {case['id']}")
        points = [transform(point, linear, matrix) for point in case["triangle"]]
        if case["reverse_winding"]:
            points[1], points[2] = points[2], points[1]
        geometric = unit(cross([points[1][i]-points[0][i] for i in range(3)], [points[2][i]-points[0][i] for i in range(3)]))
        require(all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(geometric, normal)), f"Triangle/normal disagreement: {case['id']}")
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


def check_semantic_cases(schema, registry, formats, documents):
    base = DOCS / "acceptance"
    specification = documents[base / "semantic-cases.json"]
    cases = specification["cases"]
    require(len({case["id"] for case in cases}) == len(cases), "Duplicate semantic case ID")
    for case in cases:
        value = copy.deepcopy(documents[(base / case["fixture"]).resolve()])
        for mutation in case.get("mutations", []):
            parent_path, _, key = mutation["path"].rpartition("/")
            parent = pointer(value, parent_path)
            key = key.replace("~1", "/").replace("~0", "~")
            if isinstance(parent, list):
                key = int(key)
            require(key in parent if isinstance(parent, dict) else 0 <= key < len(parent),
                    f"Invalid semantic mutation: {case['id']}")
            if mutation.get("remove"):
                del parent[key]
            else:
                parent[key] = copy.deepcopy(mutation["value"])
        validator = Draft202012Validator({"$ref": f'{schema["$id"]}#/$defs/{case["definition"]}'}, registry=registry, format_checker=formats)
        validator.validate(value)  # Semantic negative cases must still be schema-valid.
        related = {key: documents[(base / path).resolve()] for key, path in case.get("related", {}).items()}
        errors = semantic_errors(case["definition"], value, related)
        require((not errors) == case["valid"], f"Semantic case {case['id']} expected valid={case['valid']}: {errors}")
        if not case["valid"]:
            require(case["error"] in errors, f"Wrong semantic failure for {case['id']}: {errors}")

    edges = documents[DOCS / "contracts/state-transitions.json"]
    for name, definition in (("job_attempt", "JobRecord"), ("work", "WorkRecord")):
        known = set(schema["$defs"][definition]["properties"]["state"]["enum"])
        require(set(edges[name]) == known, f"State catalogue mismatch: {name}")
        require(all(set(targets).issubset(known) and len(targets) == len(set(targets)) for targets in edges[name].values()), f"Invalid state edge: {name}")
    for case in specification["state_cases"]:
        valid = case["to"] in edges[case["record"]][case["from"]]
        require(valid == case["valid"], f"State oracle mismatch: {case}")
        if case.get("requires_resume"):
            require([case["from"], case["to"]] in edges["resume_only_work_edges"], "Resume guard missing")
    for state in ("succeeded", "failed", "cancelled"):
        require(not edges["job_attempt"][state], "A terminal attempt must not be rewritten as a retry")
    for case in specification["mask_cases"]:
        valid = all(type(value) is int and 0 <= value <= 255 for value in case["pixels"])
        if case["semantics"] == "binary":
            valid = valid and all(value in (0, 255) for value in case["pixels"])
        require(valid == case["valid"], f"Mask sample mismatch: {case['id']}")
        if valid:
            require([Fraction(value, 255) for value in case["pixels"]] == [Fraction(*value) for value in case["coverage"]], f"Mask coverage mismatch: {case['id']}")
    return len(cases), len(specification["state_cases"]), len(specification["mask_cases"])


def check_ownership(operations, scenario_ids, schema, documents):
    ownership = documents[DOCS / "contracts/operation-owners.json"]
    assigned = []
    packages = {"F00", *[f"W{i:02d}" for i in range(1, 11)]}
    for group in ownership["groups"]:
        require(group["owner"] in packages, f"Unknown endpoint owner: {group}")
        require(group["path"].startswith("apps/backend/src/proscenium/") and ".." not in Path(group["path"]).parts, "Invalid endpoint ownership path")
        require(set(group["cases"]).issubset(scenario_ids), "Unknown ownership acceptance case")
        assigned.extend(group["operations"])
    require(len(assigned) == len(set(assigned)), "An HTTP operation has competing owners")
    require(set(assigned) == operations, f"Endpoint ownership gap: {set(assigned) ^ operations}")
    for tool in ownership["tools"]:
        require(tool["owner"] in packages and set(tool["cases"]).issubset(scenario_ids), "Invalid tool ownership")
        require(tool["request"] in schema["$defs"] and tool["response"] in schema["$defs"], "Missing tool request/result contract")


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
    require(len(ids) == len(scenarios) and ids == {f"C{i:02d}" for i in range(1, 32)}, "Scenario catalogue has missing/duplicate IDs")
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
    semantic_count, state_count, mask_count = check_semantic_cases(schema, registry, formats, documents)
    check_ownership(operations, ids, schema, documents)
    print(f"PASS: {len(documents)} JSON/YAML documents; {len(schema['$defs'])} schema definitions; {refs} references; {len(index)} positive/negative schema examples.")
    print(f"PASS: {len(operations)} HTTP operations; {links} local links; {external} pinned sibling-reference links; 2 design hashes; coordinate/time vectors.")
    print(f"PASS: {semantic_count} positive/negative semantic oracles; {state_count} transition cases; {mask_count} mask cases; complete endpoint/tool ownership.")
    if args.check_references:
        print(f"PASS: committed source hashes in {len(pins['references'])} reference repositories.")
    print(f"Specified, not executed: {len(scenarios)} future application fault scenarios and native/browser fixture acceptance.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError, yaml.YAMLError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error
