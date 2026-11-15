"""Preserving doctor/start CLI for the installed Blender MCP authoring bridge."""

import argparse
import fcntl
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

HOST, PORT = "127.0.0.1", 9876
CLAIM_ROOT = Path("/tmp/opencode/proscenium-native-claims")
STATE_CODE = (
    "import bpy,json\nprint(json.dumps({'filepath':bpy.data.filepath,"
    "'is_dirty':bpy.data.is_dirty,'version':bpy.app.version_string,"
    "'build_hash':bpy.app.build_hash.decode()}))"
)


def endpoint(host: str, port: int) -> tuple[str, int]:
    if not ipaddress.ip_address(host).is_loopback or not 1 <= port <= 65535:
        raise ValueError("Bridge requires a numeric loopback address and valid port")
    return host, port


def query(
    kind: str, params: dict | None = None, *, timeout: float = 5, host: str = HOST, port: int = PORT
) -> dict:
    if kind not in {"ping", "get_addon_info", "get_scene_info", "execute_code"}:
        raise ValueError("Not a supported diagnostic")
    if kind == "execute_code" and params != {"code": STATE_CODE}:
        raise ValueError("Doctor accepts only its fixed read-only state inspection")
    end = time.monotonic() + timeout
    with socket.create_connection(endpoint(host, port), timeout=timeout) as stream:
        stream.sendall(json.dumps({"type": kind, "params": params or {}}).encode())
        data = bytearray()
        while len(data) <= 2**20:
            stream.settimeout(max(0.001, end - time.monotonic()))
            chunk = stream.recv(min(65536, 2**20 + 1 - len(data)))
            if not chunk:
                raise ConnectionError("Bridge closed an incomplete diagnostic")
            data.extend(chunk)
            try:
                result = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                if time.monotonic() >= end:
                    raise TimeoutError("Bridge diagnostic timed out") from None
                continue
            if result.get("status") != "success":
                raise RuntimeError("Occupied bridge failed its diagnostic")
            return dict(result["result"])
    raise ValueError("Oversized bridge response")


def occupied(*, host: str = HOST, port: int = PORT) -> bool:
    try:
        with socket.create_connection(endpoint(host, port), timeout=0.25):
            return True
    except ConnectionRefusedError:
        return False
    # Other network errors are not proof that it is safe to start another bridge.


def doctor(project: Path, *, host: str = HOST, port: int = PORT) -> dict:
    if not occupied(host=host, port=port):
        return {"state": "absent", "bridge": f"{host}:{port}"}
    try:
        addon = query("get_addon_info", host=host, port=port)
        scene = query("get_scene_info", host=host, port=port)
        state = json.loads(
            query("execute_code", {"code": STATE_CODE}, host=host, port=port)["result"]
        )
    except Exception as exc:
        return {"state": "occupied_unverified", "preserved": True, "error": type(exc).__name__}
    filepath = state["filepath"]
    inside = bool(filepath) and Path(filepath).resolve().is_relative_to(project)
    decision = bool(state["is_dirty"] or filepath and not inside)
    return {
        "state": "decision_required" if decision else "ready",
        "preserved": True,
        "native": state,
        "addon": addon,
        "scene": scene,
        "mutation_allowed": inside and not state["is_dirty"],
        "reason": "Preserve dirty/outside-project authoring state" if decision else None,
    }


def persist(path: Path, record: dict) -> None:
    temporary = path.with_suffix(".pending")
    with temporary.open("w") as stream:
        json.dump(record, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def start(
    project: Path,
    *,
    executable: str = "blender",
    virtual_display: bool = False,
    timeout: float = 45,
    host: str = HOST,
    port: int = PORT,
    isolated: bool = False,
) -> dict:
    endpoint(host, port)
    if (host, port) != (HOST, PORT) and not isolated:
        raise ValueError("A custom bridge endpoint requires --isolated factory startup")
    destination = project / ".proscenium/tool-output/blender"
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    CLAIM_ROOT.mkdir(parents=True, exist_ok=True)
    with (CLAIM_ROOT / f"blender-authoring-{host}-{port}.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = doctor(project, host=host, port=port)
        if status["state"] != "absent":
            return status
        receipt = destination / "launch.json"
        if receipt.exists():
            previous = json.loads(receipt.read_text())
            # A missing socket does not prove a previous launch had no native effects.
            return {"state": "outcome_uncertain", "preserved": True, "launched": previous}
        claim = CLAIM_ROOT / f"blender-authoring-{host}-{port}.json"
        if claim.exists():
            return {
                "state": "outcome_uncertain",
                "preserved": True,
                "launched": json.loads(claim.read_text()),
            }
        binary = shutil.which(executable)
        if not binary:
            raise RuntimeError("Installed Blender is unavailable")
        argv = [binary]  # Real UI, saved installed add-on. Never factory startup here.
        if isolated:
            bootstrap = destination / "isolated-bootstrap.py"
            bootstrap.write_text(
                "import bpy, addon_utils\n"
                "addon_utils.enable('blender_mcp', default_set=False, persistent=False)\n"
                "import blender_mcp\n"
                f"bpy.context.scene.blendermcp_port = {port!r}\n"
                "bpy.context.scene.blendermcp_auto_start_server = True\n"
                f"bpy.types.blendermcp_server = blender_mcp.BlenderMCPServer(host={host!r}, port={port!r})\n"
                "bpy.types.blendermcp_server.start()\n"
            )
            argv += ["--factory-startup", "--disable-autoexec", "--python", str(bootstrap)]
        if virtual_display or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            xvfb = shutil.which("xvfb-run")
            if not xvfb:
                raise RuntimeError("Headless authoring requires installed xvfb-run")
            argv = [xvfb, "-a", "-s", "-screen 0 1920x1080x24", *argv]
        prepared = {
            "state": "launch_prepared",
            "argv": argv,
            "project": str(project),
            "host": host,
            "port": port,
            "isolated": isolated,
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        }
        # Fence the spawn-before-acknowledgement window, including another project
        # targeting this endpoint. Neither missing socket nor PID permits retry.
        persist(claim, prepared)
        persist(receipt, prepared)
        with (destination / "authoring.log").open("ab") as log:
            child = subprocess.Popen(
                argv,
                cwd=project,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "DISABLE_TELEMETRY": "1"},
            )
        record = {
            **prepared,
            "state": "launched",
            "pid": child.pid,
            "argv": argv,
            "project": str(project),
            "process_start": Path(f"/proc/{child.pid}/stat").read_text().split(") ")[1].split()[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "host": host,
            "port": port,
            "isolated": isolated,
        }
        persist(claim, record)
        persist(receipt, record)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if child.poll() is not None:
                return {"state": "failed", "native_exit_code": child.returncode, **record}
            if occupied(host=host, port=port):
                return {**doctor(project, host=host, port=port), "launched": record}
            time.sleep(0.1)  # Bridge readiness polling, not render throttling.
        return {"state": "outcome_uncertain", "preserved": True, "launched": record}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "start"))
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--virtual-display", action="store_true")
    parser.add_argument("--blender", default="blender")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="Factory startup; enable installed add-on in memory only",
    )
    args = parser.parse_args()
    project = args.project.expanduser().resolve(strict=True)
    if not (project / "project.json").is_file():
        parser.error("The active project must have project.json")
    result = (
        doctor(project, host=args.host, port=args.port)
        if args.command == "doctor"
        else start(
            project,
            executable=args.blender,
            virtual_display=args.virtual_display,
            host=args.host,
            port=args.port,
            isolated=args.isolated,
        )
    )
    print(json.dumps(result, indent=2))
    if result["state"] not in {"ready", "absent"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
