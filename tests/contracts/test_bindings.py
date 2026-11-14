import json
import subprocess
from pathlib import Path

import pytest
from proscenium.contracts import SCHEMA, generated
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "docs/contracts/examples"
CASES = json.loads((EXAMPLES / "index.json").read_text())["examples"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["file"])
def test_pydantic_schema_fixture_parity(case):
    value = json.loads((EXAMPLES / case["file"]).read_text())
    model = getattr(generated, case["definition"])
    if case["valid"]:
        assert model.model_validate(value).model_dump(mode="json") == value
        assert model.model_validate_json(json.dumps(value)).model_dump() == value
    else:
        with pytest.raises(ValidationError):
            model.model_validate(value)


def test_all_definitions_generated_and_schema_exact():
    for name in SCHEMA["$defs"]:
        cls = getattr(generated, name)
        assert cls.definition == name
        assert cls.model_json_schema()["$defs"] == SCHEMA["$defs"]


def test_deterministic_generation():
    subprocess.run(
        ["uv", "run", "python", "scripts/generate_contracts.py", "--check"], cwd=ROOT, check=True
    )
    subprocess.run(["node", "scripts/generate-types.mjs", "--check"], cwd=ROOT, check=True)


def test_closed_conditional_and_no_coercion():
    value = json.loads((EXAMPLES / "owner-review-job.json").read_text())
    value["owner_operation_id"] = None  # conditional requires owner authority when work is null
    with pytest.raises(ValidationError):
        generated.JobRecord.model_validate(value)
    with pytest.raises(ValidationError):
        generated.Revision.model_validate("0")
    with pytest.raises(ValidationError):
        generated.OwnerBootstrap.model_validate(
            {
                "schema_version": "1.0",
                "operation_id": "op-test",
                "pairing_code": "x" * 32,
                "client_nonce": "nonce-test",
                "actor": "owner",
            }
        )


def test_semantic_oracles():
    # The normative document validator covers schema-valid semantic negative cases too.
    subprocess.run(["uv", "run", "python", "docs/validate_spec.py"], cwd=ROOT, check=True)


def test_http_operation_ownership():
    from proscenium.app import create_app

    owners = json.loads((ROOT / "docs/contracts/operation-owners.json").read_text())
    expected = {
        op for group in owners["groups"] if group["owner"] == "F00" for op in group["operations"]
    }
    actual = {
        operation["operationId"]
        for methods in create_app().openapi()["paths"].values()
        for operation in methods.values()
        if "operationId" in operation
    }
    assert expected == actual
