"""Operator entry point for a maintained Proscenium checkout."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from proscenium.configuration import APP_ROOT
from proscenium.control import (
    ControlProtocolError,
    ControlUnavailableError,
    control_request,
    control_socket_path,
)
from proscenium.operator import OperatorError, load_settings
from proscenium.operator.diagnostics import doctor, http_status, status
from proscenium.operator.launch import PAIR_LABEL, add_serve_options, serve
from proscenium.operator.service import (
    ACTIONS,
    DEFAULT_UNIT,
    confirm_external,
    journal_lines,
    service_command,
    unit_directory,
    unit_name,
)

_DELEGATED = {
    "backup": "Back up a stopped project into a private directory",
    "verify": "Verify a private backup bundle",
    "restore": "Restore a verified backup bundle",
    "import": "Import local project sources and verified media",
}
COMMANDS = ("pair", "serve", "service", "status", "doctor", *_DELEGATED)


def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, **kwargs)


def _error(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code


def _pair(args: argparse.Namespace) -> int:
    socket_path = control_socket_path(load_settings(args.config))
    try:
        reply = control_request(socket_path, {"action": "pair"})
    except ControlUnavailableError:
        return _error(
            f"Proscenium is not running (control socket unavailable at {socket_path}). "
            "Start it with `proscenium service start` or `proscenium serve`.",
            1,
        )
    except (ControlProtocolError, OSError):
        return _error("Could not safely request pairing from the Proscenium control socket.", 2)
    url = reply.get("pairing_url")
    expires = reply.get("expires_in_seconds")
    if (
        reply.get("ok") is not True
        or not _is_pairing_url(url)
        or type(expires) is not int
        or expires <= 0
    ):
        return _error("The Proscenium runtime returned an invalid pairing response.", 2)
    if args.json:
        print(json.dumps({"pairing_url": url, "expires_in_seconds": expires}))
    else:
        label = PAIR_LABEL if expires == 900 else f"Owner pairing (private, {expires} seconds): "
        print(url if args.url_only else f"{label}{url}")
    return 0


def _is_pairing_url(value: object) -> bool:
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        return False
    return (
        url.scheme in {"http", "https"}
        and url.hostname in {"localhost", "127.0.0.1", "::1"}
        and url.username is None
        and url.password is None
        and port != 0
        and bool(re.fullmatch(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]+)?", url.netloc, re.I))
        and url.path in {"", "/"}
        and not url.query
        and bool(re.fullmatch(r"pair=[A-Za-z0-9_-]{32,}", url.fragment))
    )


def _delegate(command: str, arguments: list[str]) -> int:
    if not (APP_ROOT / "tools/operations").is_dir():
        raise OperatorError(f"'{command}' requires a maintained Proscenium checkout.")
    checkout = str(APP_ROOT)
    if checkout not in sys.path:
        sys.path.insert(0, checkout)
    try:
        if command == "import":
            from tools.operations import import_project

            import_project.main(arguments, prog="proscenium import")
        else:
            from tools.operations import recovery

            recovery.main([command, *arguments], prog="proscenium")
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        raise
    return 0


def _add_unit_option(parser: argparse.ArgumentParser, *, default: str = DEFAULT_UNIT) -> None:
    parser.add_argument("--unit", type=unit_name, default=default, metavar="NAME")


class _StrictParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = _StrictParser(prog="proscenium", description="Operate Proscenium Studio")
    commands = parser.add_subparsers(dest="command", required=True)
    pair = commands.add_parser("pair", help="Request a private pairing URL from the live runtime")
    pair.add_argument("--config", type=Path)
    output = pair.add_mutually_exclusive_group()
    output.add_argument("--url-only", action="store_true")
    output.add_argument("--json", action="store_true")
    add_serve_options(commands.add_parser("serve", help="Run Studio in the foreground"))

    service = commands.add_parser("service", help="Manage the systemd user unit")
    _add_unit_option(service)
    actions = service.add_subparsers(dest="action", required=True)
    for name in ("install", "uninstall", *ACTIONS):
        action = actions.add_parser(name)
        _add_unit_option(action, default=argparse.SUPPRESS)
        action.add_argument(
            "--force",
            action="store_true",
            help="Allow an external unit after explicit confirmation",
        )
        action.add_argument(
            "--yes",
            action="store_true",
            help="Confirm the external unit action non-interactively (requires --force)",
        )
        if name == "install":
            action.add_argument("--config", type=Path)
            action.add_argument("--checkout", type=Path)
            action.add_argument("--enable", action="store_true")
            action.add_argument("--start", action="store_true")
        elif name == "logs":
            action.add_argument("--lines", type=journal_lines, default=50)
            action.add_argument("--follow", action="store_true")

    for name, summary in (
        ("status", "Inspect the live runtime"),
        ("doctor", "Check workstation readiness"),
    ):
        check = commands.add_parser(name, help=summary)
        check.add_argument("--config", type=Path)
        check.add_argument("--json", action="store_true")
        if name == "status":
            _add_unit_option(check)

    for name, summary in _DELEGATED.items():
        commands.add_parser(name, help=summary, add_help=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments and arguments[0] in _DELEGATED:
            return _delegate(arguments[0], arguments[1:])
        args = _parser().parse_args(arguments)
        if args.command == "pair":
            return _pair(args)
        if args.command == "serve":
            serve(args.config, origin=args.origin, frontend_dir=args.frontend_dir, pair=args.pair)
            return 0
        if args.command == "service":
            return service_command(args, run, unit_directory(), confirm=confirm_external)
        if args.command == "status":
            return status(
                load_settings(args.config),
                args.unit,
                as_json=args.json,
                runner=run,
                probe=http_status,
            )
        if args.command == "doctor":
            return doctor(
                args.config, as_json=args.json, runner=run, probe=http_status, which=shutil.which
            )
    except OperatorError as exc:
        return _error(str(exc), 2)
    except (OSError, UnicodeError) as exc:
        return _error(f"Proscenium {arguments[0]}: {getattr(exc, 'strerror', None) or str(exc)}", 1)
    return 2
