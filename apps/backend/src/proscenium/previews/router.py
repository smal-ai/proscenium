from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from proscenium.api.dependencies import command, owner, tool
from proscenium.auth import Caller
from proscenium.services import get_services

from .service import PreviewService

router = APIRouter()
Owner = Annotated[Caller, Depends(owner)]
Tool = Annotated[Caller, Depends(tool)]
BASE = "/api/v1/projects/{project_id}/jobs/{job_id}"


def worker(request: Request) -> Caller:
    return get_services(request).auth.capability(request, {"worker"})


Worker = Annotated[Caller, Depends(worker)]


@router.get(BASE + "/availability", operation_id="getFrameAvailability")
def availability(request: Request, project_id: str, job_id: str, caller: Owner) -> dict:
    service: PreviewService = get_services(request).extension("previews")
    return service.availability(project_id, job_id)


def page(request, project_id, job_id, family, cursor, limit, from_frame=None, to_frame=None):
    from proscenium.api.errors import DomainError

    if (
        from_frame is not None
        and from_frame < 1
        or to_frame is not None
        and to_frame < 1
        or from_frame is not None
        and to_frame is not None
        and from_frame > to_frame
    ):
        raise DomainError("invalid_request")
    services = get_services(request)
    service: PreviewService = services.extension("previews")
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        job = tx.require("jobs", job_id)
        rows = service.records(tx, job, family)
    rows = (
        [
            r
            for r in rows
            if (from_frame is None or r["source_frame"] >= from_frame)
            and (to_frame is None or r["source_frame"] <= to_frame)
        ]
        if family == "frames"
        else rows
    )
    # Cursor keys preserve numeric frame order without adding a field to the wire DTO.
    keyed = [
        {
            "order": f"{(r['source_frame'] if family == 'frames' else r['source_frames']['start']):012d}",
            "record": r,
        }
        for r in rows
    ]
    result = services.cursors.page(
        f"{project_id}:{job_id}:{family}:{from_frame}:{to_frame}",
        keyed,
        "order",
        cursor=cursor,
        limit=limit,
    )
    result["items"] = [r["record"] for r in result["items"]]
    return result


@router.get(BASE + "/frames", operation_id="listFrameReceipts")
def frames(
    request: Request,
    project_id: str,
    job_id: str,
    caller: Owner,
    cursor: str | None = None,
    limit: int = 50,
    from_frame: int | None = None,
    to_frame: int | None = None,
) -> dict:
    return page(request, project_id, job_id, "frames", cursor, limit, from_frame, to_frame)


@router.get(BASE + "/segments", operation_id="listPreviewSegments")
def segments(
    request: Request,
    project_id: str,
    job_id: str,
    caller: Owner,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    return page(request, project_id, job_id, "segments", cursor, limit)


@router.post("/internal/v1/previews", operation_id="requestPreview")
async def preview(request: Request, caller: Tool) -> JSONResponse:
    service: PreviewService = get_services(request).extension("previews")
    result, replayed = service.request(caller, await command(request, "PreviewRequest"))
    return JSONResponse(result, status_code=200 if replayed else 202)


@router.post("/internal/v1/previews/order", operation_id="setPreviewOrder")
async def order(request: Request, caller: Tool) -> JSONResponse:
    service: PreviewService = get_services(request).extension("previews")
    result, _ = service.set_order(caller, await command(request, "PreviewOrderUpdate"))
    return JSONResponse(result, status_code=200)


@router.post("/internal/v1/frame-receipts", operation_id="registerFrameReceipt")
async def frame(request: Request, caller: Worker) -> JSONResponse:
    service: PreviewService = get_services(request).extension("previews")
    result, replayed = service.register(caller, await command(request, "FrameReceipt"))
    return JSONResponse(result, status_code=200 if replayed else 201)


@router.post("/internal/v1/segments", operation_id="registerPreviewSegment")
async def segment(request: Request, caller: Worker) -> JSONResponse:
    service: PreviewService = get_services(request).extension("previews")
    result, replayed = service.register(
        caller, await command(request, "PreviewSegment"), segment=True
    )
    return JSONResponse(result, status_code=200 if replayed else 201)
