"""Standalone import/path/composition/build preflight, before any SDK send."""

import json
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

from proscenium.app import create_app
from proscenium.configuration import Settings

if TYPE_CHECKING:
    from tests.native.unity.m11_complete_snapshot import (
        BUILD,
        INSTALLED,
        REPO,
        ROOT,
        inspect,
        sha,
        snapshot,
        write,
    )
else:
    from m11_complete_snapshot import BUILD, INSTALLED, REPO, ROOT, inspect, sha, snapshot, write


def main(finish_checks=False):
    initial = inspect()
    if finish_checks:
        assert (ROOT / "before.private.json").is_file() and (BUILD / "dist/index.html").is_file()
        assert (
            not (ROOT / "processes.json").exists() and not (ROOT / "sdk-send-claimed.json").exists()
        )
        write(
            ROOT / "preflight-oracle-correction.json",
            dict(
                old_assertion="app.routes flattens included routers",
                actual="FastAPI uses lazy _IncludedRouter; inspect actual OpenAPI paths",
                sdk_sends=0,
                app_stacks=0,
                compiled_output_reused=True,
            ),
        )
    else:
        ROOT.mkdir(mode=0o700, parents=True, exist_ok=False)
        write(ROOT / "preflight.json", initial)
        snapshot("before")
        shutil.copytree(
            REPO / "apps/frontend", BUILD, ignore=shutil.ignore_patterns("dist", "node_modules")
        )
        (BUILD / "node_modules").symlink_to(INSTALLED / "node_modules", target_is_directory=True)
        result = subprocess.run(
            ["node", str(INSTALLED / "node_modules/vite/bin/vite.js"), "build"],
            cwd=BUILD,
            text=True,
            capture_output=True,
            timeout=60,
        )
        (ROOT / "build.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stderr
    app = create_app(Settings(frontend_directory=BUILD / "dist"))
    paths = set(app.openapi()["paths"]) | {getattr(r, "path", "") for r in app.routes}
    assert "/health" in paths and "/api/v1/projects/{project_id}/resume" in paths
    check = subprocess.run(
        [
            str(INSTALLED / ".venv/bin/python"),
            "-c",
            'import m11_complete_host; import m11_complete_snapshot; print("standalone imports passed")',
        ],
        env={
            **__import__("os").environ,
            "PYTHONPATH": f"{REPO}/apps/backend/src:{REPO}:{REPO}/tests/native/unity",
        },
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert check.returncode == 0, check.stderr
    version = subprocess.check_output(
        [str(INSTALLED / "node_modules/@github/copilot-linux-x64/copilot"), "--version"],
        text=True,
        timeout=10,
    )
    assert "1.0.112" in version
    write(
        ROOT / "build-import-composition.json",
        dict(
            status="passed",
            cli_version=version.strip(),
            standalone=check.stdout.strip(),
            frontend_hashes={
                p.relative_to(BUILD / "dist").as_posix(): sha(p)
                for p in (BUILD / "dist").rglob("*")
                if p.is_file()
            },
            routes=len(paths),
        ),
    )
    print(
        json.dumps(
            dict(
                root=str(ROOT),
                status="pre_send_passed",
                source_files=len(initial["checkpoint"]["files"]),
            )
        )
    )


if __name__ == "__main__":
    main(finish_checks="--finish-checks" in sys.argv)
