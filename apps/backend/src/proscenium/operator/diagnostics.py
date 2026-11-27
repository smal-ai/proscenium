"""Read-only runtime and workstation probes."""

import json
import shutil
import stat
import subprocess
from collections.abc import Callable
from itertools import chain
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit

import httpx

from proscenium.configuration import APP_ROOT, Settings, default_config_path
from proscenium.control import (
    ControlProtocolError,
    ControlUnavailableError,
    control_request,
    control_socket_path,
    validate_control_directory,
)
from proscenium.operator import OperatorError, load_config
from proscenium.operator.service import SYSTEMCTL, Runner

_RUNTIME_FIELDS = (
    "pid",
    "origin",
    "data_directory",
    "active_project_id",
    "owner_epoch",
    "frontend_ready",
)
_UNIT_PROPERTIES = ("LoadState", "ActiveState", "SubState", "MainPID", "FragmentPath")
_COMFY_CAPABILITIES = {"comfy", "image", "image_to_3d", "audio", "video"}
HttpProbe = Callable[[str], int]
ToolProbe = Callable[[str], str | None]


def http_status(url: str) -> int:
    # No proxy/environment credentials, redirects, response bodies or provider clients.
    with httpx.stream("GET", url, timeout=2.0, follow_redirects=False, trust_env=False) as reply:
        return reply.status_code


def _provider_url(endpoint: str, capabilities: list[str], provider_id: str) -> str:
    uri = urlsplit(endpoint)
    if (
        uri.scheme not in {"http", "https"}
        or not uri.hostname
        or uri.username is not None
        or uri.password is not None
        or uri.query
        or uri.fragment
    ):
        raise ValueError("invalid provider endpoint")
    if _COMFY_CAPABILITIES.intersection(capabilities) or "comfy" in provider_id.casefold():
        path = uri.path.rstrip("/") + "/system_stats"
    elif (
        "vlm" in capabilities
        or "openai" in capabilities
        or "openai" in provider_id.casefold()
        or uri.path.rstrip("/").endswith("/v1")
    ):
        base = uri.path.rstrip("/")
        path = base + ("/models" if base.endswith("/v1") else "/v1/models")
    else:
        path = uri.path or "/"
    return uri._replace(path=path).geturl()


def _provider_status(settings: Settings, probe: HttpProbe) -> list[dict[str, Any]]:
    rows = []
    for service in settings.services:
        endpoint, capabilities = service["endpoint"], service["capabilities"]
        provider_id = service["provider_id"]
        row: dict[str, Any] = dict(
            provider_id=provider_id,
            endpoint=endpoint,
            capabilities=capabilities,
            reachable=False,
        )
        try:
            code = probe(_provider_url(endpoint, capabilities, provider_id))
            row.update(status_code=code, reachable=200 <= code < 300)
        except (httpx.TimeoutException, TimeoutError):
            row["error"] = "Probe timed out"
        except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError):
            row["error"] = "Endpoint unavailable or invalid"
        rows.append(row)
    return rows


def _runtime_status(settings: Settings) -> dict[str, Any]:
    try:
        reply = control_request(control_socket_path(settings), {"action": "status"})
    except ControlUnavailableError:
        return {"running": False, "reason": "Control socket unavailable"}
    except (ControlProtocolError, OSError):
        return {"running": False, "reason": "Control socket could not be queried safely"}
    if reply.get("ok") is not True or not all(key in reply for key in _RUNTIME_FIELDS):
        return {"running": False, "reason": "Runtime status unavailable"}
    return {"running": True, **{key: reply[key] for key in _RUNTIME_FIELDS}}


def _systemd_status(unit: str, runner: Runner) -> dict[str, Any]:
    try:
        reply = runner(
            [*SYSTEMCTL, "show", "-p", ",".join(_UNIT_PROPERTIES), "--", unit],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except FileNotFoundError:
        return {"unit": unit, "available": False, "reason": "systemctl not found"}
    except (OSError, subprocess.SubprocessError):
        return {"unit": unit, "available": False, "reason": "Could not query systemd user unit"}
    if reply.returncode:
        return {"unit": unit, "available": False, "reason": "Systemd user unit query failed"}
    properties = dict(
        line.split("=", 1) for line in (reply.stdout or "").splitlines() if "=" in line
    )
    return {
        "unit": unit,
        "available": True,
        **{key: properties.get(key) for key in _UNIT_PROPERTIES},
    }


def status(
    settings: Settings, unit: str, *, as_json: bool, runner: Runner, probe: HttpProbe
) -> int:
    runtime = _runtime_status(settings)
    systemd = _systemd_status(unit, runner)
    providers = _provider_status(settings, probe)
    if as_json:
        print(json.dumps({"runtime": runtime, "systemd": systemd, "services": providers}))
        return 0
    if runtime["running"]:
        print(f"Runtime: running (PID {runtime['pid']}) at {runtime['origin']}")
        print(f"  Data: {runtime['data_directory']}")
        print(
            f"  Active project: {runtime['active_project_id'] or 'none'} (owner epoch {runtime['owner_epoch']})"
        )
        print(f"  Frontend: {'ready' if runtime['frontend_ready'] else 'not ready'}")
    else:
        print(f"Runtime: not running or unreachable ({runtime['reason']})")
    if systemd["available"]:
        print(
            f"Systemd: {unit} {systemd['LoadState']}/{systemd['ActiveState']}/{systemd['SubState']}"
        )
        print(
            f"  Main PID: {systemd['MainPID'] or 'none'}; unit file: {systemd['FragmentPath'] or 'none'}"
        )
    else:
        print(f"Systemd: {unit} unavailable ({systemd['reason']})")
    for provider in providers:
        caps = ", ".join(provider["capabilities"]) or "no capabilities"
        detail = (
            f"HTTP {provider['status_code']}" if "status_code" in provider else provider["error"]
        )
        print(f"  {provider['provider_id']} ({caps}) {provider['endpoint']}: {detail}")
    if not providers:
        print("Independent services: none configured")
    return 0


class Check(TypedDict):
    name: str
    status: Literal["ok", "warn", "fail"]
    required: bool
    detail: str


def _check(
    name: str,
    status: Literal["ok", "warn", "fail"],
    detail: str,
    *,
    required: bool = True,
) -> Check:
    return {"name": name, "status": status, "required": required, "detail": detail}


def _data_check(settings: Settings) -> Check:
    name = "data_directory"
    directory = settings.data_directory
    missing = False
    try:
        try:
            validate_control_directory(directory, create=False)
        except FileNotFoundError:
            # Validation reached an absent component after checking its existing ancestors.
            missing = True
            directory = next(parent for parent in directory.absolute().parents if parent.is_dir())
        free = shutil.disk_usage(directory).free
    except (OSError, StopIteration):
        return _check(name, "fail", "Data directory or its ancestors are unsafe or inaccessible.")
    reserve = settings.free_space_reserve_bytes
    if free < reserve:
        return _check(
            name, "fail", f"{free:,} bytes free; {reserve:,} bytes required by the reserve."
        )
    if missing:
        return _check(
            name, "warn", f"Not yet created; {free:,} bytes free on the parent filesystem."
        )
    return _check(name, "ok", f"Safe directory with {free:,} bytes free.")


def _frontend_check(settings: Settings) -> Check:
    name = "frontend"
    root = APP_ROOT / "apps/frontend"
    index = (settings.frontend_directory or root / "dist") / "index.html"
    build_inputs = (
        root / name for name in ("index.html", "package.json", "vite.config.ts", "tsconfig.json")
    )
    try:
        if not index.is_file():
            return _check(name, "fail", "Frontend build missing. Run 'make build-frontend'.")
        built = index.stat().st_mtime_ns
        stale = any(
            path.is_file() and path.stat().st_mtime_ns > built
            for path in chain(build_inputs, (root / "src").rglob("*"))
        )
    except OSError:
        return _check(name, "fail", "Cannot inspect the frontend build or source.")
    if stale:
        return _check(
            name, "warn", "Frontend source is newer than the build. Run 'make build-frontend'."
        )
    return _check(name, "ok", "Frontend index is present.")


def _tools_check(settings: Settings | None, which: ToolProbe, runner: Runner) -> Check:
    name = "tools"
    missing = [tool for tool in ("git", "uv", "node", "npm") if not which(tool)]
    optional = [tool for tool in ("git-lfs", "ffmpeg", "ffprobe") if not which(tool)]
    rendering = settings.rendering if settings else {}
    runtime = settings.runtime if settings else {}
    if not which(str(rendering.get("blender") or "blender")):
        optional.append("Blender")
    if runtime.get("unity_editor") and not which(str(runtime["unity_editor"])):
        optional.append("configured Unity Editor")
    copilot = APP_ROOT / "node_modules/@github/copilot-linux-x64/copilot"
    if not which(str(copilot)):
        optional.append("Copilot native binary")
    gh = which("gh")
    if not gh:
        optional.append("GitHub CLI")
    else:
        try:
            result = runner(
                [gh, "auth", "status"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
            )
            if result.returncode:
                optional.append("GitHub CLI authentication")
        except (OSError, subprocess.SubprocessError):
            optional.append("GitHub CLI authentication (check unavailable)")
    notes = f" Optional capabilities unavailable: {', '.join(optional)}." if optional else ""
    if missing:
        return _check(name, "fail", f"Missing required tools: {', '.join(missing)}.{notes}")
    return _check(name, "warn" if optional else "ok", f"Required tools are installed.{notes}")


def doctor(
    config: Path | None,
    *,
    as_json: bool,
    runner: Runner,
    probe: HttpProbe,
    which: ToolProbe = shutil.which,
) -> int:
    config = config or default_config_path()
    settings = None
    try:
        settings, mode = load_config(config)
    except OperatorError as exc:
        checks = [_check("config", "fail", str(exc))]
    else:
        if mode is not None and stat.S_IMODE(mode) != 0o600:
            checks = [
                _check("config", "warn", "Configuration is not private. Use chmod 600 on the file.")
            ]
        else:
            checks = [_check("config", "ok", "Private configuration is valid.")]

    if settings is None:
        checks.extend(
            [
                _check("data_directory", "fail", "Not checked without valid configuration."),
                _check("frontend", "fail", "Not checked without valid configuration."),
            ]
        )
    else:
        checks.extend([_data_check(settings), _frontend_check(settings)])
    checks.append(_tools_check(settings, which, runner))
    providers = _provider_status(settings, probe) if settings else []
    offline = [row["provider_id"] for row in providers if not row["reachable"]]
    if settings is None:
        detail = "Not checked without valid configuration."
    elif offline:
        detail = "Unavailable: " + ", ".join(offline) + "."
    else:
        detail = "All configured endpoints responded." if providers else "None configured."
    checks.append(
        _check("services", "warn" if offline or settings is None else "ok", detail, required=False)
    )
    if as_json:
        print(json.dumps({"checks": checks}))
    else:
        for row in checks:
            print(f"{row['status'].upper():4} {row['name']}: {row['detail']}")
    return int(any(row["required"] and row["status"] == "fail" for row in checks))
