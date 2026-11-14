from __future__ import annotations

import builtins
import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from proscenium.api.errors import DomainError
from proscenium.contracts import canonical, validate

MIGRATIONS = [
    """
    CREATE TABLE records (
      namespace TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=0),
      body TEXT NOT NULL CHECK(json_valid(body)), PRIMARY KEY(namespace,id));
    CREATE TABLE operations (
      scope TEXT NOT NULL, id TEXT NOT NULL, fingerprint TEXT NOT NULL,
      result TEXT NOT NULL CHECK(json_valid(result)), PRIMARY KEY(scope,id));
    CREATE TABLE events (
      sequence INTEGER PRIMARY KEY AUTOINCREMENT, native_session_id TEXT, native_event_id TEXT,
      body TEXT NOT NULL CHECK(json_valid(body)), UNIQUE(native_session_id,native_event_id));
    CREATE TABLE outbox (
      id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
      body TEXT NOT NULL CHECK(json_valid(body)), evidence TEXT);
    CREATE TABLE budget_reservations (
      id TEXT PRIMARY KEY, task_id TEXT NOT NULL, target_id TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('reserved','consumed','released')));
    CREATE TABLE resource_claims (
      resource TEXT PRIMARY KEY, project_id TEXT NOT NULL, job_id TEXT NOT NULL, attempt_id TEXT NOT NULL);
    """
]


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path, project_id: str | None = None):
        self.path, self.project_id = path, project_id
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.migrate()
        path.chmod(0o600)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def migrate(self) -> None:
        connection = self.connect()
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise DomainError(
                    "unsupported_contract", 415, "Database is newer than this backend"
                )
            if version == len(MIGRATIONS):
                # Opening another read handle inside a service transaction must not
                # attempt a second write transaction merely to verify the schema.
                return
            if version and version < len(MIGRATIONS):
                self.backup(self.path.with_suffix(f".pre-v{version + 1}.sqlite3"))
            # Execute individual statements: executescript implicitly commits and breaks rollback.
            connection.execute("BEGIN IMMEDIATE")
            for index in range(version, len(MIGRATIONS)):
                for statement in MIGRATIONS[index].split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(f"PRAGMA user_version={index + 1}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def backup(self, destination: Path) -> None:
        if destination.exists():
            raise DomainError("source_conflict", 409, "Backup destination already exists")
        source, target = self.connect(), sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
        destination.chmod(0o600)

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[Transaction]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield Transaction(connection, self.project_id)
            connection.commit()
        except sqlite3.OperationalError as exc:
            connection.rollback()
            code = "storage_full" if "full" in str(exc).lower() else "storage_unavailable"
            raise DomainError(code, 507 if code == "storage_full" else 503) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class Transaction:
    def __init__(self, connection: sqlite3.Connection, project_id: str | None):
        self.connection, self.project_id = connection, project_id

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """Nested database-only work, never an independent commit or connection."""
        name = f"sp_{uuid4().hex}"
        self.connection.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self.connection.execute(f"ROLLBACK TO {name}")
            raise
        finally:
            self.connection.execute(f"RELEASE {name}")

    def get(self, namespace: str, identity: str, *, required: bool = True) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT body FROM records WHERE namespace=? AND id=?", (namespace, identity)
        ).fetchone()
        if row is None:
            if required:
                raise DomainError("not_found", 404)
            return None
        return json.loads(row[0])

    def require(self, namespace: str, identity: str) -> dict[str, Any]:
        result = self.get(namespace, identity)
        assert result is not None
        return result

    def put(
        self,
        namespace: str,
        identity: str,
        body: dict[str, Any],
        *,
        expected_revision: int | None = None,
        schema: str | None = None,
    ) -> int:
        """None creates only. Updates require the current storage revision, independent of wire fields."""
        if schema:
            validate(schema, body)
        if self.project_id and "project_id" in body and body["project_id"] != self.project_id:
            raise DomainError("scope_denied", 403)
        row = self.connection.execute(
            "SELECT revision FROM records WHERE namespace=? AND id=?", (namespace, identity)
        ).fetchone()
        current = None if row is None else row[0]
        if current != expected_revision:
            raise DomainError("revision_conflict", 409, current_revision=current)
        revision = 0 if current is None else current + 1
        if "state_revision" in body and body["state_revision"] != revision:
            raise DomainError("revision_conflict", 409, current_revision=current)
        self.connection.execute(
            "INSERT INTO records VALUES (?,?,?,?) ON CONFLICT(namespace,id) DO UPDATE SET "
            "revision=excluded.revision,body=excluded.body",
            (namespace, identity, revision, canonical(body).decode()),
        )
        return revision

    def revision(self, namespace: str, identity: str) -> int:
        row = self.connection.execute(
            "SELECT revision FROM records WHERE namespace=? AND id=?", (namespace, identity)
        ).fetchone()
        if row is None:
            raise DomainError("not_found", 404)
        return row[0]

    def list(self, namespace: str, *, after: str = "", limit: int = 100) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT body FROM records WHERE namespace=? AND id>? ORDER BY id LIMIT ?",
                (namespace, after, limit),
            )
        ]

    def all(self, namespace: str) -> builtins.list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT body FROM records WHERE namespace=? ORDER BY id", (namespace,)
            )
        ]

    def operation(
        self, scope: str, identity: str, body: Any, effect: Callable[[], dict[str, Any]]
    ) -> tuple[dict[str, Any], bool]:
        """Effect MUST be database-only. External dispatch belongs in the durable outbox."""
        fingerprint = hashlib.sha256(canonical(body)).hexdigest()
        row = self.connection.execute(
            "SELECT fingerprint,result FROM operations WHERE scope=? AND id=?", (scope, identity)
        ).fetchone()
        if row:
            if row[0] != fingerprint:
                raise DomainError("idempotency_conflict", 409)
            return json.loads(row[1]), True
        result = effect()
        self.connection.execute(
            "INSERT INTO operations VALUES (?,?,?,?)",
            (scope, identity, fingerprint, canonical(result).decode()),
        )
        return result, False

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor: dict[str, str],
        request_id: str | None = None,
        work_id: str | None = None,
        native_session_id: str | None = None,
        native_event_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.project_id:
            raise ValueError("Public events belong in a project store")
        if native_event_id and not native_session_id:
            raise DomainError("attribution_unavailable", 409)
        if native_event_id:
            row = self.connection.execute(
                "SELECT body FROM events WHERE native_session_id=? AND native_event_id=?",
                (native_session_id, native_event_id),
            ).fetchone()
            if row:
                old = json.loads(row[0])
                if old["event_type"] != event_type or old["payload"] != payload:
                    raise DomainError("idempotency_conflict", 409)
                return old
        sequence = self.watermark() + 1
        event = dict(
            schema_version="1.0",
            project_id=self.project_id,
            sequence=sequence,
            occurred_at=now(),
            event_type=event_type,
            payload=payload,
            actor=actor,
            request_id=request_id,
            work_id=work_id,
            native_session_id=native_session_id,
            native_event_id=native_event_id,
        )
        validate("Event", event)
        self.connection.execute(
            "INSERT INTO events VALUES (?,?,?,?)",
            (sequence, native_session_id, native_event_id, canonical(event).decode()),
        )
        return event

    def watermark(self) -> int:
        return self.connection.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]

    def events(self, after: int = 0, limit: int = 50) -> dict[str, Any]:
        last = self.watermark()
        if after > last:
            raise DomainError("cursor_expired", 410)
        if after < 0 or not 1 <= limit <= 100:
            raise DomainError("invalid_cursor")
        events = [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT body FROM events WHERE sequence>? ORDER BY sequence LIMIT ?", (after, limit)
            )
        ]
        return dict(
            schema_version="1.0",
            events=events,
            next_after=events[-1]["sequence"] if events else after,
            last_available=last,
        )

    def enqueue(self, identity: str, kind: str, body: dict[str, Any]) -> None:
        encoded = canonical(body).decode()
        row = self.connection.execute(
            "SELECT kind,body FROM outbox WHERE id=?", (identity,)
        ).fetchone()
        if row:
            if tuple(row) != (kind, encoded):
                raise DomainError("idempotency_conflict", 409)
            return
        self.connection.execute(
            "INSERT INTO outbox VALUES (?,?,'pending',?,NULL)", (identity, kind, encoded)
        )

    def claim_intent(self, identity: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT state,body FROM outbox WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise DomainError("not_found", 404)
        if row[0] != "pending":
            raise DomainError("invalid_state_transition", message="Intent requires reconciliation")
        self.connection.execute("UPDATE outbox SET state='dispatching' WHERE id=?", (identity,))
        return json.loads(row[1])

    def settle_intent(self, identity: str, state: str, evidence: dict[str, Any]) -> None:
        if state not in {"acknowledged", "outcome_uncertain", "failed"} or not evidence:
            raise DomainError("invalid_request")
        changed = self.connection.execute(
            "UPDATE outbox SET state=?,evidence=? WHERE id=? AND state IN "
            "('dispatching','outcome_uncertain')",
            (state, canonical(evidence).decode(), identity),
        ).rowcount
        if not changed:
            raise DomainError("invalid_state_transition")
