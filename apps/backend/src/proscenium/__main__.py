import argparse
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.services import Services


def main() -> None:
    parser = argparse.ArgumentParser(description="Host-native Proscenium service")
    parser.add_argument("--origin", default=None)
    parser.add_argument(
        "--config", type=Path, default=None, help="Private operator configuration JSON"
    )
    parser.add_argument("--frontend-dir", type=Path, default=None)
    parser.add_argument(
        "--pair", action="store_true", help="Print pairing URL to trusted operator terminal"
    )
    arguments = parser.parse_args()
    if arguments.config is not None and not arguments.config.is_file():
        parser.error("--config must name an existing private JSON file")
    settings = Settings.load(arguments.config)
    if arguments.origin:
        settings = replace(settings, origin=arguments.origin)
    if arguments.frontend_dir:
        settings = replace(settings, frontend_directory=arguments.frontend_dir.resolve())
    services = Services.create(settings)
    try:
        if arguments.pair:
            print(
                f"Owner pairing (private, 15 minutes): {settings.origin}/#pair={services.auth.issue_pairing()}",
                flush=True,
            )
        url = urlsplit(settings.origin)
        uvicorn.run(
            create_app(settings, services=services, manage_features=True),
            host=url.hostname or "127.0.0.1",
            port=url.port or 8765,
            workers=1,
        )
    finally:
        services.close()


if __name__ == "__main__":
    main()
