"""Integrity checks of retained real-run evidence, NOT a new native acceptance run."""

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageChops

EVIDENCE = Path(__file__).resolve().parents[2] / "docs/evidence/m01"


def load(name):
    return json.loads((EVIDENCE / name).read_text())


def test_recorded_evidence_bytes_are_unchanged():
    for name, record in load("manifest.json").items():
        content = (EVIDENCE / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == record["sha256"]
        assert len(content) == record["bytes"]


def test_recorded_native_dimensions_and_real_pixels_changed():
    before, after = load("native-before-feedback.json"), load("native-after-feedback.json")
    first = next(o for o in before["objects"] if o["object_id"] == "loop-blue-cube")
    second = next(o for o in after["objects"] if o["object_id"] == "loop-blue-cube")
    assert first["dimensions"] == [2, 2, 2] and second["dimensions"] == [2, 2, 4]
    images = [Image.open(EVIDENCE / f"clean-v{v}.png").convert("RGB") for v in (1, 2)]
    changed = sum(
        pixel != (0, 0, 0) for pixel in ImageChops.difference(*images).get_flattened_data()
    )
    assert changed == load("verification.json")["changed_pixels"] == 55819


def test_recorded_sdk_received_clean_marked_and_pinned_context():
    public = load("public-final.json")
    annotation = public["annotations"]["items"][0]
    assert annotation == load("public-submitted-feedback.json")["annotations"]["items"][0]
    assert annotation["target"]["version_id"] == "loop-v1-clean"
    messages = [e for e in load("native-delivery-redacted.json") if e["type"] == "user.message"]
    delivered = next(
        e
        for e in messages
        if any(a.get("payload", {}).get("annotations") for a in e["data"]["attachments"])
    )
    assert len([a for a in delivered["data"]["attachments"] if a["type"] == "file"]) == 2
    payload = next(
        a["payload"] for a in delivered["data"]["attachments"] if a["type"] == "extension_context"
    )
    assert payload["annotations"] == [annotation]
    assert payload["selection"]["version_id"] == "loop-v1-clean"
    assert (
        hashlib.sha256((EVIDENCE / "clean-v1.png").read_bytes()).hexdigest()
        == annotation["target"]["displayed_sha256"]
    )
    assert (
        hashlib.sha256((EVIDENCE / "marked-v1.png").read_bytes()).hexdigest()
        == public["artifacts"][annotation["markup_artifact_id"]]["sha256"]
    )


def test_recorded_replay_restart_and_resource_bounds():
    replay = load("publication-replay.json")
    assert replay["http_status"] == 200 and replay["response"]["replayed"]
    assert replay["before"] == replay["after"]
    restarted = load("restart-verification.json")
    assert restarted["project_state_after_restart"] == "awaiting_resume"
    assert restarted["requests_unchanged"] and restarted["jobs_unchanged"]
    assert not restarted["old_sdk_pid_alive"] and not restarted["new_sdk_descendants"]
    verification = load("verification.json")
    assert verification["root_requests"] == 3 and verification["native_children"] == 0
    assert verification["rendered_frames"] == 2
    assert all(r["seconds"] <= 180 for r in load("identities.json")["request_durations"])
    assert load("cleanup.json")["all_observed_owned_processes_exited"]


def test_recorded_browser_interactions_are_full_hd():
    for name in (
        "browser-new-version.png",
        "browser-retained-annotation.png",
        "browser-pin-before-send.png",
    ):
        assert Image.open(EVIDENCE / name).size == (1920, 1080)
    identity = load("identities.json")
    assert identity["browser"]["interaction_viewport"] == {"width": 1920, "height": 1080, "dpr": 1}
