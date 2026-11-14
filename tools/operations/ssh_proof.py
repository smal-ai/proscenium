"""One explicitly authorised, same-host SSH/product proof. Never installs services.

Fixture host/client keys, authentication data and logs stay in a fresh 0700 disk
root. Only a redacted report leaves it. No SSH agent or existing private key is used.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import pwd
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from .recovery import CHECKOUT, write_json
from .safety import RecoveryError, digest, private_directory


def identity(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return None if fields[0] in {"Z", "X", "x"} else fields[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def command_record(argv: list[str], root: Path) -> dict:
    redacted = [
        value.replace(str(root), "<PRIVATE_ROOT>").replace(str(CHECKOUT), "<CHECKOUT>")
        for value in argv
    ]
    return {
        "argv_redacted": redacted,
        "redacted_sha256": hashlib.sha256(json.dumps(redacted).encode()).hexdigest(),
    }


def sshd_configuration(root: Path, port: int, backend: int, user: str) -> str:
    return "\n".join(
        [
            f"Port {port}",
            "ListenAddress 127.0.0.1",
            f"HostKey {root}/host_key",
            f"PidFile {root}/sshd.pid",
            f"AuthorizedKeysFile {root}/authorized_keys",
            "StrictModes yes",
            "PubkeyAuthentication yes",
            "AuthenticationMethods publickey",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "UsePAM no",
            "PermitRootLogin no",
            f"AllowUsers {user}",
            "AllowTcpForwarding local",
            f"PermitOpen 127.0.0.1:{backend}",
            "GatewayPorts no",
            "PermitTTY no",
            "AllowAgentForwarding no",
            "X11Forwarding no",
            "PermitUserRC no",
            "HostbasedAuthentication no",
            "ForceCommand /usr/bin/false",
            "LogLevel ERROR",
            "LoginGraceTime 20",
            "MaxAuthTries 1",
            "",
        ]
    )


class Owned:
    def __init__(self, argv: list[str], root: Path, name: str):
        self.log = root / f"{name}-private.log"
        self.output = self.log.open("xb")
        self.log.chmod(0o600)
        self.process = subprocess.Popen(
            argv, cwd=CHECKOUT, stdout=self.output, stderr=self.output, start_new_session=True
        )
        self.start = identity(self.process.pid)
        self.name = name

    def close(self) -> dict:
        current = identity(self.process.pid)
        signalled = False
        if self.process.poll() is None:
            if not self.start or current != self.start:
                raise RecoveryError("owned_process_identity_changed")
            os.killpg(self.process.pid, signal.SIGTERM)
            signalled = True
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                if identity(self.process.pid) != self.start:
                    raise RecoveryError("owned_process_identity_changed")
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        self.output.close()
        return {
            "component": self.name,
            "pid_start_checked": True,
            "signalled_owned_group": signalled,
            "exit_code": self.process.returncode,
            "original_identity_gone": identity(self.process.pid) != self.start,
        }


def wait_ready(check, owned: Owned, *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if owned.process.poll() is not None:
            raise RecoveryError(f"{owned.name}_exited_before_ready")
        if check():
            return
        time.sleep(0.05)
    raise RecoveryError(f"{owned.name}_readiness_timeout")


def reachable(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def descendants(leaders: set[int]) -> dict[int, str]:
    observed = {}
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            fields = (process / "stat").read_text().rsplit(") ", 1)[1].split()
            if fields[0] not in {"Z", "X", "x"}:
                observed[int(process.name)] = (int(fields[1]), fields[19])
        except (OSError, IndexError, ValueError):
            continue
    result: dict[int, str] = {}
    parents = set(leaders)
    while True:
        children = {
            pid: start
            for pid, (parent, start) in observed.items()
            if parent in parents and pid not in parents
        }
        if not children:
            return result
        result.update(children)
        parents.update(children)


def run(root: Path, frontend: Path) -> dict:
    private_directory(root)
    processes = []
    commands: list[dict] = []
    report: dict[str, Any] = {
        "format": "proscenium-w10-ssh-proof-1",
        "status": "blocked",
        "transport": "actual_ssh_same_host_loopback",
        "inference": "forbidden",
        "application_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=CHECKOUT, text=True
        ).strip(),
        "commands": commands,
        "cleanup": [],
        "limits": [
            "Same-host loopback, not a physical remote browser or cumulative M12/M14 journey.",
            "Compiled UI pairing and scripted browser product API/media/WebSocket traffic, not native authoring.",
        ],
    }
    protected = identity(3327474)
    started = time.monotonic()
    try:
        if not (frontend / "index.html").is_file():
            raise RecoveryError("compiled_frontend_missing_run_npm_build")
        sshd = shutil.which("sshd") or "/usr/sbin/sshd"
        if not Path(sshd).is_file():
            raise RecoveryError("installed_sshd_unavailable_no_install_authorised")
        report["versions"] = {}
        for name, argv in (
            ("ssh", ["ssh", "-V"]),
            ("sshd", [sshd, "-V"]),
            ("node", ["node", "--version"]),
        ):
            probe = subprocess.run(argv, capture_output=True, timeout=5, text=True)
            report["versions"][name] = (probe.stdout + probe.stderr).strip()
        sockets = [socket.socket() for _ in range(3)]
        try:
            for connection in sockets:
                connection.bind(("127.0.0.1", 0))
            ssh_port, backend, forward = [connection.getsockname()[1] for connection in sockets]
        finally:
            for connection in sockets:
                connection.close()
        origin = f"http://127.0.0.1:{forward}"
        png = io.BytesIO()
        Image.new("RGB", (16, 16), (20, 40, 100)).save(png, "PNG")
        write_json(
            root / "allocation.json",
            {
                "origin": origin,
                "backend_port": backend,
                "frontend": str(frontend),
                "png_base64": base64.b64encode(png.getvalue()).decode(),
            },
        )
        report["allocation"] = {
            "origin": origin,
            "backend_bind": f"127.0.0.1:{backend}",
            "sshd_bind": f"127.0.0.1:{ssh_port}",
            "per_process_limit_seconds": 300,
            "existing_ssh": "operator_strict_known_host_probe_failed_no_trust_store_changes",
        }
        for name in ("host_key", "client_key"):
            argv = ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / name)]
            commands.append(command_record(argv, root))
            subprocess.run(
                argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10
            )
            (root / name).chmod(0o600)
        (root / "authorized_keys").write_bytes((root / "client_key.pub").read_bytes())
        (root / "known_hosts").write_text(
            f"[127.0.0.1]:{ssh_port} " + (root / "host_key.pub").read_text()
        )
        (root / "sshd_config").write_text(
            sshd_configuration(root, ssh_port, backend, pwd.getpwuid(os.getuid()).pw_name)
        )
        for path in root.iterdir():
            if path.is_file():
                path.chmod(0o600)
        check = [sshd, "-t", "-f", str(root / "sshd_config")]
        commands.append(command_record(check, root))
        verified = subprocess.run(check, capture_output=True, timeout=10)
        if verified.returncode:
            report["sshd_configuration_diagnostic"] = (
                verified.stderr.decode(errors="replace")
                .replace(str(root), "<PRIVATE_ROOT>")
                .strip()
            )
            raise RecoveryError("unprivileged_sshd_configuration_rejected")
        server = Owned(
            [sys.executable, "-m", "tools.operations.ssh_service", str(root)], root, "product"
        )
        processes.append(server)
        commands.append(
            command_record([sys.executable, "-m", "tools.operations.ssh_service", str(root)], root)
        )

        def healthy():
            try:
                return (
                    httpx.get(
                        f"http://127.0.0.1:{backend}/health",
                        headers={"Host": origin.removeprefix("http://")},
                        timeout=0.3,
                        trust_env=False,
                    ).status_code
                    == 200
                )
            except httpx.HTTPError:
                return False

        wait_ready(healthy, server)
        daemon_argv = [sshd, "-D", "-e", "-f", str(root / "sshd_config")]
        daemon = Owned(daemon_argv, root, "sshd")
        processes.append(daemon)
        commands.append(command_record(daemon_argv, root))
        wait_ready(lambda: reachable(ssh_port), daemon)
        client_argv = [
            "ssh",
            "-F",
            "/dev/null",
            "-N",
            "-T",
            "-p",
            str(ssh_port),
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
            f"127.0.0.1:{forward}:127.0.0.1:{backend}",
            f"{pwd.getpwuid(os.getuid()).pw_name}@127.0.0.1",
        ]
        client = Owned(client_argv, root, "ssh-client")
        processes.append(client)
        commands.append(command_record(client_argv, root))
        wait_ready(lambda: reachable(forward), client)
        # Host/Origin checks remain enforced through the actual SSH channel.
        with httpx.Client(base_url=origin, trust_env=False, timeout=10) as transport:
            assert transport.get("/health").status_code == 200
            assert (
                transport.get("/health", headers={"Origin": "http://wrong.invalid"}).status_code
                == 403
            )
            assert transport.get("/health", headers={"Host": "wrong.invalid"}).status_code == 403
        report["host_origin_checks"] = "correct_host_accepted_wrong_host_and_origin_rejected"
        browser_argv = ["node", str(CHECKOUT / "tools/operations/ssh_browser.mjs"), str(root)]
        browser = Owned(browser_argv, root, "browser")
        processes.append(browser)
        commands.append(command_record(browser_argv, root))
        if browser.process.wait(timeout=120):
            raise RecoveryError("browser_product_assertion_failed")
        report["browser"] = json.loads((root / "browser-result.json").read_text())
        report["versions"]["chromium"] = report["browser"]["browser"]
        report["source_hashes"] = {
            str(path.relative_to(CHECKOUT)): digest(path)
            for path in (
                Path(__file__),
                CHECKOUT / "tools/operations/ssh_service.py",
                CHECKOUT / "tools/operations/ssh_browser.mjs",
            )
        }
        report["status"] = "passed_bounded_ssh_product_transport"
    except (RecoveryError, OSError, subprocess.SubprocessError, AssertionError) as exc:
        report["blocker"] = str(exc) if isinstance(exc, RecoveryError) else type(exc).__name__
    finally:
        children = descendants({owned.process.pid for owned in processes})
        for process in reversed(processes):
            report["cleanup"].append(process.close())
        for pid, start in children.items():
            if identity(pid) == start:
                os.kill(
                    pid, signal.SIGTERM
                )  # Only an exact recorded descendant of our own allocation.
        deadline = time.monotonic() + 3
        while (
            any(identity(pid) == start for pid, start in children.items())
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        for pid, start in children.items():
            if identity(pid) == start:
                os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while (
            any(identity(pid) == start for pid, start in children.items())
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        report["owned_descendants_remaining"] = sum(
            identity(pid) == start for pid, start in children.items()
        )
        if report["owned_descendants_remaining"]:
            report["status"] = "failed_owned_cleanup"
        if (root / "execution-guards.json").exists():
            guards = json.loads((root / "execution-guards.json").read_text())
            report["execution_guards"] = guards
            if (
                any(guards["attempts"].values())
                or any(guards["records"].values())
                or not guards["gate_closed"]
            ):
                report["status"] = "failed_execution_guard"
            if report["status"] == "passed_bounded_ssh_product_transport" and not (
                any(
                    item.get("status") == 206 and item["exact_png_range"]
                    for item in guards["transport_audit"]
                )
                and sum(
                    item["type"] == "websocket" and item.get("status") == 101
                    for item in guards["transport_audit"]
                )
                >= 2
            ):
                report["status"] = "failed_backend_transport_audit"
        else:
            report["status"] = "blocked_missing_execution_guard_receipt"
            report.setdefault("blocker", "execution_guard_receipt_unavailable")
        report["protected_session"] = {
            "pid": 3327474,
            "not_signalled": True,
            "original_start_still_present": protected is not None
            and identity(3327474) == protected,
        }
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--frontend-dir", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--authorised-temporary-sshd", action="store_true", required=True)
    args = parser.parse_args()

    def expired(_signal, _frame):
        raise RecoveryError("allocation_deadline")

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(250)
    result = run(args.root.absolute(), args.frontend_dir.resolve(strict=True))
    write_json(args.evidence, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "blocker": result.get("blocker"),
                "evidence": str(args.evidence),
            }
        )
    )
    if result["status"] != "passed_bounded_ssh_product_transport":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
