"""Preserving doctor/start CLI for the installed Blender MCP authoring bridge."""

import argparse
import fcntl
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


def query(kind: str, params: dict | None = None, *, timeout: float = 5) -> dict:
    if kind not in {"ping", "get_addon_info", "get_scene_info", "execute_code"}:
        raise ValueError("Not a supported diagnostic")
    if kind == "execute_code" and params != {"code": STATE_CODE}:
        raise ValueError("Doctor accepts only its fixed read-only state inspection")
    end = time.monotonic() + timeout
    with socket.create_connection((HOST, PORT), timeout=timeout) as stream:
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


def occupied() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.25):
            return True
    except ConnectionRefusedError:
        return False
    # Other network errors are not proof that it is safe to start another bridge.


def doctor(project: Path) -> dict:
    if not occupied():
        return {"state": "absent", "bridge": f"{HOST}:{PORT}"}
    try:
        addon = query("get_addon_info")
        scene = query("get_scene_info")
        state = json.loads(query("execute_code", {"code": STATE_CODE})["result"])
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


def start(
    project: Path,
    *,
    executable: str = "blender",
    virtual_display: bool = False,
    timeout: float = 45,
) -> dict:
    destination = project / ".proscenium/tool-output/blender"
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    CLAIM_ROOT.mkdir(parents=True, exist_ok=True)
    with (CLAIM_ROOT / "blender-authoring.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = doctor(project)
        if status["state"] != "absent":
            return status
        binary = shutil.which(executable)
        if not binary:
            raise RuntimeError("Installed Blender is unavailable")
        argv = [binary]  # Real UI, saved installed add-on. Never factory startup here.
        if virtual_display or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            xvfb = shutil.which("xvfb-run")
            if not xvfb:
                raise RuntimeError("Headless authoring requires installed xvfb-run")
            argv = [xvfb, "-a", "-s", "-screen 0 1920x1080x24", *argv]
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
        record = {"pid": child.pid, "argv": argv, "project": str(project)}
        (destination / "launch.json").write_text(json.dumps(record, indent=2) + "\n")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if child.poll() is not None:
                return {"state": "failed", "native_exit_code": child.returncode, **record}
            if occupied():
                return {**doctor(project), "launched": record}
            time.sleep(0.1)  # Bridge readiness polling, not render throttling.
        return {"state": "outcome_uncertain", "preserved": True, "launched": record}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "start"))
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--virtual-display", action="store_true")
    parser.add_argument("--blender", default="blender")
    args = parser.parse_args()
    project = args.project.expanduser().resolve(strict=True)
    if not (project / "project.json").is_file():
        parser.error("The active project must have project.json")
    result = (
        doctor(project)
        if args.command == "doctor"
        else start(project, executable=args.blender, virtual_display=args.virtual_display)
    )
    print(json.dumps(result, indent=2))
    if result["state"] not in {"ready", "absent"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
