from typing import Any

from fastapi import Request, WebSocket, WebSocketException
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.services import get_services


def owner(request: Request) -> Caller:
    return get_services(request).auth.owner(request)


def owner_websocket(socket: WebSocket) -> Caller:
    try:
        return socket.app.state.services.auth.owner_socket(socket)
    except DomainError as exc:
        raise WebSocketException(code=1008, reason=exc.code) from exc


def tool(request: Request) -> Caller:
    return get_services(request).auth.capability(request, {"tool"})


def tool_or_worker(request: Request) -> Caller:
    return get_services(request).auth.capability(request, {"tool", "worker"})


async def command(
    request: Request, schema: str, *, project_id: str | None = None, job_id: str | None = None
) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise DomainError("unsupported_media_type", 415)
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise DomainError("invalid_request") from exc
    validate(schema, body)
    identity = next(
        (
            body[k]
            for k in (
                "operation_id",
                "request_id",
                "publication_id",
                "review_id",
                "context_id",
                "receipt_id",
                "segment_id",
                "job_id",
            )
            if k in body
        ),
        None,
    )
    if not identity or request.headers.get("Idempotency-Key") != identity:
        raise DomainError("invalid_request", message="Idempotency-Key must equal command identity")
    if project_id is not None and body.get("project_id") != project_id:
        raise DomainError("scope_denied", 403)
    if job_id is not None and body.get("job_id") != job_id:
        raise DomainError("scope_denied", 403)
    return body
