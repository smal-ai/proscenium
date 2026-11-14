"""Supervisor fault test with fake process handles/signals, never Blender."""

import json
import signal
from typing import Any

from proscenium.workflows import export_worker


def test_interruption_inside_popen_is_deferred_until_child_can_be_reaped(tmp_path, monkeypatch):
    handlers: dict[Any, Any] = {}
    groups, launches = [], []
    request = tmp_path / "export-request.json"
    request.write_text(
        json.dumps(
            dict(
                binding="offline-binding",
                source_digest="a" * 64,
                blender="/not-executed/Blender",
                entry="/offline/fixture.blend",
                materialised=str(tmp_path),
                output=str(tmp_path / "export.json"),
                claim_path=str(tmp_path / "own-export.lock"),
            )
        )
    )

    class Process:
        pid = 123456789
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, **kwargs):
            self.returncode = -15
            return self.returncode

    def spawn(*args, **kwargs):
        launches.append(args)
        handlers[signal.SIGTERM](
            signal.SIGTERM, None
        )  # Inside constructor, before handle assignment.
        return Process()

    monkeypatch.setattr(
        export_worker.signal, "signal", lambda name, function: handlers.update({name: function})
    )
    monkeypatch.setattr(
        export_worker,
        "identity",
        lambda pid: {"pid": pid, "start_ticks": "offline", "boot_id": "offline"},
    )
    monkeypatch.setattr(export_worker.subprocess, "Popen", spawn)
    monkeypatch.setattr(export_worker.os, "killpg", lambda pid, sig: groups.append((pid, sig)))
    export_worker.main(request)
    receipt = json.loads((tmp_path / "export-worker.json").read_text())
    assert len(launches) == 1 and groups == [(123456789, signal.SIGTERM)]
    assert receipt["state"] == "complete" and receipt["native_exit_code"] == -15
    assert receipt["error"] == "KeyboardInterrupt"
