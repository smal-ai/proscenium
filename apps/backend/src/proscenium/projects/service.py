"""Owner workspace lifecycle. Opening a workspace never creates native/model work."""

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import canonical, validate
from proscenium.db import Store
from proscenium.db.ownership import Ownership
from proscenium.db.paths import DENIED_PARTS, project_path
from proscenium.services import Services
from proscenium.sources.common import (
    SECRET_NAMES,
    atomic_bytes,
    fingerprint,
    git,
    managed,
    replay,
    sha,
)

DIRECTORIES = ("brief", "assets", "scenes", "storyboards", "sequences")
IGNORE = (
    ".proscenium/",
    ".env",
    ".env.*",
    "!.env.example",
    ".envrc",
    ".direnv/",
    "*.blend1",
    "*.blend2",
    "[Ll]ibrary/",
    "[Tt]emp/",
    "[Ll]ogs/",
    "[Bb]uilds/",
    "[Uu]serSettings/",
    "[Mm]emoryCaptures/",
)
FORMATS = (
    "blend fbx glb png jpg jpeg webp exr hdr mp4 tga tif tiff obj mtl abc usd usda usdc usdz zip 7z"
).split()


def owner_only(caller: Caller) -> None:
    if caller.channel != "owner":
        raise DomainError("scope_denied", 403)


def receipt(body: dict[str, Any], resource: str, state: str, revision: int) -> dict[str, Any]:
    return dict(
        schema_version="1.0",
        operation_id=body["operation_id"],
        resource_id=resource,
        state=state,
        state_revision=revision,
        replayed=False,
    )


class Projects:
    def __init__(self, services: Services):
        self.services = services

    def workspace_boundary(self, root: Path) -> None:
        if any(
            part.lower() in DENIED_PARTS | SECRET_NAMES | {".proscenium"}
            or part.lower().startswith(".env")
            for part in root.parts
        ) or root.is_relative_to(self.services.settings.data_directory.resolve()):
            raise DomainError("scope_denied", 403)

    @staticmethod
    def config(root: Path) -> dict[str, Any]:
        try:
            path = project_path(root, "project.json")
            if (root / "project.json").is_symlink():
                raise DomainError("scope_denied", 403)
            config = json.loads(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise DomainError("invalid_request", message="Missing or invalid project.json") from exc
        validate("ProjectConfig", config)
        for value in config["paths"].values():
            project_path(root, value, directory=True)
        if (
            git(root, "rev-parse", "--show-toplevel", required=False).decode().strip() != str(root)
            or git(root, "rev-parse", "--show-object-format", required=False).decode().strip()
            != "sha1"
        ):
            raise DomainError(
                "unsupported_repository", 415, "Workspace must be the Git working-tree root"
            )
        return dict(config)

    @staticmethod
    def bootstrap(
        root: Path,
        name: str,
        *,
        identity: dict[str, str] | None = None,
        install_lfs_hooks: bool = True,
    ) -> None:
        # Never initialise a nested project in somebody else's repository.
        existing = git(root, "rev-parse", "--show-toplevel", required=False).decode().strip()
        if existing and existing != str(root):
            raise DomainError("unsupported_repository", 415)
        for directory in (*DIRECTORIES, ".proscenium"):
            candidate = root / directory
            if candidate.is_symlink():
                raise DomainError("scope_denied", 403)
            candidate.mkdir(exist_ok=True, mode=0o700)
        if not (root / ".git").exists():
            git(root, "init", "-b", "main", "--object-format=sha1")
        git(root, "lfs", "install", "--local", *([] if install_lfs_hooks else ["--skip-repo"]))
        for filename, lines in (
            (".gitignore", list(IGNORE)),
            (
                ".gitattributes",
                [f"*.{extension} filter=lfs diff=lfs merge=lfs -text" for extension in FORMATS],
            ),
        ):
            path = root / filename
            if path.is_symlink():
                raise DomainError("scope_denied", 403)
            previous = path.read_text() if path.exists() else ""
            missing = [line for line in lines if line not in previous.splitlines()]
            if missing:
                # Git attributes are last-match-wins. Existing authoring rules take
                # precedence over the generic defaults, not the other way round.
                attributes = filename == ".gitattributes"
                with path.open("w" if attributes else "a") as stream:
                    additions = "\n".join(missing) + "\n"
                    stream.write(
                        additions + previous
                        if attributes
                        else (
                            ("\n" if previous and not previous.endswith("\n") else "") + additions
                        )
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
        if not (root / "project.json").exists():
            config = dict(
                schema_version="1.0",
                **(
                    identity
                    or dict(
                        project_id="project-" + str(uuid4()),
                        repository_id="repository-" + str(uuid4()),
                    )
                ),
                name=name,
                paths={directory: directory for directory in DIRECTORIES},
            )
            validate("ProjectConfig", config)
            atomic_bytes(root / "project.json", canonical(config))
            (root / "project.json").chmod(0o600)

    def open(
        self,
        caller: Caller,
        body: dict[str, Any],
        *,
        identity: dict[str, str] | None = None,
        install_lfs_hooks: bool = True,
    ) -> tuple[dict[str, Any], bool]:
        owner_only(caller)
        validate("ProjectOpen", body)
        registry = self.services.registry
        with self.services.activation_lock:
            with registry.store.transaction() as tx:
                previous = replay(tx, "projects.open", body["operation_id"], body)
                intent = tx.get("project_open_intents", body["operation_id"], required=False)
                if intent and intent["fingerprint"] != fingerprint(body):
                    raise DomainError("idempotency_conflict", 409)
                if not intent:
                    tx.put(
                        "project_open_intents",
                        body["operation_id"],
                        {"fingerprint": fingerprint(body), "state": "prepared"},
                    )
            if previous:
                return previous, True
            root = Path(body["path"]).expanduser().resolve()
            self.workspace_boundary(root)
            if body["create"]:
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                root = root.resolve(strict=True)
            except OSError as exc:
                raise DomainError("not_found", 404) from exc
            if not root.is_dir() or (root / ".proscenium").is_symlink():
                raise DomainError("scope_denied", 403)
            if body["create"] or not (root / "project.json").exists():
                self.bootstrap(
                    root,
                    body.get("name", root.name),
                    identity=identity,
                    install_lfs_hooks=install_lfs_hooks,
                )
            config = self.config(root)
            record = dict(
                schema_version="1.0",
                project_id=config["project_id"],
                repository_id=config["repository_id"],
                name=config["name"],
                path=str(root),
                native_session_id=None,
                state="inactive",
                state_revision=0,
            )
            runtime = root / ".proscenium"
            if runtime.is_symlink() or any(
                (runtime / name).is_symlink() for name in ("state.sqlite3", "owner.lock")
            ):
                raise DomainError("scope_denied", 403)
            project_store = Store(runtime / "state.sqlite3", record["project_id"])
            with project_store.transaction(write=False) as tx:
                identity = tx.get("project_identity", "identity", required=False)
                if identity and identity != {
                    "project_id": record["project_id"],
                    "repository_id": record["repository_id"],
                }:
                    raise DomainError("source_conflict", 409)
            registry.register(record)
            # Private defaults are machine-owned, not project.json preferences.
            with registry.store.transaction() as tx:
                key = "settings:" + record["project_id"]
                if not tx.get("private", key, required=False):
                    tx.put(
                        "private",
                        key,
                        dict(
                            generation_attempt_limit=self.services.settings.generation_attempt_limit,
                            default_profile_id=None,
                            state_revision=0,
                        ),
                        schema="ProjectSettingsRecord",
                    )
            with registry.project_store(record["project_id"], active=False).transaction() as tx:
                identity = {
                    "project_id": record["project_id"],
                    "repository_id": record["repository_id"],
                }
                old = tx.get("project_identity", "identity", required=False)
                if old and old != identity:
                    raise DomainError("source_conflict", 409)
                if not old:
                    tx.put("project_identity", "identity", identity)
            with registry.store.transaction() as tx:
                result, repeated = tx.operation(
                    "projects.open",
                    body["operation_id"],
                    body,
                    lambda: tx.require("projects", record["project_id"]),
                )
                saved = tx.require("project_open_intents", body["operation_id"])
                tx.put(
                    "project_open_intents",
                    body["operation_id"],
                    {**saved, "state": "completed"},
                    expected_revision=tx.revision("project_open_intents", body["operation_id"]),
                )
                return result, repeated

    def settings(self, project_id: str) -> dict[str, Any]:
        with self.services.registry.store.transaction(write=False) as tx:
            result = tx.get("private", "settings:" + project_id, required=False)
        return result or dict(
            generation_attempt_limit=self.services.settings.generation_attempt_limit,
            default_profile_id=None,
            state_revision=0,
        )

    def update_settings(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        owner_only(caller)
        validate("ProjectSettings", body)
        project_id = body["project_id"]
        self.services.registry.get(project_id)
        with self.services.registry.store.transaction() as tx:

            def effect() -> dict[str, Any]:
                if body["default_profile_id"] not in {
                    profile["profile_id"]
                    for profile in getattr(
                        self.services,
                        "effective_profiles",
                        lambda: list(self.services.settings.profiles),
                    )()
                    if profile["scope_status"] == "project_scoped"
                }:
                    raise DomainError("capability_unavailable", 501)
                key = "settings:" + project_id
                current = tx.require("private", key)
                if current["state_revision"] != body["expected_revision"]:
                    raise DomainError(
                        "revision_conflict", 409, current_revision=current["state_revision"]
                    )
                revision = current["state_revision"] + 1
                tx.put(
                    "private",
                    key,
                    {k: body[k] for k in ("generation_attempt_limit", "default_profile_id")}
                    | {"state_revision": revision},
                    expected_revision=tx.revision("private", key),
                    schema="ProjectSettingsRecord",
                )
                return receipt(body, project_id, "updated", revision)

            result, repeated = tx.operation("projects.settings", body["operation_id"], body, effect)
            return {**result, "replayed": repeated}, repeated

    def activate(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        owner_only(caller)
        validate("RevisionCommand", body)
        registry = self.services.registry
        project_id = body["project_id"]
        with self.services.activation_lock:
            with registry.store.transaction(write=False) as tx:
                previous = replay(tx, "projects.activate", body["operation_id"], body)
                pending = tx.get("project_commands", body["operation_id"], required=False)
            if previous:
                self.project_event(project_id, body, caller)
                return {**previous, "replayed": True}, True
            record = registry.get(project_id)
            if (Path(record["path"]) / ".proscenium/owner.lock").is_symlink():
                raise DomainError("scope_denied", 403)
            config = self.config(Path(record["path"]))
            if (config["project_id"], config["repository_id"]) != (
                project_id,
                record["repository_id"],
            ):
                raise DomainError("source_conflict", 409)
            if record["state_revision"] != body["expected_revision"]:
                raise DomainError(
                    "revision_conflict", 409, current_revision=record["state_revision"]
                )
            if (
                not pending
                and registry.active_id() == project_id
                and record["state"] in {"active", "awaiting_resume"}
            ):
                with registry.store.transaction() as tx:
                    result, repeated = tx.operation(
                        "projects.activate",
                        body["operation_id"],
                        body,
                        lambda: receipt(
                            body, project_id, record["state"], record["state_revision"]
                        ),
                    )
                return {**result, "replayed": repeated}, repeated
            if not pending:
                pending = {"body": body, "old": registry.active_id(), "actor_id": caller.subject_id}
                with registry.store.transaction() as tx:
                    tx.put("project_commands", body["operation_id"], pending)
            elif pending["body"] != body:
                raise DomainError("idempotency_conflict", 409)
            self.services.activate_project(project_id)
            self.services.gate.close(project_id)
            # Activation and its event each reconcile by the original owner operation ID.
            with registry.store.transaction() as tx:

                def effect() -> dict[str, Any]:
                    current = tx.require("projects", project_id)
                    revision = current["state_revision"] + 1
                    current.update(state="awaiting_resume", state_revision=revision)
                    tx.put(
                        "projects",
                        project_id,
                        current,
                        expected_revision=tx.revision("projects", project_id),
                        schema="ProjectRecord",
                    )
                    saved = tx.require("project_commands", body["operation_id"])
                    tx.put(
                        "project_commands",
                        body["operation_id"],
                        {**saved, "event_record": current},
                        expected_revision=tx.revision("project_commands", body["operation_id"]),
                    )
                    old_id = pending["old"] if pending else None
                    if old_id and old_id != project_id:
                        old = tx.require("projects", old_id)
                        old.update(state="inactive", state_revision=old["state_revision"] + 1)
                        tx.put(
                            "projects",
                            old_id,
                            old,
                            expected_revision=tx.revision("projects", old_id),
                            schema="ProjectRecord",
                        )
                    return receipt(body, project_id, "awaiting_resume", revision)

                result, repeated = tx.operation(
                    "projects.activate", body["operation_id"], body, effect
                )
            self.project_event(project_id, body, caller)
            return {**result, "replayed": repeated}, repeated

    def project_event(self, project_id: str, body: dict[str, Any], caller: Caller) -> None:
        with self.services.registry.store.transaction(write=False) as machine:
            saved = machine.get("project_commands", body["operation_id"], required=False)
        if not saved:
            return
        record = saved["event_record"]
        with self.services.registry.project_store(project_id, active=False).transaction() as tx:

            def effect() -> dict[str, Any]:
                tx.emit(
                    "project.state_changed", record, actor={"kind": "user", "id": saved["actor_id"]}
                )
                # An activation event may be repaired after W01 durably acquired its
                # native association. Keep its original historical payload, then
                # reassert the current known association in this same atomic replay.
                current = self.services.registry.get(project_id)
                if (
                    current["native_session_id"]
                    and current["native_session_id"] != record["native_session_id"]
                ):
                    tx.emit(
                        "project.state_changed",
                        current,
                        actor={"kind": "system", "id": "project-host"},
                    )
                return {"emitted": True}

            tx.operation("projects.event", body["operation_id"], body, effect)

    def snapshot(self, project_id: str) -> dict[str, Any]:
        from proscenium.copilot.eligibility import execution_status

        with self.services.activation_lock:
            self.services.reconcile_project_projection(project_id)
            record = self.services.registry.get(project_id)
            settings = self.settings(project_id)
            with self.services.registry.project_store(project_id).transaction(write=False) as tx:
                active = [
                    w
                    for w in tx.all("work")
                    if w["state"] not in {"completed", "failed", "stopped"}
                ]
                if len(active) > 1:
                    raise DomainError("source_conflict", 409, "Multiple active work records")
                result = dict(
                    schema_version="1.0",
                    project=record,
                    settings=settings,
                    event_sequence=tx.watermark(),
                    execution=execution_status(tx, self.services.ownership.epoch, record),
                    active_work=active[0] if active else None,
                    held_requests=[
                        r for r in tx.all("requests") if r["receipt"]["state"] == "held"
                    ],
                    items=[
                        self.services.extension("catalogue").item_view(tx, item)
                        for item in tx.all("items")
                    ],
                    jobs=tx.all("jobs"),
                    pending_decisions=[d for d in tx.all("decisions") if d["state"] == "pending"],
                    reviews=[
                        self.services.extension("reviews").ticket_view(
                            tx, tx.require("reviews", row[0])
                        )
                        if "reviews" in self.services.extensions
                        else tx.require("reviews", row[0])
                        for row in tx.connection.execute(
                            "SELECT id FROM records WHERE namespace='reviews' ORDER BY rowid DESC"
                        ).fetchall()
                    ],
                )
        validate("ProjectSnapshot", result)
        return result

    def rebind(self, caller: Caller, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        owner_only(caller)
        validate("ProjectRebind", body)
        registry = self.services.registry
        project_id = body["project_id"]
        with self.services.activation_lock:
            if Path(registry.get(project_id)["path"]).exists():
                self.services.reconcile_project_projection(project_id)
            with registry.store.transaction(write=False) as tx:
                previous = replay(tx, "projects.rebind", body["operation_id"], body)
            if previous:
                return {**previous, "replayed": True}, True
            record = registry.get(project_id)
            if record["state_revision"] != body["expected_revision"]:
                raise DomainError(
                    "revision_conflict", 409, current_revision=record["state_revision"]
                )
            root = Path(body["new_path"]).expanduser().resolve(strict=True)
            self.workspace_boundary(root)
            config = self.config(root)
            if (config["project_id"], config["repository_id"]) != (
                project_id,
                record["repository_id"],
            ):
                raise DomainError("source_conflict", 409)
            if (root / ".proscenium").is_symlink() or (
                root / ".proscenium/state.sqlite3"
            ).is_symlink():
                raise DomainError("scope_denied", 403)
            if not (root / ".proscenium/state.sqlite3").is_file():
                raise DomainError("source_unavailable", 422, "Relocated project database missing")
            target = Store(root / ".proscenium/state.sqlite3", project_id)
            if Path(record["path"]).exists() and Path(record["path"]) != root:
                registry.assert_settled(project_id)
                original = registry.project_store(project_id, active=False)
                if self.store_digest(original) != self.store_digest(target):
                    raise DomainError(
                        "source_conflict",
                        409,
                        "Relocated database differs from the settled original",
                    )
            with target.transaction(write=False) as tx:
                association = tx.get("copilot.session", "native", required=False)
                if association:
                    if record["native_session_id"] not in {None, association["session_id"]}:
                        raise DomainError("source_conflict", 409, "Native session identity changed")
                    record = {**record, "native_session_id": association["session_id"]}
                if tx.require("project_identity", "identity") != {
                    "project_id": project_id,
                    "repository_id": record["repository_id"],
                }:
                    raise DomainError("source_conflict", 409)
                terminal = {
                    "work": {"completed", "failed", "stopped"},
                    "jobs": {"succeeded", "failed", "cancelled"},
                    "invocations": {"completed", "failed", "cancelled"},
                    "decisions": {"answered", "declined", "cancelled", "expired"},
                }
                for namespace, states in terminal.items():
                    if any(row["state"] not in states for row in tx.all(namespace)):
                        raise DomainError("project_busy", 409)
                if tx.connection.execute(
                    "SELECT 1 FROM outbox WHERE state IN ('dispatching','outcome_uncertain')"
                ).fetchone():
                    raise DomainError("project_busy", 409)
                for artifact in tx.all("artifacts"):
                    if artifact["state"] == "ready":
                        path = managed(
                            root,
                            f".proscenium/artefacts/sha256/{artifact['sha256']}/content",
                            exists=True,
                        )
                        if sha(path.read_bytes()) != artifact["sha256"]:
                            raise DomainError("hash_mismatch", 422)
                for retained in tx.all("retained_sources"):
                    path = managed(
                        root, f".proscenium/retained/{retained['sha256']}/content", exists=True
                    )
                    if sha(path.read_bytes()) != retained["sha256"]:
                        raise DomainError("retained_source_missing", 422)
                for version in tx.all("versions"):
                    git(root, "cat-file", "-e", version["source_commit"] + "^{commit}")
                    from proscenium.sources.resolver import Resolver

                    for row in version["source"]["files"]:
                        if row["origin"]["kind"] == "git":
                            Resolver(self.services).git_bytes(project_id, row["origin"], root=root)
            if record["native_session_id"]:
                verifier = self.services.extensions.get("native_rebind_verifier")
                if verifier is None:
                    raise DomainError(
                        "native_session_missing",
                        503,
                        "Native history relocation needs runtime verification",
                    )
                verifier(project_id, root, record["native_session_id"])
            held = self.services.project_ownership.get(project_id)
            if (root / ".proscenium/owner.lock").is_symlink():
                raise DomainError("scope_denied", 403)
            target_lock = root / ".proscenium/owner.lock"
            same_lock = False
            if held and target_lock.exists():
                previous_stat, target_stat = os.fstat(held.file.fileno()), target_lock.stat()
                same_lock = (previous_stat.st_dev, previous_stat.st_ino) == (
                    target_stat.st_dev,
                    target_stat.st_ino,
                )
            new_lock = None if same_lock else Ownership(root / ".proscenium/owner.lock")
            try:
                with registry.store.transaction() as tx:

                    def effect() -> dict[str, Any]:
                        if any(
                            p["path"] == str(root) and p["project_id"] != project_id
                            for p in tx.all("projects")
                        ):
                            raise DomainError("project_location_conflict", 409)
                        revision = record["state_revision"] + 1
                        tx.put(
                            "projects",
                            project_id,
                            {**record, "path": str(root), "state_revision": revision},
                            expected_revision=body["expected_revision"],
                            schema="ProjectRecord",
                        )
                        return receipt(body, project_id, "rebound", revision)

                    result, repeated = tx.operation(
                        "projects.rebind", body["operation_id"], body, effect
                    )
                if new_lock:
                    if registry.active_id() == project_id:
                        if held:
                            held.close()
                        self.services.project_ownership[project_id] = new_lock
                        new_lock = None
                self.services.gate.close(project_id)
                return {**result, "replayed": repeated}, repeated
            finally:
                if new_lock:
                    new_lock.close()

    @staticmethod
    def store_digest(store: Store) -> str:
        tables = (
            "records",
            "operations",
            "events",
            "outbox",
            "budget_reservations",
            "resource_claims",
        )
        with store.transaction(write=False) as tx:
            result = {}
            for table in tables:
                columns = len(tx.connection.execute(f"PRAGMA table_info({table})").fetchall())
                order = ",".join(str(index + 1) for index in range(columns))
                result[table] = [
                    list(row)
                    for row in tx.connection.execute(f"SELECT * FROM {table} ORDER BY {order}")
                ]
            return fingerprint(result)
