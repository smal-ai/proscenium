"""One 600-second SSH/message/media allocation. Never silently reruns a send."""
# ruff: noqa: E402 -- standalone entry point exposes maintained repository tools

import ctypes
import io
import json
import os
import pwd
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import httpx
from m14_ssh_prepare import preservation, sha
from PIL import Image

from tools.operations.recovery import write_json
from tools.operations.ssh_proof import (
    Owned,
    command_record,
    descendants,
    identity,
    reachable,
    sshd_configuration,
    wait_ready,
)


def rows(root, namespace):
    with sqlite3.connect(f"file:{root / 'p/.proscenium/state.sqlite3'}?mode=ro", uri=True) as db:
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM records WHERE namespace=? ORDER BY id", (namespace,)
            )
        ]


def wait_file(path, browser=None, timeout=30):
    end = time.monotonic() + timeout
    while not path.exists():
        if browser and browser.process.poll() is not None:
            raise RuntimeError("browser_exited_before_" + path.name)
        if time.monotonic() >= end:
            raise TimeoutError(path.name)
        time.sleep(0.02)
    return json.loads(path.read_text()) if path.suffix == ".json" else None


def preflight_evidence(path):
    """The create-only report writer does not create its parent directories."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    if not os.access(path.parent, os.W_OK):
        raise PermissionError(path.parent)


def process_receipt(root, name, pid, start):
    """One immutable receipt per owned process, never overwrite a previous receipt."""
    write_json(root / f"owned-{name}.json", {"name": name, "pid": pid, "start": start})


def run(root, evidence):
    os.umask(0o077)
    preflight_evidence(evidence)
    spec = json.loads((root / "allocation.json").read_text())
    assert not (root / "allocation-started.json").exists()
    assert not (root / "send-reservation.json").exists()
    available = (
        int(
            next(
                line.split()[1]
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            )
        )
        * 1024
    )
    assert available >= 4 * 1024**3, "No wait/resampling authorised"
    assert preservation() == json.loads((root / "original-before.json").read_text())
    assert (REPO / "apps/frontend/dist/index.html").is_file()
    cli = REPO / "node_modules/@github/copilot-linux-x64/copilot"
    assert sha(cli) == "463978cdea4ad6973b615f1ba07b7837634218632480ef06307ea844ee369a69"
    for port in [
        spec["ssh_port"],
        spec["backend_port"],
        spec["forward_port"],
        *spec["closed_ports"],
    ]:
        assert not reachable(port)
    # All preflight is complete before allocation. No service is started on failure.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import proscenium; import tools.operations.ssh_service; import copilot",
        ],
        check=True,
        timeout=5,
    )
    for name in ("host_key", "client_key"):
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / name)],
            check=True,
            timeout=10,
        )
    (root / "authorized_keys").write_bytes((root / "client_key.pub").read_bytes())
    (root / "known_hosts").write_text(
        f"[127.0.0.1]:{spec['ssh_port']} " + (root / "host_key.pub").read_text()
    )
    (root / "sshd_config").write_text(
        sshd_configuration(
            root, spec["ssh_port"], spec["backend_port"], pwd.getpwuid(os.getuid()).pw_name
        )
    )
    subprocess.run(["/usr/sbin/sshd", "-t", "-f", str(root / "sshd_config")], check=True, timeout=5)
    png = io.BytesIO()
    Image.new("RGB", (16, 16), (20, 40, 100)).save(png, "PNG")
    (root / "upload.png").write_bytes(png.getvalue())
    assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
    os.environ["PYTHONUNBUFFERED"] = "1"
    os.environ["TMPDIR"] = str(root / "t")
    started = time.monotonic()
    write_json(
        root / "allocation-started.json",
        {
            "ns": time.monotonic_ns(),
            "pid": os.getpid(),
            "start": identity(os.getpid()),
            "available_ram_bytes": available,
        },
    )
    processes, closed = [], set()
    result = {
        "status": "incomplete",
        "base": "904f73383d6e9a3e0a5daa411f3628efd90bc2cf",
        "driver_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "retained_root": str(root),
        "transport": "actual fixture-key OpenSSH same-host loopback",
        "available_ram_bytes_before_start": available,
        "allocation_seconds": 600,
        "sdk_send_limit": 1,
        "sdk_send_seconds": 180,
        "children_authoring_encodes_provider_calls": 0,
        "commands": [],
        "processes": [],
        "cleanup": [],
    }

    def spawn(name, argv):
        owned = Owned(argv, root, name)
        processes.append(owned)
        result["processes"].append({"name": name, "pid": owned.process.pid, "start": owned.start})
        result["commands"].append(command_record(argv, root))
        process_receipt(root, name, owned.process.pid, owned.start)
        return owned

    def close(owned):
        result["cleanup"].append(owned.close())
        closed.add(owned)

    def expired(_sig, _frame):
        raise TimeoutError("600_second_allocation")

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(580)  # Leave bounded cleanup inside the whole 600-second window.
    try:
        daemon = spawn("sshd", ["/usr/sbin/sshd", "-D", "-e", "-f", str(root / "sshd_config")])
        wait_ready(lambda: reachable(spec["ssh_port"]), daemon)
        host_argv = [
            sys.executable,
            "-u",
            str(REPO / "tests/native/studio/m14_ssh_host.py"),
            str(root),
        ]
        server = spawn("host-1", [*host_argv, "1"])
        wait_file(root / "ready-1.json")
        ssh_argv = [
            "ssh",
            "-F",
            "/dev/null",
            "-N",
            "-T",
            "-p",
            str(spec["ssh_port"]),
            "-i",
            str(root / "client_key"),
            "-o",
            "IdentityAgent=none",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={root}/known_hosts",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=2",
            "-L",
            f"127.0.0.1:{spec['forward_port']}:127.0.0.1:{spec['backend_port']}",
            f"{pwd.getpwuid(os.getuid()).pw_name}@127.0.0.1",
        ]
        client = spawn("ssh-1", ssh_argv)
        wait_ready(lambda: reachable(spec["forward_port"]), client)
        with httpx.Client(base_url=spec["origin"], trust_env=False, timeout=10) as transport:
            result["boundary"] = {
                "normal": transport.get("/health").status_code,
                "wrong_host": transport.get(
                    "/health", headers={"Host": "wrong.invalid"}
                ).status_code,
                "wrong_origin": transport.get(
                    "/health", headers={"Origin": "http://wrong.invalid"}
                ).status_code,
            }
            assert result["boundary"] == {"normal": 200, "wrong_host": 403, "wrong_origin": 403}
        browser = spawn(
            "browser", ["node", str(REPO / "tests/native/studio/m14_ssh_browser.mjs"), str(root)]
        )
        admission = wait_file(root / "admitted.json", browser, 80)
        wait_file(root / "browser-offline", browser, 15)
        prior = rows(root, "requests")
        assert len(prior) == 1
        before_cut = time.monotonic_ns()
        close(client)
        after_cut = time.monotonic_ns()
        current = rows(root, "requests")[0]
        result["cut"] = {
            "admission": admission,
            "before_cut_ns": before_cut,
            "after_cut_ns": after_cut,
            "receipt_before": prior[0]["receipt"],
            "receipt_after": current["receipt"],
            "forward_closed": not reachable(spec["forward_port"]),
            "browser_offline_observed": True,
            "active_at_cut": current["receipt"]["state"]
            in {"dispatching", "acknowledged", "running"},
        }
        completed = wait_file(root / "completed.json", browser, 180)
        result["completion_disconnected"] = {
            "observed_ns": completed["observed_ns"],
            "receipt": completed["request"]["receipt"],
            "forward_closed_at_completion_observation": not reachable(spec["forward_port"]),
        }
        assert completed["request"]["receipt"]["state"] == "completed"
        client2 = spawn("ssh-2", ssh_argv)
        wait_ready(lambda: reachable(spec["forward_port"]), client2)
        (root / "reconnected").write_text("same origin, second and final forwarding client")
        wait_file(root / "review-complete", browser, 100)
        close(server)
        wait_file(root / "host-1-audit.json")
        result["closed_endpoints_observed"] = [
            {"port": port, "tcp_closed": not reachable(port)} for port in spec["closed_ports"]
        ]
        assert all(r["tcp_closed"] for r in result["closed_endpoints_observed"])
        assert not Path(
            json.loads((root / "unavailable.json").read_text())["runtime"]["cli_path"]
        ).exists()
        server2 = spawn("host-2", [*host_argv, "2"])
        wait_file(root / "ready-2.json")
        (root / "restarted").write_text("quiet unavailable configuration restart")
        assert browser.process.wait(timeout=100) == 0
        close(server2)
        result["browser"] = json.loads((root / "browser-result.json").read_text())
        assert result["browser"]["status"] == "passed"
        result["status"] = (
            "passed" if result["cut"]["active_at_cut"] else "partial_task_not_active_at_cut"
        )
    except Exception as error:
        result["failure"] = {"type": type(error).__name__, "detail": str(error)[:200]}
    finally:
        signal.alarm(0)
        children = descendants({p.process.pid for p in processes})
        for owned in reversed(processes):
            if owned not in closed:
                close(owned)
        # Subreaper: only our own kernel children, each checked by PID/start.
        for attempt in range(3):
            children.update(descendants({os.getpid()}))
            for pid, start in children.items():
                if identity(pid) == start:
                    os.kill(pid, signal.SIGTERM if attempt == 0 else signal.SIGKILL)
            time.sleep(0.2)
            while True:
                try:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                    if not pid:
                        break
                except ChildProcessError:
                    break
        result["owned_descendants_remaining"] = descendants({os.getpid()})
        for generation in ("1", "2"):
            path = root / f"host-{generation}-audit.json"
            if path.exists():
                audit = json.loads(path.read_text())
                result[f"host_{generation}"] = audit
            else:
                result[f"host_{generation}_audit_missing"] = True
        result["original_m13_byte_preserved"] = preservation() == json.loads(
            (root / "original-before.json").read_text()
        )
        result["original_inventory_sha256"] = sha(root / "original-before.json")
        result["import"] = json.loads((root / "import.json").read_text())
        result["versions"] = {
            "ssh": subprocess.run(
                ["ssh", "-V"], capture_output=True, text=True, timeout=5
            ).stderr.strip(),
            "node": subprocess.check_output(["node", "--version"], text=True, timeout=5).strip(),
            "cli_package": json.loads(
                (REPO / "node_modules/@github/copilot/package.json").read_text()
            )["version"],
            "cli_binary_sha256": sha(cli),
        }
        if (root / "browser-result.json").exists():
            result["browser"] = json.loads((root / "browser-result.json").read_text())
        if (root / "send-reservation.json").exists():
            reservation = json.loads((root / "send-reservation.json").read_text())
            result["send_reservation"] = reservation
            native = (
                root
                / "d/copilot-runtime/home/session-state"
                / reservation["session_id"]
                / "events.jsonl"
            )
            facts = {
                "user_messages": [],
                "assistant_messages": [],
                "tool_calls": [],
                "children": [],
            }
            if native.exists():
                for line in native.read_text().splitlines():
                    event = json.loads(line)
                    kind, data = event.get("type"), event.get("data", {})
                    if kind == "user.message":
                        facts["user_messages"].append(
                            {
                                "event_id": event["id"],
                                "message_id": data.get("messageId"),
                                "client_turn_id": data.get("clientTurnId"),
                                "prompt_matches": data.get("content") == spec["prompt"],
                            }
                        )
                    elif kind == "assistant.message" and data.get("content"):
                        facts["assistant_messages"].append(
                            {
                                "event_id": event["id"],
                                "message_id": data.get("messageId"),
                                "content": data["content"],
                            }
                        )
                    elif kind == "tool.execution_start":
                        facts["tool_calls"].append(
                            {
                                "event_id": event["id"],
                                "name": data.get("toolName"),
                                "call_id": data.get("toolCallId"),
                            }
                        )
                    elif kind and kind.startswith("subagent."):
                        facts["children"].append({"event_id": event["id"], "type": kind})
            result["native_facts"] = facts
        if result["status"] == "passed":
            h1, h2 = result.get("host_1", {}), result.get("host_2", {})
            checks = {
                "one_sdk_send": h1.get("sends") == 1 and h2.get("sends") == 0,
                "zero_forbidden_attempts": h1.get("forbidden") == [] and h2.get("forbidden") == [],
                "one_native_user_message": len(result["native_facts"]["user_messages"]) == 1
                and result["native_facts"]["user_messages"][0]["prompt_matches"],
                "one_native_response": len(result["native_facts"]["assistant_messages"]) == 1,
                "zero_children": not result["native_facts"]["children"],
                "only_read_only_view": all(
                    r["name"] == "view" for r in result["native_facts"]["tool_calls"]
                ),
                "native_processes_exited": all(h1.get("native_identities_gone", [False])),
                "restart_did_not_open_sdk": h2.get("native_processes") == []
                and h2.get("options") == [],
                "restart_gate_closed": h2.get("gate_closed") is True,
                "context_exact_after_restart": h1.get("records", {}).get("contexts")
                == h2.get("records", {}).get("contexts"),
                "requests_exact_after_restart": h1.get("records", {}).get("requests")
                == h2.get("records", {}).get("requests"),
                "original_m13_preserved": result["original_m13_byte_preserved"],
                "owned_cleanup": not result["owned_descendants_remaining"],
            }
            context = h1["records"]["contexts"][0]
            response_text = "\n".join(
                m["content"] for m in result["native_facts"]["assistant_messages"]
            )
            checks["model_read_marker_and_context"] = all(
                value in response_text
                for value in (
                    "M14_SSH_READ_ONLY_20261114",
                    context["context_id"],
                    "m13-film-v1",
                    "animation",
                )
            )
            result["checks"] = checks
            if not all(checks.values()):
                result["status"] = "partial_failed_final_oracle"
        result["source_hashes"] = {
            str(p.relative_to(REPO)): sha(p)
            for p in (REPO / "tests/native/studio").glob("m14_ssh*")
            if p.is_file()
        }
        result["frontend_index_sha256"] = sha(REPO / "apps/frontend/dist/index.html")
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["limits"] = [
            "Same-host actual SSH, not physical remote-client acceptance.",
            "Imported real selected M13 film, no new rendering or authoring.",
            "Configured unavailable Copilot and closed owned endpoint ports, not stopped shared services.",
            "Cumulative M12/M14 and release acceptance remain coordinator-owned.",
        ]
        write_json(root / "result-private.json", result)
        # Pairing/cookies/credentials are never selected into this report.
        write_json(evidence, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "failure": result.get("failure"),
                "seconds": result["elapsed_seconds"],
            }
        )
    )
    return result


if __name__ == "__main__":
    value = run(Path(sys.argv[1]).absolute(), Path(sys.argv[2]))
    raise SystemExit(0 if value["status"] == "passed" else 2)
