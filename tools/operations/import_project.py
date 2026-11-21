"""Import verified local project sources and media as the stopped runtime's owner."""

import argparse
import json
import subprocess
from pathlib import Path

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.projects.importer import import_project
from proscenium.services import Services


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument("--config", type=Path)
    location.add_argument("--data-dir", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.config:
            settings = Settings.load(args.config.resolve(strict=True))
        else:
            settings = Settings(data_directory=args.data_dir.expanduser().resolve())
        root = args.project_root.expanduser().resolve(strict=True)
        data = settings.data_directory.resolve()
        if data.is_relative_to(root) or root.is_relative_to(data):
            raise DomainError("scope_denied", 403, "Project and registry paths must not overlap")
        manifest = json.loads(args.manifest.read_bytes())
        services = Services.create(settings)
        try:
            result = import_project(services, Caller("owner", "owner-import"), root, manifest)
        finally:
            services.close()
    except DomainError as exc:
        parser.exit(1, f"Import failed: {exc.code}: {exc}\n")
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Import failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
