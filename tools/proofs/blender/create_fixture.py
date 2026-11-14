"""Build disposable Git/LFS F01 sources and verify an actual historical Blender reopen.

Run with the host Python, not Blender. This is fixture evidence, not acceptance of
Proscenium's production source resolver or the cloud authoring journey.
"""

import argparse
import hashlib
import json
import struct
import subprocess
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path


def run(argv, cwd, timeout=120):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"Command {argv!r} failed ({result.returncode}):\n"
                           + result.stdout.decode(errors="replace")[-4000:]
                           + result.stderr.decode(errors="replace")[-2000:])
    return result.stdout


def png(colour):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    data = (b"\0" + bytes(colour) * 8) * 8
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(data)) + chunk(b"IEND", b""))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blender", default="blender")
    args = parser.parse_args()
    root = args.output.resolve()
    receipt = root / "fixture-evidence.json"
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        for row in saved["historical_files"]:
            if digest(root / "historical" / row["path"]) != row["sha256"]:
                raise ValueError("An existing fixture changed. Inspect before choosing a new output directory.")
        print(json.dumps({"state": "reused", "receipt": str(receipt)}))
        return
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new empty output directory. Partial fixtures are retained for inspection.")
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "project"
    workspace.mkdir()
    (workspace / ".proscenium-fixture").write_text("F01 disposable native source fixture\n")
    (workspace / ".gitignore").write_text(".proscenium/\n*.blend1\n*.blend2\n.env\n")
    (workspace / ".gitattributes").write_text(
        "*.blend filter=lfs diff=lfs merge=lfs -text\n*.png filter=lfs diff=lfs merge=lfs -text\n")
    run(["git", "init", "-b", "main"], workspace)
    run(["git", "config", "user.name", "Proscenium fixture"], workspace)
    run(["git", "config", "user.email", "fixture@proscenium.invalid"], workspace)
    run(["git", "lfs", "install", "--local"], workspace)
    recipe = Path(__file__).with_name("source_fixture.py").resolve()

    def blender(operation, project, report=None):
        command = [args.blender, "--background", "--factory-startup", "--disable-autoexec",
                   "--threads", "4", "--python-exit-code", "1", "--python", str(recipe),
                   "--", operation, "--root", str(project)]
        if report is not None:
            command += ["--report", str(report)]
        output = run(command, workspace, timeout=180)
        (root / f"{operation}-{project.name}.log").write_bytes(output)

    texture = workspace / "assets/cube/colour.png"
    texture.parent.mkdir(parents=True)
    texture.write_bytes(png((32, 96, 224)))
    blender("asset-v1", workspace)
    blender("scene", workspace)
    paths = ["assets/cube/asset.blend", "assets/cube/colour.png", "scenes/fixture/scene.blend"]
    run(["git", "add", ".gitignore", ".gitattributes", ".proscenium-fixture", *paths], workspace)
    run(["git", "commit", "-m", "fixture: native scene and blue linked asset"], workspace)
    first = run(["git", "rev-parse", "HEAD"], workspace).decode().strip()
    historical_files = [{"path": path, "sha256": digest(workspace / path)} for path in paths]
    texture.write_bytes(png((224, 32, 160)))
    blender("asset-v2", workspace)
    run(["git", "add", "assets/cube/asset.blend", "assets/cube/colour.png"], workspace)
    run(["git", "commit", "-m", "fixture: taller magenta linked asset"], workspace)
    second = run(["git", "rev-parse", "HEAD"], workspace).decode().strip()
    historical = root / "historical"
    historical.mkdir()
    (historical / ".proscenium-fixture").write_text("F01 historical reopen fixture\n")
    lfs_objects = workspace / ".git/lfs/objects"
    for row in historical_files:
        blob = run(["git", "show", f"{first}:{row['path']}"], workspace)
        if not blob.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
            raise ValueError("Expected an actual committed LFS pointer")
        fields = dict(line.split(b" ", 1) for line in blob.splitlines())
        oid = fields[b"oid"].decode().removeprefix("sha256:")
        data = (lfs_objects / oid[:2] / oid[2:4] / oid).read_bytes()
        if len(data) != int(fields[b"size"]) or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("LFS content identity mismatch")
        target = historical / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    blender("inspect", historical, root / "historical-inspection.json")
    blender("inspect", workspace, root / "current-inspection.json")
    old = json.loads((root / "historical-inspection.json").read_text())
    new = json.loads((root / "current-inspection.json").read_text())
    for objects, expected_height in ((old["objects"], 1), (new["objects"], 1.5)):
        cubes = [obj for obj in objects if obj["object_id"] == "object-cube-source"]
        if len(cubes) != 2 or any(abs(obj["bounds_max"][2] - expected_height) > 1e-6 for obj in cubes):
            raise ValueError("Native linked geometry does not match its source version")
    receipt.write_text(json.dumps({
        "status": "native_fixture_verified",
        "scope": "Real fixture generation, Git/LFS hydration and historical Blender reopen only",
        "recorded_at": datetime.now(timezone(timedelta(hours=2))).isoformat(),
        "blender": old["blender"], "build_hash": old["build_hash"],
        "asset_v1_commit": first, "asset_v2_commit": second,
        "historical_files": historical_files,
        "current_files": [{"path": path, "sha256": digest(workspace / path)} for path in paths],
        "historical_inspection": "historical-inspection.json",
        "current_inspection": "current-inspection.json",
    }, indent=2) + "\n")
    print(json.dumps({"state": "verified", "receipt": str(receipt)}))


if __name__ == "__main__":
    main()
