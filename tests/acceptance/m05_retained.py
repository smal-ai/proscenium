"""Offline assertions over the retained real M05 attempt; no model/native execution."""

import hashlib
import io
import json
import runpy
import struct
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image
from proscenium.contracts import validate

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m05"


def read(name):
    return json.loads((EVIDENCE / name).read_text())


def test_actual_native_discovery_and_bounded_cloud_allocation():
    identity = read("identities.json")
    proof = read("source-verification.json")
    assert identity["sdk"] == "1.0.34" and identity["model"] == "gpt-6.1-sol"
    assert proof["native_roots"] == 3 and proof["native_children"] == 0
    assert all(ms <= 180000 for ms in proof["root_durations_ms"])
    calls = read("native-tool-invocations.json")
    assert [c["arguments"]["query"] for c in calls if c["toolName"] == "list_items"] == [
        "amber-cube",
        "tabletop blocking",
        "Reusable fixture cube",
    ]
    assert any(
        c["toolName"] == "get_version" and c["arguments"]["version_id"] == "asset-cube-v1"
        for c in calls
    )
    assert not {c["toolName"] for c in calls} & {"task", "submit_generation", "request_vlm_review"}


def test_real_acquisition_provenance_and_truthful_statuses():
    ledger = read("acquisition-ledger.json")
    assert ledger["download_bytes"] == 12918 and ledger["download_bytes"] < 5 * 1024**2
    assert ledger["coverage"]["optional_blendswap"] == "not configured"
    assert {"discovered", "acquired", "imported", "validated"} <= ledger["observations"].keys()
    assert ledger["provenance"]["creator"] == "Cesium"
    assert ledger["provenance"]["licence"] == "CC-BY-4.0"
    assert ledger["repository_commit"] in ledger["provenance"]["source_url"]
    data = (EVIDENCE / "Box.glb").read_bytes()
    assert struct.unpack_from("<4sII", data) == (b"glTF", 2, len(data))
    assert len(data) == 1664
    record = next(row for row in ledger["downloads"] if row["path"].endswith("Box.glb"))
    assert hashlib.sha256(data).hexdigest() == record["sha256"]
    assert "CC-BY-4.0" in (EVIDENCE / "khronos-metadata.json").read_text()


def test_import_and_revision_are_real_and_distinct():
    initial = read("import-inspection.json")
    revised = read("revision-inspection.json")
    assert initial["imported_from"] == "research/Box.glb"
    assert initial["blender"]["version"] == revised["blender"]["version"] == "4.5.14 LTS"
    assert initial["vertices"] == revised["vertices"] == 24
    assert initial["polygons"] == revised["polygons"] == 12
    assert initial["bounds_m"]["max"][2] == pytest.approx(1, abs=1e-5)
    assert revised["dimensions_m"] == pytest.approx([1, 1, 1.5], abs=1e-5)
    assert revised["bounds_m"]["min"][2] == 0
    assert revised["materials"] == ["Red"]
    assert revised["ids"]["proscenium_object_id"] == "object-cube-source"
    assert revised["ids"]["proscenium_instance_id"] == "instance-asset"
    assert revised["units"] == {"system": "METRIC", "scale_length": 1}
    assert revised["before_dependencies"]["blend_paths"]
    assert revised["after_dependencies"]["blend_paths"] == []


def test_scene_pins_old_assets_and_unknown_licence_publication_are_preserved():
    final = read("public-final.json")
    setup = read("labelled-setup.json")["result"]["submission"]
    published = {v["version_id"]: v for page in final["versions"].values() for v in page["items"]}
    for item in setup["items"]:
        version = published[item["version_id"]]
        validate("VersionRecord", version)
        assert version["source"] == item["source"]
        assert version["provenance"]["licence"] is None
        if item["kind"] == "scene":
            assert version["source"]["dependencies"] == [
                {"item_id": "asset-cube", "version_id": "asset-cube-v1"}
            ]
    assert len(final["versions"]["asset-cube"]["items"]) == 3
    assert final["selections"]["items"][0]["choice"]["version"]["version_id"] == "asset-cube-v1"
    assert read("source-verification.json")["both_scene_versions_and_dependencies_unchanged"]
    assert len(read("ui-local-search.json")) == 3
    assert "CC-BY-4.0" in read("ui-known-provenance.json")["catalogue_text"]


def test_only_corrected_revision_render_completed_and_failed_proxy_not_hidden():
    final = read("public-final.json")
    jobs = final["jobs"]["items"]
    assert Counter(j["state"] for j in jobs) == {"failed": 2, "succeeded": 1}
    assert Counter(j["render_spec"]["purpose"] for j in jobs) == {"keyframe": 2, "proxy": 1}
    successful = next(j for j in jobs if j["state"] == "succeeded")
    assert successful["target"]["version"]["version_id"] == "asset-cube-v3"
    for job in jobs:
        assert job["attempt_number"] == 1 and job["render_spec"]["device"] == "CPU"
        assert job["render_spec"]["samples"] == 8
    frame = final["frames"][successful["job_id"]]["items"][0]
    assert (
        hashlib.sha256((EVIDENCE / "keyframe-v3.png").read_bytes()).hexdigest() == frame["sha256"]
    )
    assert Image.open(EVIDENCE / "keyframe-v3.png").size == (640, 360)
    assert final["view"]["context"]["target"]["origin"] == "blender_render"


@pytest.mark.parametrize("partial", [False, True])
def test_actual_acquisition_helper_fault_preserves_existing_sources(tmp_path, monkeypatch, partial):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["acquire_box.py", "offline-fixture-noop"])
    module = runpy.run_path(str(EVIDENCE / "acquire_box.py.txt"))
    protected = tmp_path / "assets/cube/asset.blend"
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"labelled pre-existing editable source sentinel")
    original = protected.read_bytes()

    class BrokenResponse(io.BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise urllib.error.URLError("offline fixture: connection lost after partial bytes")
            return super().read(size)

    def fail(*args, **kwargs):
        if partial:
            return BrokenResponse(b"glTF-partial-incomplete")
        raise urllib.error.HTTPError(
            "https://example.invalid/fixture.glb", 503, "offline fixture unavailable", {}, None
        )

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    ledger = {"download_bytes": 0, "downloads": []}
    with pytest.raises(urllib.error.URLError, match="offline fixture"):
        module["fetch"]("https://example.invalid/fixture.glb", "research/fixture.glb", ledger)
    assert protected.read_bytes() == original
    assert not (tmp_path / "research/fixture.glb").exists()
    assert not (tmp_path / "research/fixture.glb.partial").exists()
    assert ledger["downloads"] == []


def test_adoption_failure_and_ui_context_failure_remain_explicit():
    history = read("history-adoption.json")
    assert history["status"] == "blocked_final_authoring_attempt_exhausted"
    assert history["historical_inspections"][0]["texture_validation"] == "not reached"
    assert len(history["historical_inspections"]) == 1
    for instance in history["historical_inspections"][0]["instances"]:
        assert instance["dimensions_m"] == [1, 1, 1]
    assert "scene-main-v2" not in {
        v["version_id"] for p in read("public-final.json")["versions"].values() for v in p["items"]
    }
    assert read("known-ui-context-defect.json")["observed"]["context"]["target"] is None
    authoring = read("native-authoring-processes.json")
    assert len(authoring) == 3 and all(row["reaped"] for row in authoring)
    assert all(row["available_bytes_before"] >= 4 * 1024**3 for row in authoring)


def test_manifest_cleanup_and_no_automatic_model_reopen():
    for name, identity in read("manifest.json").items():
        content = (EVIDENCE / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == identity["sha256"]
        assert len(content) == identity["bytes"]
        if name.endswith(".json"):
            assert '"csrf_token":' not in content.decode()
            assert "#pair=" not in content.decode()
    cleanup = read("cleanup.json")
    assert cleanup["browser_reaped"] and cleanup["native_proc_absent"]
    reopen = read("reopen-observation.json")
    assert reopen["requests_before"] == reopen["requests_after"] == 3
    assert not reopen["automatic_resume_sent"]
