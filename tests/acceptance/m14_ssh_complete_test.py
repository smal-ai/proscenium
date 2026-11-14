"""One turn may have several assistant messages. Prove identities, not a count of one."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/native/studio"))
from m14_ssh_oracle import assistant_projection  # noqa: E402


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
    subprocess.run(["git", "init", "--object-format=sha1", str(project)], check=True, capture_output=True)
    config = {"schema_version": "1.0", "project_id": "imported", "repository_id": "imported-repo", "name": "Labelled import",
              "paths": {name: name for name in ("assets", "brief", "scenes", "sequences", "storyboards")}}
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
