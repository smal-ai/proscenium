"""Read-only SourceClosure helper copied into the owned proof project for the model."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("commit")
    parser.add_argument("entry")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--dependency", nargs=2, action="append", default=[])
    args = parser.parse_args()
    root = Path.cwd().resolve()
    project = json.loads((root / "project.json").read_bytes())
    rows = []
    for name in sorted(args.files):
        assert not Path(name).is_absolute() and ".." not in Path(name).parts
        data = subprocess.check_output(["git", "show", f"{args.commit}:{name}"])
        if data.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
            fields = dict(line.split(b" ", 1) for line in data.splitlines())
            oid = fields[b"oid"].decode().removeprefix("sha256:")
            data = (root / ".git/lfs/objects" / oid[:2] / oid[2:4] / oid).read_bytes()
            assert hashlib.sha256(data).hexdigest() == oid and len(data) == int(fields[b"size"])
        rows.append(
            {
                "destination": name,
                "origin": {
                    "kind": "git",
                    "repository_id": project["repository_id"],
                    "commit": args.commit,
                    "path": name,
                    "sha256": hashlib.sha256(data).hexdigest(),
                },
            }
        )
    native = args.entry.endswith(".blend")
    result = {
        "entry_path": args.entry,
        "files": rows,
        "dependencies": sorted(
            [{"item_id": a, "version_id": b} for a, b in args.dependency],
            key=lambda row: (row["item_id"], row["version_id"]),
        ),
        "required_runtime": {
            "application": "blender" if native else "document",
            "version": "4.5.14 LTS" if native else "1.0",
            "features": ["linked-libraries", "external-textures"] if native else [],
        },
    }
    # This fixture manifest has ASCII strings/arrays only. Its compact sorted JSON
    # is also RFC8785. The production resolver independently verifies the digest.
    result["digest"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
