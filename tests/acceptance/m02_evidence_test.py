"""Integrity checks of retained real-run evidence. Passing does NOT accept M02."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2] / "docs/evidence/m02"


def load(name):
    return json.loads((ROOT / name).read_text())


def test_recorded_m02_evidence_manifest():
    for path, record in load("manifest.json").items():
        content = (ROOT / path).read_bytes()
        assert hashlib.sha256(content).hexdigest() == record["sha256"]
        assert len(content) == record["bytes"]


def test_actual_two_model_sessions_have_different_active_project_content():
    records = load("production-records.json")
    ids = set()
    for label in ("a", "b"):
        project = records[label]
        ids.add(project["copilot.session"][0]["session_id"])
        assert len(project["requests"]) == len(project["copilot.sends"]) == 1
        text = "\n".join(
            e["payload"]["text"]
            for e in project["events"]
            if e["event_type"] == "assistant.completed"
        )
        assert f"M02-{label.upper()}-only-20261114" in text
        other = "B" if label == "a" else "A"
        assert f"M02-{other}-only-20261114" not in text
    assert len(ids) == 2
    assert load("verification.json")["strict_result"] == "FAIL"


def test_actual_independent_histories_atomic_batches_and_unfinished_exclusion():
    records = load("production-records.json")["a"]
    assert Counter(v["item_id"] for v in records["versions"]) == {
        "asset-cube": 2,
        "asset-companion": 2,
        "scene-main": 2,
        "scene-alternate": 2,
        "asset-delayed": 1,
    }
    receipts = load("fixture-receipts.json")
    seed = receipts["02-seed"]["result"]
    assert len(seed["receipt"]["versions"]) == 4
    assert {v["item_id"] for v in seed["receipt"]["versions"]} == {
        "asset-cube",
        "asset-companion",
        "scene-main",
        "scene-alternate",
    }
    assert len(receipts["04-finish"]["result"]["receipt"]["versions"]) == 3
    assert not records["frames"]
    assert records["jobs"][0]["state"] == "cancelled"
    assert load("browser-pinned-after-advance.json")["visible"]["source"].endswith("scene-main-v1")


def test_old_blend_really_opened_original_dependencies_without_live_mutation():
    receipts = load("fixture-receipts.json")
    inspection = receipts["06-old-open"]["result"]
    assert inspection["report"]["native_exit_code"] == 0
    assert inspection["report"]["object_dimensions"]["Blue cube"] == [1, 1, 1]
    assert inspection["report"]["object_dimensions"]["Renamed cube"] == [1, 1, 1]
    assert inspection["report"]["missing"] == []
    assert len(inspection["processes"]) == 1
    assert inspection["before"] == inspection["after"] == receipts["05-dirty"]["result"]
    assert receipts["07-after-browse"]["result"] == receipts["05-dirty"]["result"]
    assert load("m01-before.json") == load("m01-after.json")


def test_reopen_full_hd_and_real_defects_are_retained_not_relabeled_success():
    for name in (
        "browser-pinned-after-advance.png",
        "browser-reopened-a.png",
        "browser-reopened-b.png",
    ):
        assert Image.open(ROOT / name).size == (1920, 1080)
    receipts = load("fixture-receipts.json")
    assert receipts["11-diagnose-old-event"]["result"]["reducer_error"] == "scope_denied"
    assert load("browser-native-b.json")["requests"]["items"][0]["receipt"]["state"] == "held"
    assert (
        load("browser-recovered-b.json")["requests"]["items"][0]["receipt"]["state"] == "completed"
    )
    assert load("browser-post-cancel.json")["jobs"]["items"][0]["state"] == "cancel_requested"
    assert receipts["08-explicit-cancel"]["result"]["job"]["state"] == "cancelled"
    assert load("cleanup.json")["m01_unchanged"]
