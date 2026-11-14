"""Read-only closure digest helper, no asset or node-graph construction."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def main():
    commit, entry = sys.argv[1:3]
    root = Path.cwd().resolve()
    project = json.loads((root / "project.json").read_bytes())
    data = subprocess.check_output(["git", "show", f"{commit}:{entry}"])
    if data.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
        fields = dict(line.split(b" ", 1) for line in data.splitlines())
        oid = fields[b"oid"].decode().removeprefix("sha256:")
        data = (root / ".git/lfs/objects" / oid[:2] / oid[2:4] / oid).read_bytes()
        assert hashlib.sha256(data).hexdigest() == oid and len(data) == int(fields[b"size"])
    result = {
        "entry_path": entry,
        "files": [
            {
                "destination": entry,
                "origin": {
                    "kind": "git",
                    "repository_id": project["repository_id"],
                    "commit": commit,
                    "path": entry,
                    "sha256": hashlib.sha256(data).hexdigest(),
                },
            }
        ],
        "dependencies": [],
        "required_runtime": {
            "application": "blender",
            "version": "4.5.14 LTS",
            "features": ["geometry-nodes"],
        },
    }
    result["digest"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
