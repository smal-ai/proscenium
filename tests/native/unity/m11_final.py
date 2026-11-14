"""One original-work publication continuation. No operator-authored first effects."""

import asyncio
import dataclasses
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from proscenium.contracts import validate
from proscenium.copilot.service import wire
from proscenium.copilot.tools import Tools
from proscenium.sources.common import relative

if TYPE_CHECKING:
    from tests.native.unity.m11_publish_host import ORIGINAL, REPO, main
    from tests.native.unity.m11_publish_snapshot import SESSION_ID, WORK_ID, records, sha
else:
    from m11_publish_host import ORIGINAL, REPO, main
    from m11_publish_snapshot import SESSION_ID, WORK_ID, records, sha

SCENE = "unity/M11/Assets/Proscenium/Generated/f4ecf3b8541adc9031e22d92/Playable.unity"
SCENE_SHA = "2d8e139c878c2ad4c6734f5616d1412e91b4bcf06f7c5e3fb9c604199c84d834"
HEAD = "3297ae647200270ebdc102630e7cfbfce2f4bfd1"


def tool_wire(value):
    """SDK tool records are dataclasses, not the Pydantic native event objects."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return wire(value)


def headroom():
    memory = {
        line.split(":")[0]: int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith(("MemAvailable:", "MemTotal:"))
    }
    assert memory["MemAvailable"] >= 4 * 1024**3, memory
    disk = shutil.disk_usage(ORIGINAL).free
    assert disk >= 5 * 1024**3, disk
    return dict(memory=memory, disk_free=disk)


def preflight():
    import importlib.metadata

    project = ORIGINAL / "project"
    database = project / ".proscenium/state.sqlite3"
    body = json.loads((REPO / "docs/evidence/m11-publication/permission-blocker.json").read_text())[
        "request"
    ]["args"]
    validate("CheckpointRequest", body)
    paths = {p: sha(relative(project, p, exists=True)) for p in body["paths"]}
    assert paths[SCENE] == SCENE_SHA
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
        == HEAD
    )
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        assert (
            db.execute(
                "SELECT id FROM records WHERE namespace='checkpoint_intents' AND id=?",
                ("m11-unity-source-checkpoint",),
            ).fetchone()
            is None
        )
    assert not any(v["kind"] == "unity_result" for v in records(database, "versions"))
    invocations = records(database, "invocations")
    assert all(i["state"] in {"completed", "failed", "cancelled", "stopped"} for i in invocations)
    work = next(w for w in records(database, "work") if w["work_id"] == WORK_ID)
    # Accepted metadata recovery deliberately preserved the old awaiting_user work row.
    # Cold production reconciliation, not a private state edit, owns its next transition.
    assert work["state"] in {"awaiting_resume", "awaiting_user"}
    assert work["native_session_id"] == SESSION_ID
    decision = next(
        d
        for d in records(database, "decisions")
        if d["decision_id"]
        == "decision-35e4534552dc528a97a1ae5035496f83af51d8cc430f11ae95cdef3efebb90e3"
    )
    assert decision["state"] == "cancelled"
    assert decision["response"] is None and decision["resolved_by"] is None
    assert decision["delivery_state"] == "not_sent"
    sdk = importlib.metadata.version("github-copilot-sdk")
    assert sdk == "1.0.34", sdk
    return dict(
        status="passed",
        head=HEAD,
        paths=paths,
        sdk=sdk,
        cancelled_decision=decision,
        work_state_before_cold_reconcile=work["state"],
        guard=headroom(),
        specification="3.2.0",
        contract="1.0.0",
        wire="1.0",
    )


class FinalHooks:
    """Observe real tools and replay only completed original native invocations."""

    def __init__(self, services, control):
        self.services, self.control = services, control
        self.calls: list[tuple[Any, str, Any, Any]] = []
        self.loop = None
        self.replaying = False
        original = Tools.invoke

        async def observed(tools, name, invocation):
            self.loop = asyncio.get_running_loop()
            result = await original(tools, name, invocation)
            if name in {"checkpoint_sources", "publish_outputs"}:
                self.calls.append((tools, name, invocation, tool_wire(result)))
                self.write(
                    "native-tools.json",
                    [dict(name=n, invocation=tool_wire(i), result=r) for _, n, i, r in self.calls],
                )
            return result

        patch.object(Tools, "invoke", observed).start()
        self.original_invoke = original

    def write(self, name, value):
        (self.control / name).write_text(json.dumps(value, indent=2, default=str))

    def headroom(self):
        self.write("send-headroom.json", headroom())

    async def replay(self):
        database = ORIGINAL / "project/.proscenium/state.sqlite3"
        namespaces = ("jobs", "attempts", "versions", "items", "checkpoint_intents", "requests")
        before = {n: records(database, n) for n in namespaces}
        work = next(w for w in records(database, "work") if w["work_id"] == WORK_ID)
        assert work["state"] == "completed", work
        results = []
        assert {n for _, n, _, _ in self.calls} == {"checkpoint_sources", "publish_outputs"}
        # Native objects retain the exact tool_call_id, session, work and original epoch.
        for tools, name, invocation, result in self.calls:
            parsed = json.loads(result["text_result_for_llm"])
            assert not parsed.get("error"), parsed
            replayed = tool_wire(await self.original_invoke(tools, name, invocation))
            actual = json.loads(replayed["text_result_for_llm"])
            assert actual.pop("replayed") is True, actual
            expected = dict(parsed)
            expected.pop("replayed", None)
            assert actual == expected, (actual, expected)
            results.append(dict(name=name, invocation=tool_wire(invocation), result=replayed))
        after = {n: records(database, n) for n in namespaces}
        assert before == after, (
            "Original replay must not create/change source/catalogue/job records"
        )
        self.write(
            "original-authority-replay.json",
            dict(
                passed=True,
                results=results,
                records_unchanged=True,
                namespaces=list(namespaces),
            ),
        )

    def tick(self):
        if (self.control / "REPLAY").exists() and not self.replaying:
            self.replaying = True
            assert self.loop is not None
            try:
                asyncio.run_coroutine_threadsafe(self.replay(), self.loop).result(15)
            except Exception as error:
                self.write("replay-failure.json", dict(error=repr(error)))


if __name__ == "__main__":
    if sys.argv[1] == "preflight":
        print(json.dumps(preflight(), indent=2))
    else:
        main(Path(sys.argv[1]).resolve(), continuation_seconds=180, process_seconds=270, final=True)
