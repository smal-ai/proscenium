"""One-root real SDK -> editable Geometry Nodes -> production proxy/still proof.

No graph, source asset or ready media is seeded. No browser is required for the
native GLB triangle-identity check. Run only under the explicit bounded allocation.
"""

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

from copilot import CopilotClient
from fastapi.testclient import TestClient
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.previews.journal import identity, read, sha
from proscenium.services import Services
from proscenium.sources.common import git

REPO = Path(__file__).resolve().parents[3]


def write(path: Path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main(root):
    assert root.is_relative_to(Path.home() / ".cache/proscenium-development")
    assert root.name.endswith("-b"), "Only the separately authorised follow-up project is writable"
    available = (
        int(
            next(
                line.split()[1]
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            )
        )
        * 1024
    )
    assert available >= 4 * 1024**3, "Required four-GiB host headroom is unavailable"
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    temporary = Path.home() / ".cache" / ("gn-" + sha(str(root).encode())[:10])
    temporary.mkdir(mode=0o700, exist_ok=False)
    os.environ["TMPDIR"] = str(temporary)
    settings = Settings(
        data_directory=root / "machine",
        origin="http://127.0.0.1:8765",
        execution_profiles=frozenset({"blender-cpu"}),
        profiles=(
            {
                "profile_id": "authoring",
                "name": "Scoped authoring",
                "lead_model_id": "gpt-6.1-sol",
                "worker_model_ids": ["gpt-6.1-sol"],
                "scope_status": "project_scoped",
            },
        ),
        runtime={
            "cli_path": str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            "lead_model": "gpt-6.1-sol",
            "worker_model": "gpt-6.1-sol",
            "profile_id": "authoring",
            "skill_directories": [],
            "mcp_servers": {},
        },
        rendering={
            "max_frames": 1,
            "max_pixels": 640 * 360,
            "max_samples": 8,
            "threads": 4,
            "timeout": 300,
            "claim_path": "/tmp/opencode/proscenium-native-claims/blender-render.lock",
        },
        services=(),
    )
    services = Services.create(settings)
    result: dict[str, Any] = {
        "base_commit": "011ab92d7c0f2a85ce61ea0235ad305d98b317fe",
        "implementation_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "root": str(root),
        "initial_headroom_bytes": available,
        "label": "Scripted owner input. No procedural graph/source/media preseeded.",
    }
    original_create = CopilotClient.create_session
    creations = 0

    async def allocated_create(client, **kwargs):
        nonlocal creations
        assert creations == 0, "Only one fresh SDK session is authorised"
        creations += 1
        kwargs.update(
            excluded_tools=["task"],
            custom_agents=[],
            included_builtin_agents=[],
            enable_skills=False,
        )
        write(
            root / "session-allocation.json",
            {
                "create_count": creations,
                "excluded_tools": kwargs["excluded_tools"],
                "custom_agents": [],
                "included_builtin_agents": [],
                "enable_skills": False,
                "permission_handler_preserved": kwargs.get("on_permission_request") is not None,
            },
        )
        return await original_create(client, **kwargs)

    async def no_resume(*args, **kwargs):
        raise RuntimeError("No session resume is authorised for this fresh single-send proof")

    try:
        with (
            patch.object(CopilotClient, "create_session", allocated_create),
            patch.object(CopilotClient, "resume_session", no_resume),
            TestClient(
                create_app(settings, services=services, manage_features=True),
                base_url=settings.origin,
            ) as client,
        ):
            code = services.auth.issue_pairing()
            headers = {"Origin": settings.origin, "Idempotency-Key": "gn-b-owner-pair"}
            paired = client.post(
                "/api/v1/owner/session",
                headers=headers,
                json={
                    "schema_version": "1.0",
                    "operation_id": "gn-b-owner-pair",
                    "pairing_code": code,
                    "client_nonce": "gn-native-browserless-owner",
                },
            )
            assert paired.status_code == 201, paired.text
            headers["X-CSRF-Token"] = paired.json()["csrf_token"]

            def post(route, body):
                response = client.post(
                    route,
                    json=body,
                    headers={
                        **headers,
                        "Idempotency-Key": body.get(
                            "operation_id", body.get("request_id", body.get("context_id", "gn"))
                        ),
                    },
                )
                assert response.is_success, (route, response.status_code, response.text)
                return response.json()

            post(
                "/api/v1/projects",
                dict(
                    schema_version="1.0",
                    operation_id="gn-b-create",
                    path=str(root / "project"),
                    create=True,
                    name="Model-authored stateless Geometry Nodes proof",
                ),
            )
            project = root / "project"
            config = read(project / "project.json")
            project_id = config["project_id"]
            post(
                f"/api/v1/projects/{project_id}/activate",
                dict(
                    schema_version="1.0",
                    operation_id="gn-b-activate",
                    project_id=project_id,
                    expected_revision=0,
                ),
            )
            (project / "tools").mkdir()
            for source, target in (
                ("geometry_nodes_author.py", "asset_author.py"),
                ("geometry_nodes_contract.py", "asset_contract.py"),
            ):
                shutil.copyfile(Path(__file__).with_name(source), project / "tools" / target)
            (project / "contracts").mkdir()
            shutil.copyfile(REPO / "docs/contracts/schema.json", project / "contracts/schema.json")
            (project / "AGENTS.md").write_text(
                "# Empty procedural project\nThere is NO storyboard/plan/existing asset/brief to discover. The request is the entire brief. Known readable files: project.json, AGENTS.md, tools/asset_author.py, tools/asset_contract.py, contracts/schema.json. Create only tools/asset.py and assets/procedural files. No root listing, glob, root-wide search, .proscenium/.git/runtime/authentication discovery or sibling access. Use python3 tools/asset_contract.py --head for checkpoint expected_head. The helper handles specific Git/LFS access. One authoring process only via python3 tools/asset_author.py tools/asset.py. This scoped CLI allocation replaces live-MCP/service authoring for this fresh empty project. No skills requiring live services, task tool, custom agents, children, native services, GPU/providers/Unity, software changes or authoring render/export. Production previews only, CPU8samples640x360. Stop on any refusal or required decision.\n"
            )
            git(project, "config", "user.name", "Geometry Nodes scoped owner fixture")
            git(project, "config", "user.email", "geometry-nodes@example.invalid")
            git(
                project,
                "add",
                "--",
                "project.json",
                ".gitignore",
                ".gitattributes",
                "AGENTS.md",
                "tools",
                "contracts",
            )
            git(
                project,
                "commit",
                "-m",
                "test fixture: empty procedural project and bounded read-only helpers",
            )
            result["fixture_head"] = git(project, "rev-parse", "HEAD").decode().strip()
            context_id = "geometry-nodes-b-context"
            post(
                f"/api/v1/projects/{project_id}/contexts",
                dict(
                    schema_version="1.0",
                    context_id=context_id,
                    project_id=project_id,
                    captured_at=datetime.now(ZoneInfo("Africa/Johannesburg")).isoformat(),
                    item_id=None,
                    version_id=None,
                    view_mode="none",
                    target=None,
                    camera=None,
                    range=None,
                    selected_objects=[],
                    attachments=[],
                    annotations=[],
                    approved_findings=[],
                ),
            )
            prompt = Path(__file__).with_name("geometry_nodes_prompt.txt").read_text()
            with (root / "root-reservation.json").open("x") as reservation:
                json.dump(
                    {
                        "request_id": "geometry-nodes-b-root",
                        "limit": 1,
                        "prompt_sha256": sha(prompt.encode()),
                        "started_ns": time.monotonic_ns(),
                    },
                    reservation,
                )
            accepted = post(
                f"/api/v1/projects/{project_id}/requests",
                dict(
                    schema_version="1.0",
                    request_id="geometry-nodes-b-root",
                    project_id=project_id,
                    type="queue",
                    text=prompt,
                    context_id=context_id,
                ),
            )
            result["request_admission"] = accepted
            started = time.monotonic()
            latest = None
            while time.monotonic() - started < 180:
                with services.registry.project_store(project_id).transaction(write=False) as tx:
                    latest = {
                        name: tx.all(name)
                        for name in (
                            "requests",
                            "work",
                            "invocations",
                            "decisions",
                            "versions",
                            "jobs",
                            "frames",
                            "segments",
                            "artifacts",
                            "publications",
                        )
                    }
                write(root / "public-latest.json", latest)
                result["state"] = latest
                pending = [d for d in latest["decisions"] if d["state"] == "pending"]
                if pending:
                    write(root / "blocked-owner-question.json", pending)
                    print("COORDINATOR QUESTION: " + json.dumps(pending))
                    raise RuntimeError(
                        "Unexpected actual owner decision. No approval or retry performed"
                    )
                failed_calls = [call for call in latest["invocations"] if call["state"] == "failed"]
                if failed_calls:
                    write(root / "blocked-native-call.json", failed_calls)
                    print("COORDINATOR TOOL FAILURE: " + json.dumps(failed_calls), flush=True)
                    raise RuntimeError(
                        "Native tool failure requires coordinator inspection; no retry"
                    )
                if (
                    latest["requests"]
                    and all(
                        request["receipt"]["state"] == "completed" for request in latest["requests"]
                    )
                    and not latest["versions"]
                ):
                    raise RuntimeError(
                        "Root ended without publication; preserve the consumed allocation"
                    )
                if (
                    len(latest["jobs"]) == 2
                    and all(
                        job["state"] in {"succeeded", "failed", "cancelled"}
                        for job in latest["jobs"]
                    )
                    and all(
                        request["receipt"]["state"] == "completed" for request in latest["requests"]
                    )
                ):
                    break
                failed = [job for job in latest["jobs"] if job["state"] == "failed"]
                if failed:
                    raise RuntimeError("Production node preview failed; inspect retained journal")
                time.sleep(0.05)
            else:
                raise TimeoutError("Single allocated root did not complete its production previews")
            result["seconds_to_both_previews"] = time.monotonic() - started
            result["state"] = latest
            assert all(job["state"] == "succeeded" for job in latest["jobs"]), latest["jobs"]
            version = latest["versions"][0]
            result["source"] = version
            source = project / version["source"]["entry_path"]
            expected = next(
                row["origin"]["sha256"]
                for row in version["source"]["files"]
                if row["destination"] == version["source"]["entry_path"]
            )
            assert sha(source.read_bytes()) == expected
            result["working_source_unchanged_by_renderer"] = True
            result["working_head"] = git(project, "rev-parse", "HEAD").decode().strip()
            result["authoring_readback"] = read(project / "assets/procedural/readback.json")
            result["profiles"] = {
                job["job_id"]: read(
                    project
                    / ".proscenium/jobs/previews"
                    / job["job_id"]
                    / job["attempt_id"]
                    / "native-profile.json"
                )
                for job in latest["jobs"]
            }
            result["status"] = (
                "native_model_graph_and_production_previews_pass_pending_geometry_checks"
            )
            write(root / "native-result.json", result)
    except BaseException as error:
        result["status"] = "blocked_no_native_compatibility_claim"
        result["error"] = str(error)
        raise
    finally:
        services.close()
        result["cleanup"] = {
            "sdk_process_absent": True,
            "unrelated_blender_3834895": "not touched",
            "authoring_mcp": False,
        }
        runtime = root / "machine/copilot-runtime/process.json"
        if runtime.exists():
            previous = read(runtime)
            actual = identity(previous["pid"])
            result["cleanup"]["sdk_process"] = {"original": previous, "observed": actual}
            result["cleanup"]["sdk_process_absent"] = (
                actual is None
                or actual["start_ticks"] != previous.get("start_ticks")
                or actual["state"] in {"Z", "X"}
            )
        receipts = {}
        for path in (root / "project/.proscenium/jobs/previews").glob("*/*/native-profile.json"):
            previous = read(path)["process"]
            actual = identity(previous["pid"])
            receipts[str(path.relative_to(root))] = {
                "original": previous,
                "observed": actual,
                "owned_process_gone": actual is None
                or actual["start_ticks"] != previous["start_ticks"]
                or actual["state"] in {"Z", "X"},
            }
        result["cleanup"]["native_processes"] = receipts
        write(root / "native-result.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    main(parser.parse_args().root.resolve())
