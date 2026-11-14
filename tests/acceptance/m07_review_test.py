"""Offline guard checks and retained real readback evidence, never launches services."""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "native/studio"))
from m07_review_server import ReadOnlyGuard  # noqa: E402


@pytest.mark.parametrize(
    "method,path,allowed",
    [
        ("GET", "/api/v1/projects/p/artifacts/a/content", True),
        ("POST", "/api/v1/owner/session", True),
        ("POST", "/api/v1/projects/p/activate", True),
        ("POST", "/api/v1/projects/p/requests", False),
        ("POST", "/internal/v1/generation-jobs", False),
        ("POST", "/api/v1/projects/p/selections", False),
    ],
)
def test_read_only_guard(method, path, allowed):
    audit: dict[str, Any] = {"forbidden": [], "requests": []}
    reached = []
    sent = []

    async def app(*args):
        reached.append(True)

    async def send(message):
        sent.append(message)

    asyncio.run(
        ReadOnlyGuard(app, audit)({"type": "http", "method": method, "path": path}, None, send)
    )
    assert bool(reached) == allowed
    assert bool(audit["forbidden"]) != allowed
    if not allowed:
        assert sent[0]["status"] == 403


def retained():
    root = os.environ.get("M07_REVIEW_ACTUAL")
    if not root:
        pytest.skip("Set M07_REVIEW_ACTUAL to retained actual readback, not a new execution")
    path = Path(root)
    run = json.loads((path / "run.json").read_text())
    result = json.loads((path / "browser-result.json").read_text())
    audit = json.loads((path / "server-audit.json").read_text())
    return run, result, audit


def test_retained_actual_boundary_cleanup():
    run, result, audit = retained()
    assert run["original_unchanged"] and run["port_released"]
    assert run["protected_identities_unchanged"]
    assert run["elapsed"] <= 300
    assert min(row["available"] for row in run["samples"]) >= 4 * 1024**3
    assert all(row["reaped"] for row in run["children"].values())
    assert audit["forbidden"] == [] and audit["gate_closed"]
    assert audit["sdk_sends"] == audit["provider_sends"] == 0
    assert audit["services"] == audit["execution_profiles"] == []


def test_retained_actual_readback():
    run, result, audit = retained()
    assert run.get("pass") and result.get("pass"), result.get(
        "error", "Actual UI proof is incomplete"
    )
    assert len(result["views"]) == 7
    assert result["before"] == result["after"]
    assert result["viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
    for row in result["views"]:
        assert row["context"]["target"]["displayed_sha256"] == row["content"]["sha256"]
        assert row["context"]["target"]["render_job_id"] is None
        assert row["context"]["target"]["source_frame"] is None
        if "reference" in row["name"]:
            assert row["job"] == "" and row["label"] == "No associated job"
        else:
            assert row["job"] in row["image"]["src"] and row["job"] in row["label"]
