"""F00 operations only. Parent composes feature APIRouters via create_app(routers=...)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from proscenium.api.dependencies import command, owner, tool, tool_or_worker
from proscenium.auth import COOKIE_NAME, Caller
from proscenium.contracts import validate
from proscenium.services import get_services

router = APIRouter()
Owner = Annotated[Caller, Depends(owner)]
Tool = Annotated[Caller, Depends(tool)]
Worker = Annotated[Caller, Depends(tool_or_worker)]


@router.post("/api/v1/owner/session", operation_id="pairOwnerSession")
async def pair_owner_session(request: Request) -> JSONResponse:
    services = get_services(request)
    body = await command(request, "OwnerBootstrap")
    result, cookie, replayed = services.auth.pair(body)
    response = JSONResponse(result, status_code=200 if replayed else 201)
    # Session expiry slides server-side. Session cookie avoids a stale fixed Max-Age.
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        httponly=True,
        samesite="strict",
        path="/",
        secure=services.settings.origin.startswith("https:"),
    )
    return response


@router.get("/api/v1/configuration", operation_id="getConfigurationView")
def get_configuration(request: Request, caller: Owner) -> dict:
    return get_services(request).configuration_view()


@router.get("/api/v1/configuration/status", operation_id="getConfigurationStatus")
def configuration_status(request: Request, caller: Owner) -> dict:
    from proscenium.copilot.capabilities import get_configuration_status

    return get_configuration_status(get_services(request))


@router.put("/api/v1/configuration/profiles/{profile_id}", operation_id="upsertAuthoringProfile")
async def upsert_profile(request: Request, profile_id: str, caller: Owner) -> dict:
    body = await command(request, "AuthoringProfileUpsert")
    return get_services(request).upsert_profile(caller, profile_id, body)


@router.post(
    "/api/v1/configuration/profiles/{profile_id}/delete", operation_id="deleteAuthoringProfile"
)
async def delete_profile(request: Request, profile_id: str, caller: Owner) -> dict:
    body = await command(request, "AuthoringProfileDelete")
    return get_services(request).delete_profile(caller, profile_id, body)


@router.get("/api/v1/projects/{project_id}/jobs", operation_id="listJobs")
def list_jobs(
    request: Request, project_id: str, caller: Owner, cursor: str | None = None, limit: int = 50
) -> dict:
    services = get_services(request)
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        result = services.cursors.page(
            f"{project_id}:jobs", tx.all("jobs"), "job_id", cursor=cursor, limit=limit
        )
        return validate("JobPage", result)


@router.get("/api/v1/projects/{project_id}/jobs/{job_id}", operation_id="getJob")
def get_job(request: Request, project_id: str, job_id: str, caller: Owner) -> dict:
    return get_services(request).jobs.get(project_id, job_id)


@router.get("/api/v1/projects/{project_id}/jobs/{job_id}/attempts", operation_id="listJobAttempts")
def list_attempts(
    request: Request,
    project_id: str,
    job_id: str,
    caller: Owner,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    services = get_services(request)
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        tx.require("jobs", job_id)
        records = [a for a in tx.all("attempts") if a["job_id"] == job_id]
        result = services.cursors.page(
            f"{project_id}:attempts:{job_id}", records, "attempt_id", cursor=cursor, limit=limit
        )
        return validate("JobAttemptPage", result)


@router.post(
    "/api/v1/projects/{project_id}/jobs/{job_id}/cancel", operation_id="requestJobCancellation"
)
async def cancel_job(request: Request, project_id: str, job_id: str, caller: Owner) -> JSONResponse:
    body = await command(request, "RevisionCommand", project_id=project_id)
    result, replayed = get_services(request).jobs.cancel(caller, job_id, body)
    return JSONResponse(result, status_code=200 if replayed else 202)


@router.post("/internal/v1/jobs", operation_id="admitToolJob")
async def admit_job(request: Request, caller: Tool) -> JSONResponse:
    body = await command(request, "JobAdmission")
    result, replayed = get_services(request).jobs.admit(caller, body)
    return JSONResponse(result, status_code=200 if replayed else 202)


@router.post("/internal/v1/jobs/{job_id}/retry", operation_id="retryToolJob")
async def retry_job(request: Request, job_id: str, caller: Tool) -> JSONResponse:
    body = await command(request, "JobRetry", job_id=job_id)
    result, replayed = get_services(request).jobs.retry(caller, body)
    return JSONResponse(result, status_code=200 if replayed else 202)


@router.post("/internal/v1/jobs/{job_id}/state", operation_id="updateJobState")
async def update_job(request: Request, job_id: str, caller: Worker) -> JSONResponse:
    body = await command(request, "JobStateUpdate", job_id=job_id)
    result, _ = get_services(request).jobs.update(caller, body)
    return JSONResponse(result)
