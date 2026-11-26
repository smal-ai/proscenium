from __future__ import annotations

import builtins
import hashlib
import json
import sqlite3
import stat
from _thread import LockType
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Any, TypeVar
from uuid import uuid4
from weakref import WeakValueDictionary

from proscenium.api.errors import DomainError
from proscenium.contracts import canonical, validate

_MIGRATION_LOCKS: WeakValueDictionary[Path, LockType] = WeakValueDictionary()
_MIGRATION_LOCKS_GUARD = Lock()
_T = TypeVar("_T")

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


def _schema_version(connection: sqlite3.Connection) -> int:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > len(MIGRATIONS):
        raise DomainError("unsupported_contract", 415, "Database is newer than this backend")
    return version


def _migration_lock(path: Path) -> LockType:
    key = path.resolve()
    with _MIGRATION_LOCKS_GUARD:
        lock = _MIGRATION_LOCKS.get(key)
        if lock is None:
            lock = _MIGRATION_LOCKS[key] = Lock()
        return lock


def _retry_busy(operation: Callable[[], _T]) -> _T:
    # A WAL transition can return BUSY without waiting for SQLite's busy handler.
    # Do not multiply the configured busy timeout when SQLite does wait.
    deadline = monotonic() + 0.4
    for attempt in range(21):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if (
                attempt == 20
                or monotonic() >= deadline
                or not ("locked" in message or "busy" in message)
            ):
                raise
            sleep(0.02)
    raise AssertionError("unreachable")


class Store:
    # Recovery's read-only Store adapter verifies its source independently and
    # bypasses this constructor. Only normal Store handles opt into migration.
    _verify_transactions: bool = False

    def __init__(
        self,
        path: Path,
        project_id: str | None = None,
        *,
        defer_migrate: bool = False,
        write_barrier: Any = None,
        write_guard: Callable[[], None] | None = None,
        before_commit: Callable[[Transaction], None] | None = None,
        after_commit: Callable[[Transaction], None] | None = None,
    ):
        self.path, self.project_id = path, project_id
        self.write_barrier, self.write_guard = write_barrier, write_guard
        self.before_commit, self.after_commit = before_commit, after_commit
        self._verify_transactions = True
        try:
            st = path.stat()
        except FileNotFoundError:
            st = None
        if defer_migrate and st is not None and stat.S_ISREG(st.st_mode):
            if stat.S_IMODE(st.st_mode) != 0o600:
                path.chmod(0o600)
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.migrate()
        path.chmod(0o600)

    def connect(self, *, set_wal: bool = False) -> sqlite3.Connection:
        if set_wal:
            return _retry_busy(lambda: self._open_connection(set_wal=True))
        return self._open_connection(set_wal=False)

    def _open_connection(self, *, set_wal: bool) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            if set_wal:
                mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if str(mode).lower() != "wal":
                    raise DomainError("storage_unavailable", 503, "SQLite WAL mode is unavailable")
            connection.execute("PRAGMA synchronous=FULL")
        except BaseException:
            connection.close()
            raise
        return connection

    def migrate(self) -> None:
        with _migration_lock(self.path), closing(self.connect(set_wal=True)) as connection:
            version = _retry_busy(lambda: _schema_version(connection))
            if version == len(MIGRATIONS):
                # Opening another read handle inside a service transaction must not
                # attempt a second write transaction merely to verify the schema.
                return
            if version and version < len(MIGRATIONS):
                self.backup(self.path.with_suffix(f".pre-v{version + 1}.sqlite3"))
            _retry_busy(lambda: self._apply_migrations(connection))

    @staticmethod
    def _apply_migrations(connection: sqlite3.Connection) -> None:
        try:
            # Execute individual statements: executescript implicitly commits and breaks rollback.
            connection.execute("BEGIN IMMEDIATE")
            # A separate process may have migrated since the initial version check.
            version = _schema_version(connection)
            for index in range(version, len(MIGRATIONS)):
                for statement in MIGRATIONS[index].split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(f"PRAGMA user_version={index + 1}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

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
        # All service-created owner stores share this reentrant barrier. Read handles
        # remain independent, including nested reads made inside an existing writer.
        with self.write_barrier if write and self.write_barrier is not None else nullcontext():
            if write and self.write_guard:
                self.write_guard()
            with self._transaction(write=write) as tx:
                yield tx
            if write and self.after_commit:
                self.after_commit(tx)

    def _connect_verified(self) -> sqlite3.Connection:
        connection = self.connect()
        if not self._verify_transactions:
            return connection
        try:
            # Only SQLite may inspect this file. Closing a raw file descriptor
            # can release the POSIX locks of other SQLite handles in this process.
            version = _schema_version(connection)
            mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        except BaseException:
            connection.close()
            raise
        if version < len(MIGRATIONS) or mode != "wal":
            connection.close()
            self.migrate()
            return self.connect()
        return connection

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[Transaction]:
        try:
            with closing(self._connect_verified()) as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                    tx = Transaction(connection, self.project_id)
                    yield tx
                    if write and self.before_commit:
                        self.before_commit(tx)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except sqlite3.OperationalError as exc:
            code = "storage_full" if "full" in str(exc).lower() else "storage_unavailable"
            raise DomainError(code, 507 if code == "storage_full" else 503) from exc


class Transaction:
    def __init__(self, connection: sqlite3.Connection, project_id: str | None):
        self.connection, self.project_id = connection, project_id
        self.changed_namespaces: set[str] = set()
        self.emitted_events = False

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """Nested database-only work, never an independent commit or connection."""
        name = f"sp_{uuid4().hex}"
        changed_namespaces = self.changed_namespaces.copy()
        emitted_events = self.emitted_events
        self.connection.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self.connection.execute(f"ROLLBACK TO {name}")
            self.changed_namespaces = changed_namespaces
            self.emitted_events = emitted_events
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
        encoded = canonical(body)
        self.connection.execute(
            "INSERT INTO records VALUES (?,?,?,?) ON CONFLICT(namespace,id) DO UPDATE SET "
            "revision=excluded.revision,body=excluded.body",
            (namespace, identity, revision, encoded.decode()),
        )
        self.changed_namespaces.add(namespace)
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

    def all_raw(self, namespace: str) -> builtins.list[tuple[str, dict[str, Any]]]:
        records = []
        for (raw,) in self.connection.execute(
            "SELECT body FROM records WHERE namespace=? ORDER BY id", (namespace,)
        ):
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError) as exc:
                raise DomainError(
                    "invalid_request", message=f"Invalid record in {namespace}"
                ) from exc
            if not isinstance(parsed, dict):
                raise DomainError("invalid_request", message=f"Invalid record in {namespace}")
            records.append((raw, parsed))
        return records

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
        self.emitted_events = True
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
        self.changed_namespaces.add("outbox")

    def claim_intent(self, identity: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT state,body FROM outbox WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise DomainError("not_found", 404)
        if row[0] != "pending":
            raise DomainError("invalid_state_transition", message="Intent requires reconciliation")
        self.connection.execute("UPDATE outbox SET state='dispatching' WHERE id=?", (identity,))
        self.changed_namespaces.add("outbox")
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
        self.changed_namespaces.add("outbox")
