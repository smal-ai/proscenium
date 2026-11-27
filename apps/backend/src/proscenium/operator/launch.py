"""Shared launch path for the console script and original module invocation."""

import argparse
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from proscenium.configuration import Settings
from proscenium.control import validate_control_directory
from proscenium.operator import OperatorError, load_settings

PAIR_LABEL = "Owner pairing (private, 15 minutes): "


def add_serve_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--origin", default=None)
    parser.add_argument(
        "--config", type=Path, default=None, help="Private operator configuration JSON"
    )
    parser.add_argument("--frontend-dir", type=Path, default=None)
    parser.add_argument(
        "--pair", action="store_true", help="Print pairing URL to trusted operator terminal"
    )


def legacy_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Host-native Proscenium service", allow_abbrev=False
    )
    add_serve_options(parser)
    return parser


def serve(
    config: Path | None = None,
    *,
    origin: str | None = None,
    frontend_dir: Path | None = None,
    pair: bool = False,
) -> None:
    settings = load_settings(config)
    settings = _apply_overrides(settings, origin, frontend_dir)
    validate_control_directory(settings.data_directory, create=True)

    import uvicorn

    from proscenium.app import create_app
    from proscenium.services import Services

    url = urlsplit(settings.origin)
    port = url.port or 8765
    services = Services.create(settings)
    try:
        if pair:
            print(
                f"{PAIR_LABEL}{settings.origin}/#pair={services.auth.issue_pairing()}", flush=True
            )
        uvicorn.run(
            create_app(settings, services=services, manage_features=True),
            host=url.hostname or "127.0.0.1",
            port=port,
            workers=1,
        )
    finally:
        services.close()


def _apply_overrides(settings: Settings, origin: str | None, frontend: Path | None) -> Settings:
    try:
        if origin:
            settings = replace(settings, origin=origin)
        if frontend:
            settings = replace(settings, frontend_directory=frontend.resolve())
    except (ValueError, OSError, RuntimeError) as exc:
        raise OperatorError("Invalid origin or frontend directory override.") from exc
    return settings
