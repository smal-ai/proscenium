"""Whole driver flow with harmless owned processes, then one real blocked API.

No SSH daemon/client/key generation, browser, SDK construction or provider call.
Synthetic flow observations remain labelled and cannot establish product acceptance.
"""
# ruff: noqa: E402 -- standalone entry point exposes maintained repository tools

import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import httpx
import m14_ssh_run as driver
from m14_ssh_prepare import prepare, sha

from tools.operations.recovery import write_json
from tools.operations.ssh_proof import Owned, identity


def synthetic_flow(root, *, partial=False):
    prepare(root)
    processes = []
    real_run = subprocess.run

    def no_ssh(command, *args, **kwargs):
        if command[0] == "ssh-keygen":
            path = Path(command[-1])
            path.write_text("SYNTHETIC NOT A KEY")
            path.with_suffix(".pub").write_text("SYNTHETIC NOT A KEY")
            return SimpleNamespace(returncode=0)
        if command[0] == "/usr/sbin/sshd":
            return SimpleNamespace(returncode=0)
        return real_run(command, *args, **kwargs)

    class Harmless(Owned):
        def __init__(self, _command, owned_root, name):
            if partial and name == "host-1":
                raise RuntimeError("synthetic partial startup fault before second spawn")
            # Real PID/start/group cleanup, but only a harmless Python sleep fixture.
            super().__init__(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    "import time; time.sleep(0.7)"
                    if name == "browser"
                    else "import time; time.sleep(20)",
                ],
                owned_root,
                name,
            )
            processes.append({"name": name, "pid": self.process.pid, "start": self.start})
            if name in {"host-1", "host-2"}:
                generation = name[-1]
                write_json(root / f"ready-{generation}.json", {"synthetic": True})
                write_json(
                    root / f"host-{generation}-audit.json",
                    {
                        "synthetic": True,
                        "sends": 0,
                        "forbidden": [],
                        "native_processes": [],
                        "options": [],
                        "native_identities_gone": [],
                        "gate_closed": True,
                        "records": {
                            "contexts": [{"context_id": "synthetic-context"}],
                            "requests": [],
                        },
                    },
                )
            if name == "browser":
                write_json(root / "admitted.json", {"synthetic": True})
                (root / "browser-offline").write_text("synthetic")
                (root / "review-complete").write_text("synthetic")
                write_json(
                    root / "completed.json",
                    {
                        "synthetic": True,
                        "observed_ns": time.monotonic_ns(),
                        "request": {"receipt": {"state": "completed"}},
                    },
                )
                write_json(root / "browser-result.json", {"synthetic": True, "status": "passed"})
                write_json(
                    root / "send-reservation.json",
                    {"synthetic": True, "session_id": "synthetic", "count": 0},
                )
                native = root / "d/copilot-runtime/home/session-state/synthetic/events.jsonl"
                native.parent.mkdir(parents=True, mode=0o700)
                native.write_text("")

    class Boundary:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, path, headers=None):
            return SimpleNamespace(status_code=403 if headers else 200)

    output = root / "new-parent/evidence/synthetic-flow.json"
    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "Owned", Harmless))
        stack.enter_context(patch.object(driver, "reachable", lambda port: False))
        stack.enter_context(patch.object(driver, "wait_ready", lambda check, owned: None))
        stack.enter_context(
            patch.object(driver, "rows", lambda root, ns: [{"receipt": {"state": "running"}}])
        )
        stack.enter_context(patch.object(driver.httpx, "Client", Boundary))
        stack.enter_context(patch.object(driver.subprocess, "run", no_ssh))
        result = driver.run(root, output)
    assert not result["owned_descendants_remaining"]
    assert all(identity(row["pid"]) != row["start"] for row in processes)
    if partial:
        assert result["failure"]["detail"].startswith("synthetic partial startup fault")
        assert [p["name"] for p in processes] == ["sshd"]
    else:
        assert [p["name"] for p in processes] == [
            "sshd",
            "host-1",
            "ssh-1",
            "browser",
            "ssh-2",
            "host-2",
        ]
        assert len(list(root.glob("owned-*.json"))) == 6
        assert (
            result["status"] == "partial_failed_final_oracle"
        )  # Zero real sends cannot pass the product oracle.
        assert result["host_1"]["synthetic"] and result["host_2"]["synthetic"]
    return {
        "scope": "synthetic full driver control flow, not product acceptance",
        "partial_startup": partial,
        "processes": processes,
        "all_original_identities_gone": True,
        "raw_report_sha256": sha(output),
        "output_parent_created": output.parent.is_dir(),
        "original_m13_preserved": result["original_m13_byte_preserved"],
    }


def actual_blocked_host(root):
    prepare(root)
    spec = json.loads((root / "allocation.json").read_text())
    started = time.monotonic()
    host = Owned(
        [
            sys.executable,
            "-u",
            str(REPO / "tests/native/studio/m14_ssh_host.py"),
            str(root),
            "preflight",
        ],
        root,
        "actual-blocked-host",
    )
    driver.process_receipt(root, "actual-blocked-host", host.process.pid, host.start)
    paths = []
    try:
        driver.wait_file(root / "ready-preflight.json", host, timeout=15)
        code = json.loads((root / "pairing-private.json").read_text())["code"]
        with httpx.Client(
            base_url=f"http://127.0.0.1:{spec['backend_port']}",
            trust_env=False,
            headers={"Host": spec["origin"].removeprefix("http://"), "Origin": spec["origin"]},
            timeout=5,
        ) as client:
            assert client.get("/health").status_code == 200
            operation = str(uuid4())
            paired = client.post(
                "/api/v1/owner/session",
                json={
                    "schema_version": "1.0",
                    "operation_id": operation,
                    "client_nonce": str(uuid4()),
                    "pairing_code": code,
                },
                headers={"Idempotency-Key": operation},
            )
            assert paired.status_code == 201
            for path in (
                "/api/v1/configuration",
                "/api/v1/projects",
                f"/api/v1/projects/{spec['project_id']}/snapshot",
                f"/api/v1/projects/{spec['project_id']}/requests",
                f"/api/v1/projects/{spec['project_id']}/items",
                f"/api/v1/projects/{spec['project_id']}/versions/m13-film-v1/sequence-map",
            ):
                response = client.get(path)
                paths.append({"path": path, "status": response.status_code})
                assert response.status_code == 200, (path, response.status_code)
    finally:
        cleanup = host.close()
    audit = driver.wait_file(root / "host-preflight-audit.json", timeout=3)
    assert not audit["forbidden"] and audit["sends"] == 0 and not audit["native_processes"]
    assert audit["gate_closed"] and all(not values for values in audit["records"].values())
    assert time.monotonic() - started < 30
    return {
        "scope": "actual composed API, read-only project/config/health, all SDK/provider/native effects blocked",
        "seconds": round(time.monotonic() - started, 3),
        "pid": host.process.pid,
        "start": host.start,
        "paths": paths,
        "cleanup": cleanup,
        "lifespan_audit_sha256": sha(root / "host-preflight-audit.json"),
        "sends": audit["sends"],
        "forbidden_attempts": audit["forbidden"],
        "gate_closed": audit["gate_closed"],
    }


if __name__ == "__main__":
    os.umask(0o077)
    base = Path(sys.argv[1])
    report = {
        "synthetic_full_flow": synthetic_flow(base.with_name(base.name + "-flow")),
        "synthetic_partial_startup": synthetic_flow(
            base.with_name(base.name + "-partial"), partial=True
        ),
        "actual_blocked_api": actual_blocked_host(base.with_name(base.name + "-host")),
    }
    write_json(Path(sys.argv[2]), report)
    print(json.dumps({"status": "whole_controlflow_and_actual_blocked_api_preflight_passed"}))
