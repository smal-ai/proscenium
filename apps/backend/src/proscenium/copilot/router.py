"""W01 owner endpoints. Connections only read/admit, never own runtime execution."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from proscenium.api.dependencies import command, owner, owner_websocket
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.services import get_services

from .service import Copilot

router = APIRouter()
Owner = Annotated[Caller, Depends(owner)]


def runtime(request: Request) -> Copilot:
    return get_services(request).extension("copilot")  # type: ignore[no-any-return]


@router.post("/api/v1/projects/{project_id}/contexts", operation_id="captureContext")
async def capture_context(request: Request, project_id: str, caller: Owner) -> JSONResponse:
    body = await command(request, "ContextSnapshot", project_id=project_id)
    result, replayed = runtime(request).contexts.capture(body)
    return JSONResponse(result, status_code=200 if replayed else 201)


@router.get("/api/v1/projects/{project_id}/contexts/{context_id}", operation_id="getContext")
def get_context(request: Request, project_id: str, context_id: str, caller: Owner) -> dict:
    return runtime(request).contexts.get(project_id, context_id)


@router.post("/api/v1/projects/{project_id}/requests", operation_id="submitRequest")
async def submit_request(request: Request, project_id: str, caller: Owner) -> JSONResponse:
    body = await command(request, "MessageSubmit", project_id=project_id)
    service = runtime(request)
    result, replayed = service.state.submit(caller, body)
    service.notify()
    return JSONResponse(result, status_code=200 if replayed else 202)


@router.get("/api/v1/projects/{project_id}/requests", operation_id="listRequests")
def list_requests(
    request: Request, project_id: str, caller: Owner, cursor: str | None = None, limit: int = 50
) -> dict:
    services = get_services(request)
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        keyed = []
        for record in tx.all("requests"):
            accepted = datetime.fromisoformat(
                record["accepted_at"].replace("Z", "+00:00")
            ).astimezone(UTC)
            elapsed = accepted - datetime(1970, 1, 1, tzinfo=UTC)
            epoch_ns = (
                elapsed.days * 86400 + elapsed.seconds
            ) * 1_000_000_000 + elapsed.microseconds * 1000
            keyed.append({"order": f"{epoch_ns:024d}:{record['request_id']}", "record": record})
        page = services.cursors.page(
            f"{project_id}:requests:accepted:v2",
            keyed,
            "order",
            cursor=cursor,
            limit=limit,
        )
        return validate("RequestPage", {**page, "items": [row["record"] for row in page["items"]]})  # type: ignore[no-any-return]


@router.get("/api/v1/projects/{project_id}/requests/{request_id}", operation_id="getRequest")
def get_request(request: Request, project_id: str, request_id: str, caller: Owner) -> dict:
    with get_services(request).registry.project_store(project_id).transaction(write=False) as tx:
        return tx.require("requests", request_id)


@router.post(
    "/api/v1/projects/{project_id}/requests/{request_id}/cancel", operation_id="cancelHeldRequest"
)
async def cancel_request(
    request: Request, project_id: str, request_id: str, caller: Owner
) -> JSONResponse:
    body = await command(request, "RevisionCommand", project_id=project_id)
    result, replayed = runtime(request).state.cancel(caller, request_id, body)
    return JSONResponse({**result, "replayed": replayed}, status_code=200 if replayed else 202)


@router.post("/api/v1/projects/{project_id}/resume", operation_id="resumeProjectWork")
async def resume_project(request: Request, project_id: str, caller: Owner) -> JSONResponse:
    body = await command(request, "ResumeCommand", project_id=project_id)
    service = runtime(request)
    result, replayed = service.admit_resume(caller, body)
    service.notify()
    return JSONResponse({**result, "replayed": replayed}, status_code=200 if replayed else 202)


@router.post(
    "/api/v1/projects/{project_id}/decisions/{decision_id}/answer",
    operation_id="answerOwnerDecision",
)
async def answer_decision(
    request: Request, project_id: str, decision_id: str, caller: Owner
) -> JSONResponse:
    body = await command(request, "DecisionAnswer", project_id=project_id)
    service = runtime(request)
    result, replayed = service.state.answer(caller, decision_id, body)
    service.notify()
    return JSONResponse({**result, "replayed": replayed})


@router.get(
    "/api/v1/projects/{project_id}/resumes/{operation_id}", operation_id="getResumeOperation"
)
def get_resume_operation(
    request: Request, project_id: str, operation_id: str, caller: Owner
) -> dict:
    with get_services(request).registry.project_store(project_id).transaction(write=False) as tx:
        record = tx.require("copilot.resumes", operation_id)
        body = record["body"]
        if body["project_id"] != project_id:
            raise DomainError("scope_denied", 403)
        return validate(
            "ResumeOperationStatus",
            dict(
                schema_version="1.0",
                operation_id=operation_id,
                project_id=project_id,
                mode=body["mode"],
                work_id=body["work_id"],
                state=record["state"],
                detail_code=record.get("detail_code"),
            ),
        )  # type: ignore[no-any-return]


@router.get("/api/v1/projects/{project_id}/events", operation_id="replayProjectEvents")
def replay_events(
    request: Request,
    project_id: str,
    caller: Owner,
    after: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    with get_services(request).registry.project_store(project_id).transaction(write=False) as tx:
        return tx.events(after, limit)


@router.websocket("/api/v1/events", name="connectProjectEvents")
async def events(socket: WebSocket) -> None:
    owner_websocket(socket)
    await socket.accept()
    services = socket.app.state.services

    async def send(value: dict[str, Any]) -> None:
        # Bounded write time closes slow consumers. SQLite is the durable backlog.
        await asyncio.wait_for(socket.send_json(value), 5)

    try:
        subscription = validate("Subscription", await asyncio.wait_for(socket.receive_json(), 10))
        project_id, after = subscription["project_id"], subscription["after_sequence"]
        store = services.registry.project_store(project_id)
        with store.transaction(write=False) as tx:
            watermark = tx.watermark()
            tx.events(after, 1)  # Reject ahead-of-journal cursor before welcome.
        await send(
            dict(
                type="welcome",
                schema_version="1.0",
                project_id=project_id,
                earliest_sequence=1 if watermark else 0,
                last_sequence=watermark,
            )
        )
        caught_up = False
        while True:
            with store.transaction(write=False) as tx:
                page = tx.events(after, 100)
            for event in page["events"]:
                if not caught_up and event["sequence"] > watermark:
                    await send(
                        dict(
                            type="caught_up",
                            schema_version="1.0",
                            project_id=project_id,
                            through_sequence=watermark,
                        )
                    )
                    caught_up = True
                await send(event)
                after = event["sequence"]
            if not caught_up and after >= watermark:
                await send(
                    dict(
                        type="caught_up",
                        schema_version="1.0",
                        project_id=project_id,
                        through_sequence=watermark,
                    )
                )
                caught_up = True
            try:
                message = await asyncio.wait_for(socket.receive_json(), 0.1)
                subscription = validate("Subscription", message)
                project_id, after = subscription["project_id"], subscription["after_sequence"]
                store = services.registry.project_store(project_id)
                with store.transaction(write=False) as tx:
                    watermark = tx.watermark()
                    tx.events(after, 1)
                await send(
                    dict(
                        type="welcome",
                        schema_version="1.0",
                        project_id=project_id,
                        earliest_sequence=1 if watermark else 0,
                        last_sequence=watermark,
                    )
                )
                caught_up = False
            except TimeoutError:
                pass
    except DomainError as exc:
        if exc.code == "cursor_expired":
            await send(
                dict(
                    type="resync_required",
                    schema_version="1.0",
                    project_id=project_id,
                    reason="cursor_expired",
                )
            )
        await socket.close(code=1008, reason=exc.code)
    except (WebSocketDisconnect, TimeoutError):
        with suppress(RuntimeError):
            await socket.close(code=1013)
