"""One turn may have several assistant messages. Prove identities, not a count of one."""

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/native/studio"))
from m14_ssh_oracle import assistant_projection  # noqa: E402
from m14_ssh_sources import source_bytes  # noqa: E402


def messages_and_events():
    messages = [
        {"message_id": "preamble", "event_id": "event-a", "content": "Reading the marker."},
        {
            "message_id": "answer",
            "event_id": "event-b",
            "content": "MARKER context-id m13-film-v1 animation",
        },
    ]
    events = [
        {
            "event_type": "assistant.completed",
            "native_event_id": m["event_id"],
            "payload": {"message_id": m["message_id"], "text": m["content"]},
        }
        for m in messages
    ]
    return messages, events


def test_multiple_public_messages_from_one_turn_are_not_duplicates():
    messages, events = messages_and_events()
    assert all(
        assistant_projection(
            messages, events, ["MARKER", "context-id", "m13-film-v1", "animation"]
        ).values()
    )


@pytest.mark.parametrize(
    "fault", ["duplicate-public", "duplicate-native", "missing", "wrong-text", "wrong-id"]
)
def test_message_projection_does_not_hide_duplicates_or_loss(fault):
    messages, events = messages_and_events()
    if fault == "duplicate-public":
        events.append(copy.deepcopy(events[0]))
    elif fault == "duplicate-native":
        messages.append(copy.deepcopy(messages[0]))
    elif fault == "missing":
        events.pop()
    elif fault == "wrong-text":
        events[1]["payload"]["text"] = "different response"
    else:
        events[1]["payload"]["message_id"] = "different ID"
    assert not all(assistant_projection(messages, events, ["MARKER"]).values())


def test_reply_must_contain_marker_and_context_together():
    messages, events = messages_and_events()
    assert not assistant_projection(messages, events, ["missing marker"])[
        "meaningful_final_marker_context_reply"
    ]


def test_shared_exclusive_guard_survives_phase_folder_change(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "m14_ssh_host", ROOT / "tests/native/studio/m14_ssh_host.py"
    )
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    host.reserve_send(a, {"request_id": "original", "count": 1}, guard_root=tmp_path)
    with pytest.raises(FileExistsError):
        host.reserve_send(b, {"request_id": "duplicate", "count": 2}, guard_root=tmp_path)
    assert (
        json.loads((tmp_path / "sdk-send-reservation.json").read_text())["request_id"] == "original"
    )
    assert not (b / "send-reservation.json").exists()


def test_import_restores_empty_configured_directories_for_real_activation_validator(tmp_path):
    from m14_ssh_prepare import import_roots
    from proscenium.api.errors import DomainError
    from proscenium.projects.service import Projects

    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init", "--object-format=sha1", str(project)], check=True, capture_output=True
    )
    config = {
        "schema_version": "1.0",
        "project_id": "imported",
        "repository_id": "imported-repo",
        "name": "Labelled import",
        "paths": {name: name for name in ("assets", "brief", "scenes", "sequences", "storyboards")},
    }
    (project / "project.json").write_text(json.dumps(config))
    with pytest.raises(DomainError) as failure:
        Projects.config(project)
    assert failure.value.code == "scope_denied"
    import_roots(project, config)
    assert Projects.config(project) == config


def test_import_directory_paths_cannot_escape(tmp_path):
    from m14_ssh_prepare import import_roots

    with pytest.raises(AssertionError):
        import_roots(tmp_path, {"paths": {"brief": "../other-project"}})


def completed():
    return json.loads((ROOT / "docs/evidence/m14-ssh/complete-result.json").read_text())


def test_actual_original_turn_survived_tunnel_cut_without_duplication():
    value = completed()
    assert value["status"] == "passed" and all(value["checks"].values())
    assert value["cut"]["active_at_cut"] and value["cut"]["forward_closed"]
    assert value["completion_disconnected"]["forward_closed_at_completion_observation"]
    assert value["completion_disconnected"]["observed_ns"] > value["cut"]["after_cut_ns"]
    facts = value["native_facts"]
    assert len(facts["user_messages"]) == 1 and len(facts["assistant_messages"]) == 2
    assert facts["assistant_messages"][0]["content"] == ""  # Real legitimate pre-tool completion.
    assert all(
        assistant_projection(
            facts["assistant_messages"],
            value["browser"]["replay_after"]["events"],
            [
                "M14_SSH_READ_ONLY_20261114",
                value["cut"]["contexts_before"][0]["context_id"],
                "m13-film-v1",
                "animation",
            ],
        ).values()
    )
    assert [tool["name"] for tool in facts["tool_calls"]] == ["view"] and not facts["children"]


def test_actual_film_playback_pin_download_range_and_upload():
    browser = completed()["browser"]
    assert browser["presented_ordinals"] == list(range(1, 11))
    assert all(
        frame["width"] == 640 and frame["height"] == 360 for frame in browser["numbered_identity"]
    )
    mapping = browser["mapping"]["frames"]
    assert len(mapping[2]["contributors"]) == 1 and len(mapping[3]["contributors"]) == 2
    assert len(mapping[5]["contributors"]) == 2 and len(mapping[6]["contributors"]) == 1
    annotation = browser["annotation"]
    assert (
        annotation
        == browser["annotation_after_reconnect"]
        == browser["old_annotation_after_restart"]
    )
    assert (
        annotation["target"]["sequence_frame"] == 5 and annotation["target"]["source_frame"] is None
    )
    assert (
        annotation["target"]["displayed_sha256"]
        == "175388689618d986d241179067b8d8ea20cc819458421191421c978640f847af"
    )
    assert annotation["markups"][0]["kind"] == "pin"
    assert (
        browser["downloads"][0]["sha256"]
        == "ddf01466d4a086bcdae27890e6cbabaa2bf309b62e48bf9353a056d92a60aa8e"
    )
    assert browser["downloads"][0]["bytes"] == 64876
    assert (
        browser["downloads"][1]["sha256"]
        == browser["catalogue"]["m13-encode-v1-manifest"]["sha256"]
    )
    assert (
        browser["range"]["status"] == 206
        and browser["range"]["headers"]["content-range"] == "bytes 0-31/64876"
    )
    assert len(browser["range"]["bytes"]) == 32 and browser["upload"]["bytes"] == 82
    assert browser["upload_receipt_recovery"]["extra_uploads"] == 0


def test_actual_quiet_unavailable_review_has_no_native_or_provider_replay():
    value = completed()
    first, second = value["host_1"], value["host_2"]
    assert first["sends"] == 1 and second["sends"] == 0
    assert not first["forbidden"] and not second["forbidden"]
    assert second["gate_closed"] and second["native_processes"] == [] and second["options"] == []
    assert first["records"]["contexts"] == second["records"]["contexts"]
    assert first["records"]["requests"] == second["records"]["requests"]
    assert len(first["records"]["annotations"]) == 1 and len(second["records"]["annotations"]) == 2
    assert all(row["tcp_closed"] for row in value["closed_endpoints_observed"])
    configuration = value["browser"]["configuration"]
    assert configuration["profiles"][0]["scope_status"] == "unavailable"
    assert all(service["available"] is False for service in configuration["services"])
    assert "Configuration metadata only" in value["browser"]["unavailable_text"]
    assert "Offline" in value["browser"]["unavailable_text"]


def test_actual_replay_cleanup_and_resource_bounds():
    value = completed()
    for label in ("replay_after", "replay_restart"):
        sequences = value["browser"][label]["sequences"]
        assert sequences == list(range(1, len(sequences) + 1))
    assert len(value["browser"]["replay_after"]["sequences"]) == 59
    assert len(value["browser"]["replay_restart"]["sequences"]) == 61
    assert value["boundary"] == {"normal": 200, "wrong_host": 403, "wrong_origin": 403}
    assert value["browser"]["off_origin"] == []
    assert len(value["processes"]) == 6 and all(process["start"] for process in value["processes"])
    assert all(row["original_identity_gone"] for row in value["cleanup"])
    assert (
        all(value["host_1"]["native_identities_gone"])
        and value["owned_descendants_remaining"] == {}
    )
    assert (
        value["available_ram_bytes_before_start"] >= 4 * 1024**3 and value["elapsed_seconds"] < 1200
    )
    assert value["original_m13_byte_preserved"] and len(value["browser"]["observer_failures"]) == 2


def test_exact_runtime_lineage_and_shared_allocation_summary():
    value = json.loads((ROOT / "docs/evidence/m14-ssh/complete-summary.json").read_text())
    assert value["status"] == "passed_bounded_m14_ssh_message_media_unavailable_review"
    allocation = value["allocation"]
    assert allocation["startup_sets"] == 1 and allocation["sdk_sends"] == 1
    assert allocation["including_pre_phase_key_setup_seconds"] < 1200
    assert allocation["send_to_observed_completion_seconds"] < 180
    assert value["original_m13_inventory_files"] == 195
    assert (
        hashlib.sha256(
            (ROOT / "docs/evidence/m14-ssh/complete-result.json").read_bytes()
        ).hexdigest()
        == value["raw_report_sha256"]
    )
    lineage = value["source_lineage"]
    for hashes, revision in (
        (
            lineage["launched_source_hashes"],
            lineage["launched_driver_host_browser_import_revision"],
        ),
        (lineage["runtime_recovery_module_hashes"], lineage["pre_send_reviewed_commit"]),
    ):
        for path, digest in hashes.items():
            data = source_bytes(revision, path, digest)
            assert hashlib.sha256(data).hexdigest() == digest
    assert (
        value["pre_send_authorisation"]["ns"]
        < allocation["shared_exclusive_send_guard"]["before_send_ns"]
    )
    assert len(value["compiled_ui_assets"]) > 40 and len(value["case_failures_retained"]) == 2
    assert value["versions"]["sdk"] == "1.0.34" and value["versions"]["cli_package"] == "1.0.112"
    assert (
        value["versions"]["cli_binary_sha256"]
        == "463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69"
    )
    actual = completed()
    usage = [
        event
        for event in actual["browser"]["replay_after"]["events"]
        if event["event_type"] == "usage.recorded"
    ]
    assert usage and all(event["payload"]["model_id"] == "gpt-6.1-sol" for event in usage)
    assert actual["host_1"]["options"][0]["memory"] == {"enabled": False}
    assert actual["host_1"]["options"][0]["enable_session_store"] is False


def test_actual_screenshots_are_full_hd_without_starting_another_browser():
    from PIL import Image

    for name in (
        "complete-review.png",
        "complete-unavailable-services.png",
        "complete-unavailable-review.png",
    ):
        with Image.open(ROOT / "docs/evidence/m14-ssh" / name) as image:
            assert image.size == (1920, 1080)


def test_source_inspection_never_requires_original_fleet_git_objects(monkeypatch):
    manifest_path = ROOT / "docs/evidence/m14-ssh/complete-source-mirrors.json"
    manifest = json.loads(manifest_path.read_text())
    summary = json.loads((ROOT / "docs/evidence/m14-ssh/complete-summary.json").read_text())
    assert (
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        == summary["portable_source_inspection"]["manifest_sha256"]
    )
    original_revisions = set(manifest["sources"])
    real = subprocess.check_output

    def main_reachable_only(command, *args, **kwargs):
        if command[:2] == ["git", "show"]:
            assert command[2].split(":", 1)[0] not in original_revisions
        return real(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "check_output", main_reachable_only)
    for original, entry in manifest["sources"].items():
        for path, record in entry["files"].items():
            assert (
                hashlib.sha256(source_bytes(original, path, record["sha256"])).hexdigest()
                == record["sha256"]
            )
    mirrors = {
        entry.get("accepted_revision")
        for entry in manifest["sources"].values()
        if entry["mode"] == "accepted_git_mirror"
    }
    assert mirrors == {
        "a46b510af2f1a257a59e3b56a6359dac882a532f",
        "3d3fc64b0c47d64edc01cf67e2e08f77b53eda98",
    }


def test_retained_source_tampering_and_wrong_evidence_digest_fail(tmp_path):
    directory = tmp_path / "docs/evidence/m14-ssh/complete-sources/revision"
    directory.mkdir(parents=True)
    retained = directory / "source.txt"
    retained.write_bytes(b"exact source\n")
    digest = hashlib.sha256(retained.read_bytes()).hexdigest()
    manifest = {
        "sources": {
            "original": {
                "original_revision": "original",
                "mode": "retained_exact_source",
                "files": {
                    "source.py": {
                        "sha256": digest,
                        "retained_path": str(retained.relative_to(tmp_path)),
                    }
                },
            }
        }
    }
    (tmp_path / "docs/evidence/m14-ssh/complete-source-mirrors.json").write_text(
        json.dumps(manifest)
    )
    assert source_bytes("original", "source.py", digest, root=tmp_path) == b"exact source\n"
    with pytest.raises(AssertionError, match="Evidence/mirror hash disagreement"):
        source_bytes("original", "source.py", "0" * 64, root=tmp_path)
    retained.write_bytes(b"changed source\n")
    with pytest.raises(AssertionError, match="Source mirror bytes changed"):
        source_bytes("original", "source.py", digest, root=tmp_path)
