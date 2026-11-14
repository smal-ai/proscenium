"""Read-only M13 import into a fresh private registry, with NO native history."""
# ruff: noqa: E402 -- standalone entry point exposes maintained repository tools

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from proscenium.api.composition import configure_services
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.services import Services

from tools.operations.recovery import write_json
from tools.operations.safety import private_directory

SOURCE = Path.home() / ".cache/proscenium-development/c13c"
PROJECT = "m13-cinematic"
VIDEO_SHA = "ddf01466d4a086bcdae27890e6cbabaa2bf309b62e48bf9353a056d92a60aa8e"
MARKER = "M14_SSH_READ_ONLY_20261114"
PROMPT = "Read-only M14 SSH proof. No edits, delegation, authoring, providers or service operations. Do not give a preamble. Use view once to read m14-marker.txt in this project. Reply in one final concise line with its exact marker plus the context_id, selected version and review mode from authoritative host context, then stop. Imported media is actual M13 history, not newly rendered."


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def preservation():
    """Hash originals without constructing/migrating any original application store."""
    paths = [p for p in (SOURCE / "project").rglob("*") if p.is_file()]
    paths += [
        p for p in (SOURCE / "machine/copilot-runtime/home/session-state").rglob("*") if p.is_file()
    ]
    return {str(p.relative_to(SOURCE)): sha(p) for p in sorted(paths)}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def import_roots(project, config):
    # Git cannot preserve empty configured roots. Normal activation requires them.
    for configured in config["paths"].values():
        target = project / configured
        assert target.resolve().is_relative_to(project.resolve())
        target.mkdir(mode=0o700, parents=True, exist_ok=True)


def prepare(root):
    private_directory(root)
    (root / "t").mkdir(mode=0o700)
    before = preservation()
    write_json(root / "original-before.json", before)
    backend, forward, ssh, comfy, vlm = [free_port() for _ in range(5)]
    assert len({backend, forward, ssh, comfy, vlm}) == 5
    origin = f"http://127.0.0.1:{forward}"
    config = {
        "data_directory": str(root / "d"),
        "origin": origin,
        "frontend_directory": str(REPO / "apps/frontend/dist"),
        "services": [],
        "execution_profiles": [],
        "profiles": [
            {
                "profile_id": "authoring",
                "name": "M14 read-only allocation",
                "lead_model_id": "gpt-6.1-sol",
                "worker_model_ids": [],
                "scope_status": "project_scoped",
            }
        ],
        "runtime": {
            "cli_path": str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            "lead_model": "gpt-6.1-sol",
            "worker_model": "gpt-6.1-sol",
            "profile_id": "authoring",
            "mcp_servers": {},
            "skill_directories": [],
        },
    }
    write_json(root / "config.json", config)
    unavailable = json.loads(json.dumps(config))
    unavailable["revision"] = 1
    unavailable["profiles"][0].update(
        name="M14 configured unavailable Copilot", scope_status="unavailable"
    )
    # A genuinely missing direct executable, not a pretend successful health check.
    unavailable["runtime"]["cli_path"] = str(root / "absent/copilot")
    unavailable["services"] = [
        {
            "provider_id": "m14-closed-comfy",
            "endpoint": f"http://127.0.0.1:{comfy}",
            "capabilities": ["image"],
            "model_id": None,
            "available": False,
        },
        {
            "provider_id": "m14-closed-vlm",
            "endpoint": f"http://127.0.0.1:{vlm}",
            "capabilities": ["vlm"],
            "model_id": None,
            "available": False,
        },
    ]
    unavailable["provider_options"] = {"review_provider_id": "m14-closed-vlm"}
    write_json(root / "unavailable.json", unavailable)
    Settings.load(root / "unavailable.json")
    services = Services.create(Settings.load(root / "config.json"))
    try:
        project = root / "p"
        subprocess.run(
            ["git", "clone", "--no-hardlinks", str(SOURCE / "project"), str(project)],
            env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"},
            check=True,
            capture_output=True,
        )
        shutil.copytree(
            SOURCE / "project/.git/lfs/objects", project / ".git/lfs/objects", dirs_exist_ok=True
        )
        subprocess.run(["git", "remote", "remove", "origin"], cwd=project, check=True)
        subprocess.run(["git", "lfs", "checkout"], cwd=project, check=True, capture_output=True)
        identity = json.loads((SOURCE / "project/project.json").read_text())
        import_roots(project, identity)
        identity["name"] = "M14 imported actual M13 output history"
        (project / "project.json").write_bytes(canonical(identity))
        (project / "m14-marker.txt").write_text(
            MARKER + "\nIMPORTED ACTUAL M13 HISTORY. No new rendering.\n"
        )
        services.registry.register(
            dict(
                schema_version="1.0",
                project_id=PROJECT,
                repository_id=identity["repository_id"],
                name=identity["name"],
                path=str(project),
                native_session_id=None,
                state="inactive",
                state_revision=0,
            )
        )
        services.activate_project(PROJECT)
        configure_services(services)
        store = services.registry.project_store(PROJECT)
        original = sqlite3.connect(
            f"file:{SOURCE / 'project/.proscenium/state.sqlite3'}?mode=ro", uri=True
        )
        selected = {"fixture-v1", "m13-film-v1"}
        imported = []
        # Terminal records are imported history, not new dispatches or fabricated outcomes.
        # Preserve project/repository identities because immutable map/manifest bytes bind them.
        namespaces = {
            "items",
            "versions",
            "version_links",
            "jobs",
            "attempts",
            "frames",
            "segments",
            "selections",
        }
        with store.transaction() as tx:
            tx.put(
                "project_identity",
                "identity",
                {"project_id": PROJECT, "repository_id": identity["repository_id"]},
            )
            for namespace, key, revision, body in original.execute(
                "SELECT namespace,id,revision,body FROM records ORDER BY namespace,id"
            ):
                if namespace not in namespaces:
                    continue
                row = json.loads(body)
                if namespace in {"versions", "version_links"} and key not in selected:
                    continue
                if namespace == "items":
                    if row["item_id"] == "m13-film":
                        row["current_version_id"] = "m13-film-v1"
                        row["description"] = (
                            "IMPORTED ACTUAL SELECTED M13 V1 HISTORY. Not newly rendered."
                        )
                        row["state_revision"] = 0
                        revision = 0
                if namespace == "jobs":
                    if row["target"]["version"]["version_id"] not in selected:
                        continue
                    assert row["state"] == "succeeded"
                if namespace == "attempts" and row["job_id"] == "m13-encode-v2":
                    continue
                tx.connection.execute(
                    "INSERT INTO records VALUES (?,?,?,?)",
                    (namespace, key, revision, canonical(row).decode()),
                )
                imported.append(
                    {
                        "namespace": namespace,
                        "id": key,
                        "body_sha256": hashlib.sha256(canonical(row)).hexdigest(),
                    }
                )
        artifacts = services.extension("artifacts")
        for (body,) in original.execute(
            "SELECT body FROM records WHERE namespace='artifacts' ORDER BY id"
        ):
            row = json.loads(body)
            # Keep all registered actual bytes available to historical targets. No original annotation is imported.
            data = (
                SOURCE / f"project/.proscenium/artefacts/sha256/{row['sha256']}/content"
            ).read_bytes()
            assert hashlib.sha256(data).hexdigest() == row["sha256"] and len(data) == row["bytes"]
            result = artifacts.promote(
                PROJECT,
                row["artifact_id"],
                data,
                kind=row["kind"],
                purpose=row["purpose"],
                media_type=row["media_type"],
                derived_from=row["derived_from"],
                metadata={
                    k: row[k]
                    for k in ("dimensions", "fps", "frame_count", "source_version_id")
                    if k in row
                },
            )
            assert result == row
            with store.transaction() as tx:
                artifacts.put(tx, result)
        original.close()
        video = artifacts.get(PROJECT, "m13-encode-v1-delivery")
        assert video["sha256"] == VIDEO_SHA and video["bytes"] == 64876
        assert artifacts.ready_path(PROJECT, video["artifact_id"]).stat().st_size == 64876
        for version in selected:
            services.extension("sources").materialise(PROJECT, version)
        with store.transaction(write=False) as tx:
            assert (
                not tx.all("copilot.session")
                and not tx.all("requests")
                and not tx.all("annotations")
            )
        write_json(
            root / "import.json",
            {
                "label": "IMPORTED ACTUAL M13 HISTORY, NOT NEW RENDERS",
                "source": str(SOURCE),
                "preserved_project_id_for_immutable_media": PROJECT,
                "fresh_native_association": True,
                "native_history_imported": False,
                "original_annotation_imported": False,
                "records": imported,
                "video": video,
            },
        )
    finally:
        services.close()
    assert preservation() == before
    write_json(
        root / "allocation.json",
        {
            "origin": origin,
            "backend_port": backend,
            "ssh_port": ssh,
            "forward_port": forward,
            "closed_ports": [comfy, vlm],
            "frontend": config["frontend_directory"],
            "project_id": PROJECT,
            "prompt": PROMPT,
        },
    )
    return {
        "root": str(root),
        "imported_records": len(imported),
        "original_files_hashed": len(before),
        "model_sends": 0,
    }


if __name__ == "__main__":
    root = Path(sys.argv[1]).absolute()
    if "--complete" in sys.argv:
        private_directory(root)
        value = prepare(root / "a")
        path = root / "a/allocation.json"
        body = json.loads(path.read_text())
        body["guard_root"] = str(root)
        path.write_text(json.dumps(body, indent=2))
        value["shared_one_send_guard"] = str(root / "sdk-send-reservation.json")
        print(json.dumps(value))
    else:
        print(json.dumps(prepare(root)))
