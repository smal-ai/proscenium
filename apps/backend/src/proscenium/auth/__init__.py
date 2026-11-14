import hashlib
import secrets
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import Request, WebSocket

from proscenium.api.errors import DomainError
from proscenium.contracts import validate
from proscenium.db.store import Store

COOKIE_NAME = "proscenium_owner"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Caller:
    channel: str
    subject_id: str
    project_id: str | None = None
    work_id: str | None = None
    owner_epoch: str | None = None
    job_id: str | None = None
    attempt_id: str | None = None
    input_digest: str | None = None
    operations: tuple[str, ...] = ()
    invocation_id: str | None = None

    def permits(self, operation: str, project_id: str | None = None) -> None:
        if self.channel != "owner" and operation not in self.operations:
            raise DomainError("scope_denied", 403)
        if self.channel != "owner" and project_id != self.project_id:
            raise DomainError("scope_denied", 403)


class Auth:
    """Private machine-store capabilities. Never put these records in project context/events."""

    def __init__(self, store: Store):
        self.store = store

    def issue_pairing(self) -> str:
        code = secrets.token_urlsafe(48)
        with self.store.transaction() as tx:
            tx.put("auth.pairing", digest(code), {"expires": time.time() + 900, "binding": None})
        return code

    def pair(self, body: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        validate("OwnerBootstrap", body)
        with self.store.transaction() as tx:
            identity = digest(body["pairing_code"])
            record = tx.get("auth.pairing", identity, required=False)
            if not record:
                raise DomainError("invalid_pairing", 401)
            binding = [body["operation_id"], body["client_nonce"]]
            if record["binding"] is not None:
                if record["binding"] != binding:
                    raise DomainError("invalid_pairing", 401)
                session = tx.require("auth.sessions", digest(record["cookie"]))
                if session["expires"] <= time.time():
                    raise DomainError("invalid_pairing", 401)
                return self.session_view(session), record["cookie"], True
            if record["expires"] <= time.time():
                raise DomainError("invalid_pairing", 401)
            # Operation IDs cannot be recycled with another code/client either.
            if tx.get("auth.pair_operations", body["operation_id"], required=False):
                raise DomainError("idempotency_conflict", 409)
            cookie = secrets.token_urlsafe(48)
            session = {
                "owner_session_id": f"owner-{uuid4()}",
                "csrf_token": secrets.token_urlsafe(48),
                "expires": time.time() + 43200,
            }
            tx.put("auth.sessions", digest(cookie), session)
            tx.put("auth.pair_operations", body["operation_id"], {"binding": binding})
            tx.put(
                "auth.pairing",
                identity,
                {**record, "binding": binding, "cookie": cookie},
                expected_revision=tx.revision("auth.pairing", identity),
            )
            return self.session_view(session), cookie, False

    @staticmethod
    def session_view(session: dict[str, Any]) -> dict[str, Any]:
        return dict(
            schema_version="1.0",
            owner_session_id=session["owner_session_id"],
            csrf_token=session["csrf_token"],
            expires_at=datetime.fromtimestamp(session["expires"], UTC).isoformat(),
        )

    def issue_capability(self, caller: Caller) -> str:
        if (
            caller.channel not in {"tool", "worker"}
            or not caller.project_id
            or not caller.operations
        ):
            raise ValueError("Capabilities require a project and explicit operation allowlist")
        if caller.channel == "tool" and (not caller.work_id or not caller.owner_epoch):
            raise ValueError("Tool capability requires admitted work and owner epoch")
        if caller.channel == "worker" and not all(
            (caller.job_id, caller.attempt_id, caller.input_digest)
        ):
            raise ValueError("Worker capability requires exact admitted attempt and input")
        token = secrets.token_urlsafe(48)
        with self.store.transaction() as tx:
            tx.put("auth.capabilities", digest(token), asdict(caller))
        return token

    def owner(self, request: Request) -> Caller:
        return self._owner(
            request.cookies.get(COOKIE_NAME),
            request.headers.get("X-CSRF-Token", ""),
            request.method not in {"GET", "HEAD", "OPTIONS"},
        )

    def owner_socket(self, socket: WebSocket) -> Caller:
        return self._owner(socket.cookies.get(COOKIE_NAME), "", False)

    def _owner(self, cookie: str | None, csrf: str, mutation: bool) -> Caller:
        if not cookie:
            raise DomainError("owner_session_required", 401)
        with self.store.transaction() as tx:
            identity = digest(cookie)
            session = tx.get("auth.sessions", identity, required=False)
            if not session or session["expires"] <= time.time():
                raise DomainError("owner_session_required", 401)
            if mutation:
                if not secrets.compare_digest(csrf, session["csrf_token"]):
                    raise DomainError("permission_denied", 403)
            session["expires"] = time.time() + 43200
            tx.put(
                "auth.sessions",
                identity,
                session,
                expected_revision=tx.revision("auth.sessions", identity),
            )
            return Caller("owner", session["owner_session_id"])

    def capability(self, request: Request, channels: set[str]) -> Caller:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise DomainError("capability_required", 401)
        with self.store.transaction(write=False) as tx:
            record = tx.get("auth.capabilities", digest(header[7:]), required=False)
        if not record or record["channel"] not in channels:
            raise DomainError("capability_required", 401)
        record["operations"] = tuple(record["operations"])
        return Caller(**record)

    def revoke(self, token: str) -> None:
        with self.store.transaction() as tx:
            tx.connection.execute(
                "DELETE FROM records WHERE namespace='auth.capabilities' AND id=?", (digest(token),)
            )
