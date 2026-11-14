"""Continue local controls against an existing disposable W01 proof, with NO sends."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from production_live import RecordingNative, configured, wait_record
from proscenium.auth import Caller
from proscenium.copilot import install
from proscenium.copilot.service import Copilot
from proscenium.db.store import now

pytestmark = pytest.mark.native


def test_existing_history_stop_and_quiet_cold_resume():
    value = os.environ.get("W01_EXISTING_NATIVE_ROOT")
    if not value:
        pytest.skip("Requires an existing bounded W01 disposable proof")
    root = Path(value).resolve()
    assert str(root).startswith("/tmp/opencode/proscenium-w01-")

    async def run():
        services = configured(root)
        native = RecordingNative(services)
        runtime = install(services, native=native)
        native.runtime = runtime
        evidence = {"root_sends": 0, "recorded_at": now()}
        try:
            await runtime.start()
            assert not runtime.sessions
            evidence["initial_reconcile"] = await runtime.reconcile("w01-proof")
            with services.registry.project_store("w01-proof").transaction(write=False) as tx:
                original = tx.require("requests", "w01-native-shell")
                assert original["receipt"]["state"] == "completed"
            stop_id = "w01-native-local-stop"
            runtime.state.submit(
                Caller("owner", "w01-proof-owner"),
                dict(
                    schema_version="1.0",
                    request_id=stop_id,
                    project_id="w01-proof",
                    type="stop",
                    target_work_id=original["receipt"]["work_id"],
                ),
            )
            runtime.notify()
            evidence["stop"] = (await asyncio.wait_for(wait_record(runtime, stop_id), 119))[
                "receipt"
            ]
            assert evidence["stop"]["state"] == "completed"
            await runtime.close()
            other_native = RecordingNative(services)
            other = Copilot(services, native=other_native)
            other_native.runtime = other
            try:
                await other.start()
                assert not other.sessions
                evidence["cold_reconcile"] = await other.reconcile("w01-proof")
                assert evidence["cold_reconcile"]["pending_empty"]
            finally:
                await other.close()
            evidence["owned_processes_closed"] = True
        finally:
            await runtime.close()
            services.close()
            (root / "controls-evidence.json").write_text(json.dumps(evidence, indent=2))

    asyncio.run(run())
