"""One separately reserved root. Exact native permission/input IDs, no children."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from production_live import RecordingNative, configured, reserve, wait_record
from proscenium.auth import Caller
from proscenium.copilot import install
from proscenium.db.store import now

pytestmark = pytest.mark.native


def test_native_root_permission_input_sqlite_before_reply():
    value = os.environ.get("W01_DECISION_NATIVE_ROOT")
    if not value:
        pytest.skip("Requires one explicitly reserved W01 root with no children")
    root = Path(value).resolve()
    assert str(root).startswith("/tmp/opencode/proscenium-w01-")
    root.mkdir(exist_ok=True)
    minimal = os.environ.get("W01_DECISION_MINIMAL") == "1"
    request_id = "w01-native-decision-minimal" if minimal else "w01-native-decision"

    async def run():
        services = configured(root)
        native = RecordingNative(services)
        runtime = install(services, native=native)
        native.runtime = runtime
        owner = Caller("owner", "w01-fixture-owner")
        evidence = {"recorded_at": now(), "root_sends": 1, "children": 0}
        try:
            await runtime.start()
            runtime.contexts.capture(
                dict(
                    schema_version="1.0",
                    context_id="w01-decision-context",
                    project_id="w01-proof",
                    captured_at=now(),
                    item_id=None,
                    version_id=None,
                    view_mode="none",
                    target=None,
                    camera=None,
                    range=None,
                    selected_objects=[],
                    attachments=[],
                    annotations=[],
                    approved_findings=[],
                )
            )
            reserve(root, request_id)
            prompt = (
                "Use ask_user to ask 'Red or blue?' with choices red and blue. Wait for the owner's answer, then reply with that colour and finish. No other tools or children."
                if minimal
                else "Bounded integration check. First call record_w01 with the exact context_id from host context. Then use ask_user to ask 'Choose red or blue for this bounded test' with choices red and blue. Do not answer yourself. After the owner replies, run only pwd with bash and finish with the selected colour and directory. No files, no children or other tools."
            )
            runtime.state.submit(
                owner,
                dict(
                    schema_version="1.0",
                    request_id=request_id,
                    project_id="w01-proof",
                    type="queue",
                    text=prompt,
                    context_id="w01-decision-context",
                ),
            )
            runtime.notify()

            async def answer_owner():
                for _ in range(1100):
                    with services.registry.project_store("w01-proof").transaction(
                        write=False
                    ) as tx:
                        decisions = [d for d in tx.all("decisions") if d["state"] == "pending"]
                    for decision in decisions:
                        assert decision["kind"] == "user_input"
                        runtime.state.answer(
                            owner,
                            decision["decision_id"],
                            dict(
                                schema_version="1.0",
                                operation_id="w01-owner-answer",
                                project_id="w01-proof",
                                expected_revision=decision["state_revision"],
                                fingerprint=decision["fingerprint"],
                                action="answer",
                                answer="red",
                            ),
                        )
                        return
                    await asyncio.sleep(0.1)
                raise TimeoutError("No native question appeared")

            answer_task = asyncio.create_task(answer_owner())
            try:
                result = await asyncio.wait_for(wait_record(runtime, request_id), 119)
                evidence["request"] = result["receipt"]
                with services.registry.project_store("w01-proof").transaction(write=False) as tx:
                    evidence["decisions"] = tx.all("decisions")
                    evidence["permissions"] = tx.all("copilot.permissions")
                    evidence["recordings"] = tx.all("copilot.proof")
                assert result["receipt"]["state"] == "completed"
                assert answer_task.done() and answer_task.exception() is None
                assert len(evidence["decisions"]) == 1
                assert evidence["decisions"][0]["delivery_state"] == "acknowledged"
                assert evidence["decisions"][0]["invocation_id"]
                if not minimal:
                    assert evidence["permissions"] and all(
                        p["native_id"] for p in evidence["permissions"]
                    )
            finally:
                answer_task.cancel()
                await asyncio.gather(answer_task, return_exceptions=True)
        finally:
            await runtime.close()
            services.close()
            evidence["owned_runtime_closed"] = True
            (root / "decision-evidence.json").write_text(json.dumps(evidence, indent=2))

    asyncio.run(run())
