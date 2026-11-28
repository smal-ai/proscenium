"""Private directory backup/restore. No archive extraction, inference or service lifecycle.

Run from the maintained checkout: uv run python -m tools.operations.recovery --help
The format is deliberately NOT a shareable project export.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from importlib.metadata import version
from itertools import chain
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from proscenium.api.errors import DomainError
from proscenium.catalogue.relationship_backfill import rebuild_relationships
from proscenium.configuration import Settings
from proscenium.contracts import validate
from proscenium.db import Store
from proscenium.db.registry import Registry
from proscenium.db.store import MIGRATIONS, now

from .safety import (
    RecoveryError,
    assert_stopped,
    copy_file,
    digest,
    locks,
    private_directory,
    relative,
    safe_file,
    secret_path,
    tree_files,
)

FORMAT = "proscenium-private-backup-1"
CHECKOUT = Path(__file__).resolve().parents[2]


def write_json(path: Path, body: Any) -> None:
    with open(path, "x", encoding="utf-8", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        json.dump(body, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def git(root: Path, *args: str, output: Path | None = None, binary=False) -> Any:
    # No shell, external hooks, fsmonitor, lazy network object fetch or LFS smudge.
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args]
    with output.open("xb") if output else open(os.devnull, "wb") as stream:
        result = subprocess.run(
            command,
            cwd=root,
            env=environment,
            stdout=stream if output else subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    if output:
        output.chmod(0o600)
    if result.returncode:
        # Git diagnostics may contain credentialled remote URLs. Do not echo them.
        raise RecoveryError(f"git_failed:{args[0]}")
    if output:
        return ""
    return result.stdout if binary else result.stdout.decode("utf-8", errors="strict").rstrip("\n")


class BackupSource(Store):
    """F00 backup API over a quiescent, strictly read-only Linux source handle.

    Ordinary read-only SQLite connections still modify WAL shared-memory marks.
    unix-none + EXCLUSIVE uses a private in-memory WAL index instead. External
    registry/project flocks AND stopped external writers are mandatory. A nonempty
    WAL must never use immutable=1, which would omit its committed pages. With no
    WAL payload, immutable mode also prevents SQLite deleting empty sidecars.
    No raw database copy is made.
    """

    def __init__(self, path: Path, project_id: str | None = None):
        self.path, self.project_id = path, project_id
        self.write_barrier = self.write_guard = self.before_commit = self.after_commit = None

    def connect(self, *, set_wal: bool = False) -> sqlite3.Connection:
        if set_wal:
            raise RecoveryError("backup_source_is_read_only")
        wal_name = self.path.name + "-wal"
        try:
            wal_bytes = safe_file(self.path.parent, wal_name).stat().st_size
        except FileNotFoundError:
            wal_bytes = 0
        # An absent or zero-byte WAL contains no frames. Under the required
        # quiescence locks, immutable reads cannot omit a committed transaction.
        # unix-none may otherwise unlink a zero-byte WAL even with mode=ro.
        parameters = "mode=ro&vfs=unix-none" if wal_bytes else "mode=ro&immutable=1"
        connection = sqlite3.connect(
            self.path.as_uri() + "?" + parameters,
            uri=True,
            isolation_level=None,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


class BackupRegistry(Registry):
    """Keep the shared Registry settlement rule, without opening mutable sources."""

    project_source: Store | None = None

    def project_store(self, project_id: str, *, active: bool = True) -> Store:
        if self.project_source is None or self.project_source.project_id != project_id:
            raise RecoveryError("backup_project_identity_mismatch")
        if active and self.active_id() != project_id:
            raise RecoveryError("backup_project_not_active")
        return self.project_source


def existing_store(
    path: Path,
    project_id: str | None = None,
    *,
    read_only=False,
    expected_revision: int | None = None,
) -> Store:
    safe_file(path.parent, path.name)
    source = BackupSource(path, project_id)
    try:
        with closing(source.connect()) as db:
            revision = db.execute("PRAGMA user_version").fetchone()[0]
            if (
                not 1 <= revision <= len(MIGRATIONS)
                or db.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            ):
                raise RecoveryError("unsupported_or_corrupt_database")
            if expected_revision is not None and revision != expected_revision:
                raise RecoveryError("backup_database_revision_mismatch")
    except sqlite3.DatabaseError as exc:
        raise RecoveryError("unsupported_or_corrupt_database") from exc
    return source if read_only else Store(path, project_id)


def private_modes(root: Path) -> None:
    root.chmod(0o700)
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RecoveryError("symlink_refused")
        path.chmod(0o700 if path.is_dir() else 0o600)


def session_ids(store: Store, project: dict) -> list[str]:
    with store.transaction(write=False) as tx:
        association = tx.get("copilot.session", "native", required=False)
        ids = {project.get("native_session_id")}
        if association:
            ids.add(association["session_id"])
        # Historical associated sessions must not disappear when an active one changes.
        for namespace in ("work", "requests", "copilot.bindings", "copilot.native"):
            for record in tx.all(namespace):
                ids.add(record.get("native_session_id"))
                ids.add(record.get("session_id"))
        ids.update(
            record.get("retired_session_id") for record in tx.all("copilot.retired_sessions")
        )
        ids.update(
            row[0]
            for row in tx.connection.execute(
                "SELECT DISTINCT native_session_id FROM events WHERE native_session_id IS NOT NULL"
            )
        )
    ids.discard(None)
    for identity in ids:
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity):
            raise RecoveryError("invalid_native_session_identity")
    return sorted(identity for identity in ids if identity is not None)


def required_bytes(root: Path, store: Store) -> dict[str, str]:
    required = {}
    with store.transaction(write=False) as tx:
        for artifact in tx.all("artifacts"):
            if artifact["state"] == "ready":
                sha = artifact["sha256"]
                required[f".proscenium/artefacts/sha256/{sha}/content"] = sha
        for retained in tx.all("retained_sources"):
            sha = retained["sha256"]
            required[f".proscenium/retained/{sha}/content"] = sha
    for path, sha in required.items():
        if not re.fullmatch("[0-9a-f]{64}", sha):
            raise RecoveryError("invalid_content_hash")
        relative(path)
    return required


def git_snapshot(root: Path, destination: Path, store: Store) -> dict:
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise RecoveryError("standalone_git_repository_required")
    # Inspect before invoking Git, which would otherwise follow an object-store
    # or configuration symlink outside this project's source authority.
    if any(path.is_symlink() for path in (root / ".git").rglob("*")):
        raise RecoveryError("git_metadata_symlink_refused")
    for excluded in ("shallow", "objects/info/alternates", "commondir", "info/grafts"):
        if (root / ".git" / excluded).exists():
            raise RecoveryError("partial_or_external_repository_refused")
    if git(root, "rev-parse", "--show-toplevel") != str(root):
        raise RecoveryError("git_root_mismatch")
    git(root, "fsck", "--full", "--no-reflogs")
    head = git(root, "rev-parse", "HEAD")
    refs = git(root, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
    symbolic_refs = {}
    for line in git(root, "for-each-ref", "--format=%(refname) %(symref)").splitlines():
        name, _, destination_ref = line.partition(" ")
        if destination_ref:
            symbolic_refs[name] = destination_ref
    head_reference = git(root, "rev-parse", "--symbolic-full-name", "HEAD")
    # Audit paths over ALL reachable history, not merely today's index. Refuse known
    # secret names rather than silently redact history and break source identities.
    listing = destination / "git-objects.txt"
    git(root, "rev-list", "--objects", "--all", "HEAD", output=listing)
    reachable = set()
    with listing.open() as stream:
        for line in stream:
            oid, separator, name = line.rstrip("\n").partition(" ")
            reachable.add(oid)
            if separator and name and (secret_path(name) or ".proscenium" in relative(name).parts):
                raise RecoveryError("secret_path_in_git_history")
    # A DB reference to a locally present but unreachable commit is not recoverable
    # from --all. Refuse rather than make temporary refs in the owner's repository.
    with store.transaction(write=False) as tx:
        for record in tx.all("versions"):
            commits = {record["source_commit"]}
            commits.update(
                row["origin"]["commit"]
                for row in record["source"]["files"]
                if row["origin"]["kind"] == "git"
            )
            if not commits <= reachable:
                raise RecoveryError("source_unavailable:unreachable_published_commit")
    git(root, "bundle", "create", str(destination / "source.bundle"), "--all", "HEAD")
    (destination / "source.bundle").chmod(0o600)
    # Do NOT use `git lfs ls-files`: Git's clean filter can recreate a missing
    # object from the working file. Inspect committed pointer blobs directly.
    object_ids = set()
    with listing.open() as stream:
        for line in stream:
            object_id = line.split(" ", 1)[0].strip()
            if git(root, "cat-file", "-t", object_id) != "blob":
                continue
            if int(git(root, "cat-file", "-s", object_id)) > 1024:
                continue
            content = git(root, "cat-file", "blob", object_id, binary=True)
            pointer = re.fullmatch(
                rb"version https://git-lfs.github.com/spec/v1\noid sha256:([a-f0-9]{64})\nsize [0-9]+\n?",
                content,
            )
            if pointer:
                object_ids.add(pointer[1].decode("ascii"))
    objects = sorted(object_ids)
    for oid in objects:
        if not re.fullmatch("[a-f0-9]{64}", oid):
            raise RecoveryError("invalid_lfs_identity")
        name = f"lfs/objects/{oid[:2]}/{oid[2:4]}/{oid}"
        try:
            path = safe_file(root / ".git", name)
        except FileNotFoundError as exc:
            raise RecoveryError("source_unavailable:lfs_object_missing") from exc
        if digest(path) != oid:
            raise RecoveryError("hash_mismatch:lfs")
        copy_file(path, destination / name)
    # Copy current authored working files, including untracked ordinary source.
    # Preserve dirty/index state in the original. Recovery uses a new independent index.
    files = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    # .strip() does not strip NUL separators.
    for name in sorted(set(files.split("\0")) - {""}):
        if secret_path(name) or name.startswith(".proscenium/"):
            raise RecoveryError("excluded_working_source")
        try:
            path = safe_file(root, name)
        except FileNotFoundError:
            continue  # Preserve an explicitly deleted working file by absence.
        copy_file(path, destination / "working" / relative(name))
    return {
        "head": head,
        "head_reference": head_reference,
        "refs": refs,
        "symbolic_refs": symbolic_refs,
        "lfs_oids": objects,
        "working_index_restored": False,
    }


def backup(settings: Settings, project_id: str, target: Path, *, allow_incomplete=False) -> dict:
    runtime_lock = CHECKOUT / "docs/implementation/runtime-lock.json"
    locked = json.loads(runtime_lock.read_text())
    sdk_version = version("github-copilot-sdk")
    if sdk_version != locked["released_sdk"]["version"]:
        raise RecoveryError("native_layout_requires_pinned_sdk")
    data = settings.data_directory.resolve(strict=True)
    # Registry ownership first, before opening/migrating any database.
    with locks([data / "owner.lock"]):
        registry_store = existing_store(data / "registry.sqlite3", read_only=True)
        registry = BackupRegistry(registry_store)
        project = registry.get(project_id)
        root = Path(project["path"])
        if root.resolve(strict=True) != root or (root / ".proscenium").is_symlink():
            raise RecoveryError("project_location_conflict")
        if target.absolute().is_relative_to(root) or target.absolute().is_relative_to(data):
            raise RecoveryError("backup_destination_inside_live_store")
        with locks([root / ".proscenium/owner.lock"]):
            assert_stopped([root, data])
            store = existing_store(root / ".proscenium/state.sqlite3", project_id, read_only=True)
            registry.project_source = store
            # Stopped service alone does not establish settled external/native work.
            registry.assert_settled(project_id)
            with registry_store.transaction(write=False) as tx:
                if tx.connection.execute("SELECT 1 FROM resource_claims LIMIT 1").fetchone():
                    raise RecoveryError("resource_claims_require_reconciliation")
            ids = session_ids(store, project)
            native = data / "copilot-runtime/home/session-state"
            native_paths = []
            limits = []
            for sid in ids:
                path = native / sid
                if path.exists() and path.resolve(strict=True) != path:
                    raise RecoveryError("native_storage_symlink_refused")
                if not path.is_dir() or not (path / "events.jsonl").is_file():
                    limits.append({"code": "native_session_missing", "session_id": sid})
                else:
                    native_paths.append((sid, path))
            for name, sha in required_bytes(root, store).items():
                try:
                    path = safe_file(root, name)
                except FileNotFoundError:
                    limits.append(
                        {
                            "code": "retained_source_missing"
                            if "/retained/" in name
                            else "media_unavailable",
                            "path": name,
                        }
                    )
                    continue
                if digest(path) != sha:
                    raise RecoveryError("hash_mismatch:required_media")
            if limits and not allow_incomplete:
                raise RecoveryError("incomplete_backup:" + ",".join(x["code"] for x in limits))
            private_directory(target)
            # Failure leaves a private, explicitly incomplete directory with no manifest.
            (target / "project/.proscenium").mkdir(parents=True, mode=0o700)
            (target / "machine").mkdir(mode=0o700)
            store.backup(target / "project/.proscenium/state.sqlite3")
            registry_store.backup(target / "machine/registry.sqlite3")
            # Never include pairing codes/cookies/tool capabilities, cursor keys or
            # other projects in a recoverable registry. Keep only the selected binding
            # and its authoritative non-secret preferences/profile definitions.
            with closing(sqlite3.connect(target / "machine/registry.sqlite3")) as db:
                registry_revision = db.execute("PRAGMA user_version").fetchone()[0]
                db.execute(
                    "DELETE FROM records WHERE NOT ((namespace='projects' AND id=?) OR "
                    "(namespace='private' AND id IN (?, 'configuration.profiles')))",
                    (project_id, f"settings:{project_id}"),
                )
                for table in (
                    "operations",
                    "events",
                    "outbox",
                    "budget_reservations",
                    "resource_claims",
                ):
                    db.execute(f"DELETE FROM {table}")
                db.commit()
                db.execute("VACUUM")
                db.execute("PRAGMA journal_mode=DELETE")
            with closing(sqlite3.connect(target / "project/.proscenium/state.sqlite3")) as db:
                project_revision = db.execute("PRAGMA user_version").fetchone()[0]
                db.execute("DELETE FROM records WHERE namespace LIKE 'auth.%'")
                db.commit()
                db.execute("VACUUM")
                db.execute("PRAGMA journal_mode=DELETE")
            for category in ("artefacts", "retained", "tool-output"):
                source = root / ".proscenium" / category
                if source.exists():
                    for path in tree_files(source):
                        name = path.relative_to(root).as_posix()
                        if secret_path(name):
                            raise RecoveryError("secret_path_in_managed_store")
                        copy_file(path, target / "project" / name)
            native_locks = []
            native_directories = []
            for sid, source in native_paths:
                for lock in (
                    native / ".session-operation-locks" / f"{sid}.lock",
                    source / ".workspace-fork.lock",
                ):
                    if lock.exists():
                        native_locks.append(lock)
            with locks(native_locks):
                for sid, source in native_paths:
                    for directory in source.rglob("*"):
                        if directory.is_symlink():
                            raise RecoveryError("symlink_refused")
                        if directory.is_dir():
                            name = directory.relative_to(source).as_posix()
                            if secret_path(name):
                                raise RecoveryError("secret_path_in_native_session")
                            (target / "native" / sid / name).mkdir(
                                parents=True, exist_ok=True, mode=0o700
                            )
                            native_directories.append(f"{sid}/{name}")
                    for path in tree_files(source):
                        name = path.relative_to(source).as_posix()
                        if secret_path(name):
                            raise RecoveryError("secret_path_in_native_session")
                        if name.endswith(".lock"):
                            continue
                        copy_file(path, target / "native" / sid / name)
            source = target / "source"
            source.mkdir(mode=0o700)
            source_manifest = git_snapshot(root, source, store)
            assert_stopped([root, data])
            manifest: dict[str, Any] = {
                "format": FORMAT,
                "privacy": "private_backup_not_shareable",
                "created_at": now(),
                "project": project,
                "native_session_ids": ids,
                "native_directories": sorted(native_directories),
                "source": source_manifest,
                "database_revisions": {"registry": registry_revision, "project": project_revision},
                "versions": {
                    "sdk": sdk_version,
                    "cli_locked": locked["released_cli"]["version"],
                    "cli_sha256_locked": locked["released_cli"]["binary_sha256"],
                    "runtime_lock_sha256": digest(runtime_lock),
                    "cli_observation": "lock_only_no_native_process_started",
                },
                "configuration": {
                    "public_view": settings.view(),
                    "runtime_profile_references": {
                        key: settings.runtime[key]
                        for key in ("lead_model", "worker_model", "profile_id", "max_continues")
                        if key in settings.runtime
                    },
                    "rendering_profile_references": {
                        key: settings.rendering[key]
                        for key in (
                            "profile_id",
                            "build_hash",
                            "threads",
                            "timeout",
                            "max_frames",
                            "max_pixels",
                            "max_samples",
                        )
                        if key in settings.rendering
                    },
                    "private_configuration": "operator must reconfigure runtime/rendering/provider options; no secrets copied",
                },
                "limits": limits,
                "files": {},
            }
            for path in tree_files(target):
                manifest["files"][path.relative_to(target).as_posix()] = {
                    "sha256": digest(path),
                    "bytes": path.stat().st_size,
                }
            write_json(target / "manifest.json", manifest)
            private_modes(target)
            return manifest


def verify(bundle: Path) -> dict:
    if bundle.absolute() != bundle.resolve(strict=True):
        raise RecoveryError("backup_root_not_canonical")
    manifest = json.loads(safe_file(bundle, "manifest.json").read_text())
    if manifest.get("format") != FORMAT:
        raise RecoveryError("unsupported_backup_format")
    if not re.fullmatch("[0-9a-f]{40}", manifest["source"]["head"]):
        raise RecoveryError("invalid_git_head")
    for name, destination_ref in manifest["source"]["symbolic_refs"].items():
        for value in (name, destination_ref):
            if not value.startswith("refs/"):
                raise RecoveryError("invalid_symbolic_ref")
            relative(value)
    head_reference = manifest["source"]["head_reference"]
    if head_reference.startswith("refs/"):
        relative(head_reference)
    expected = manifest["files"]
    for name in manifest["native_directories"]:
        parts = relative(name).parts
        if parts[0] not in manifest["native_session_ids"] or secret_path(name):
            raise RecoveryError("unsafe_native_directory")
    for name in expected:
        relative(name)
        allowed = name in {
            "machine/registry.sqlite3",
            "source/source.bundle",
            "source/git-objects.txt",
            "project/.proscenium/state.sqlite3",
        } or name.startswith(
            (
                "source/working/",
                "source/lfs/objects/",
                "native/",
                "project/.proscenium/artefacts/",
                "project/.proscenium/retained/",
                "project/.proscenium/tool-output/",
            )
        )
        if not allowed:
            raise RecoveryError("unexpected_backup_payload")
        if name.startswith("source/lfs/objects/") and (
            not re.fullmatch("[0-9a-f]{64}", Path(name).name)
            or expected[name]["sha256"] != Path(name).name
        ):
            raise RecoveryError("lfs_content_identity_mismatch")
        if name.startswith("source/working/") and (
            secret_path(name.removeprefix("source/working/"))
            or ".proscenium" in relative(name.removeprefix("source/working/")).parts
        ):
            raise RecoveryError("excluded_working_source")
    actual = {p.relative_to(bundle).as_posix() for p in tree_files(bundle)} - {"manifest.json"}
    if actual != set(expected):
        raise RecoveryError("backup_file_inventory_mismatch")
    for name, record in expected.items():
        path = safe_file(bundle, name)
        if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
            raise RecoveryError("hash_mismatch:" + name)
    for path in chain([bundle], bundle.rglob("*")):
        if path.stat().st_mode & 0o077:
            raise RecoveryError("private_backup_permissions_required")
    revisions = manifest.get("database_revisions")
    if not isinstance(revisions, dict) or any(
        type(revisions.get(key)) is not int for key in ("registry", "project")
    ):
        raise RecoveryError("backup_database_revision_mismatch")
    existing_store(
        bundle / "machine/registry.sqlite3", read_only=True, expected_revision=revisions["registry"]
    )
    existing_store(
        bundle / "project/.proscenium/state.sqlite3",
        read_only=True,
        expected_revision=revisions["project"],
    )
    return manifest


def restore(bundle: Path, target: Path, *, rebind: bool, allow_incomplete=False) -> dict:
    if not rebind:
        raise RecoveryError("explicit_location_rebind_required")
    manifest = verify(bundle)
    if target.absolute().is_relative_to(bundle):
        raise RecoveryError("restore_destination_inside_backup")
    if manifest["limits"] and not allow_incomplete:
        raise RecoveryError("incomplete_backup_requires_explicit_acknowledgement")
    private_directory(target)
    # Restore a SINGLE selected project into a fresh filesystem and registry. Never
    # attach these IDs to the original live registry or start a native runtime.
    root = target / "project"
    root.mkdir(mode=0o700)
    for name in manifest["files"]:
        if name.startswith("project/"):
            copy_file(safe_file(bundle, name), target / relative(name))
        elif name.startswith("source/working/"):
            name_in_project = name.removeprefix("source/working/")
            copy_file(safe_file(bundle, name), root / relative(name_in_project))
    git(root, "init", "-b", "recovery")
    git(root, "lfs", "install", "--local", "--skip-smudge", "--skip-repo")
    git(
        root,
        "fetch",
        "--no-tags",
        "--update-head-ok",
        str(bundle / "source/source.bundle"),
        "+refs/*:refs/*",
    )
    git(root, "fetch", "--no-tags", str(bundle / "source/source.bundle"), "HEAD")
    git(root, "update-ref", "--no-deref", "HEAD", manifest["source"]["head"])
    for name, destination_ref in manifest["source"]["symbolic_refs"].items():
        git(root, "symbolic-ref", name, destination_ref)
    if manifest["source"]["head_reference"].startswith("refs/"):
        git(root, "symbolic-ref", "HEAD", manifest["source"]["head_reference"])
    git(root, "read-tree", "HEAD")
    for name in manifest["files"]:
        if name.startswith("source/lfs/"):
            copy_file(safe_file(bundle, name), root / ".git" / name.removeprefix("source/"))
        elif name.startswith("native/"):
            copy_file(
                safe_file(bundle, name),
                target
                / "machine/copilot-runtime/home/session-state"
                / name.removeprefix("native/"),
            )
    for name in manifest["native_directories"]:
        (target / "machine/copilot-runtime/home/session-state" / relative(name)).mkdir(
            parents=True, exist_ok=True, mode=0o700
        )
    # Hydrated working source was retained byte-for-byte, not fetched remotely or
    # smudged by project-defined filters. Missing working source stays missing.
    git(root, "fsck", "--full", "--no-reflogs")
    machine = target / "machine"
    machine.mkdir(exist_ok=True, mode=0o700)
    copy_file(bundle / "machine/registry.sqlite3", machine / "registry.sqlite3")
    project_id = manifest["project"]["project_id"]
    registry = Registry(Store(machine / "registry.sqlite3"))
    Store(root / ".proscenium/state.sqlite3", project_id)
    record = registry.get(project_id)
    if record != manifest["project"]:
        raise RecoveryError("project_manifest_identity_mismatch")
    project_config = json.loads(safe_file(root, "project.json").read_text())
    validate("ProjectConfig", project_config)
    if any(project_config[k] != record[k] for k in ("project_id", "repository_id")):
        raise RecoveryError("project_source_identity_mismatch")
    for name in project_config["paths"].values():
        if secret_path(name) or ".proscenium" in relative(name).parts:
            raise RecoveryError("unsafe_project_directory")
        (root / relative(name)).mkdir(parents=True, exist_ok=True, mode=0o700)
    with registry.store.transaction() as tx:
        tx.put(
            "projects",
            project_id,
            {
                **record,
                "path": str(root),
                "state": "awaiting_resume",
                "state_revision": record["state_revision"] + 1,
            },
            expected_revision=tx.revision("projects", project_id),
            schema="ProjectRecord",
        )
        tx.put("registry", "active", {"project_id": project_id})
    rebuild_relationships(SimpleNamespace(registry=registry), project_id)
    # Gate lives in memory and is closed on every Services.create. No callbacks,
    # session creation, epoch rewriting, pending-work continuation or job pumping.
    report = {
        "project_id": project_id,
        "path": str(root),
        "data_directory": str(machine),
        "original_path": record["path"],
        "execution": "closed_awaiting_explicit_resume",
        "limits": manifest["limits"],
        "native_resume_tested": False,
        "authentication": "not_restored_repair_separately_via_supported_login_and_owner_pairing",
        "location_reconciliation": "fresh_registry_only_original_must_remain_offline",
    }
    write_json(target / "recovery-report.json", report)
    write_json(
        target / "config.json", {"data_directory": str(machine), "origin": "http://127.0.0.1:8765"}
    )
    private_modes(target)
    return report


def main(argv: Sequence[str] | None = None, *, prog: str | None = None) -> None:
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    save = sub.add_parser("backup")
    save.add_argument("--config", type=Path)
    save.add_argument("--data-directory", type=Path)
    save.add_argument("--project-id", required=True)
    save.add_argument("--target", type=Path, required=True)
    save.add_argument("--allow-incomplete", action="store_true")
    check = sub.add_parser("verify")
    check.add_argument("bundle", type=Path)
    load = sub.add_parser("restore")
    load.add_argument("bundle", type=Path)
    load.add_argument("--target", type=Path, required=True)
    load.add_argument("--rebind-fresh-registry", action="store_true")
    load.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "backup":
            settings = Settings.load(args.config)
            if args.data_directory:
                settings = replace(settings, data_directory=args.data_directory)
            result = backup(
                settings,
                args.project_id,
                args.target.absolute(),
                allow_incomplete=args.allow_incomplete,
            )
            result = {
                "status": "backup_created",
                "files": len(result["files"]),
                "limits": result["limits"],
            }
        elif args.action == "verify":
            result = verify(args.bundle.absolute())
            result = {
                "status": "verified",
                "files": len(result["files"]),
                "limits": result["limits"],
            }
        else:
            result = restore(
                args.bundle.absolute(),
                args.target.absolute(),
                rebind=args.rebind_fresh_registry,
                allow_incomplete=args.allow_incomplete,
            )
        print(json.dumps(result, indent=2))
    except DomainError as exc:
        parser.exit(2, f"Recovery refused: {exc.code}\n")
    except (RecoveryError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Recovery refused: {exc}\n")


if __name__ == "__main__":
    main()
