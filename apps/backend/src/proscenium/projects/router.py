from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from proscenium.api.dependencies import command, owner
from proscenium.auth import Caller
from proscenium.projects.service import Projects
from proscenium.services import get_services

router = APIRouter()
Owner = Annotated[Caller, Depends(owner)]
BASE = "/api/v1/projects"


@router.get(BASE, operation_id="listProjects")
def projects(request: Request, caller: Owner, cursor: str | None = None, limit: int = 50) -> dict:
    services = get_services(request)
    with services.registry.store.transaction(write=False) as tx:
        records = tx.all("projects")
    return services.cursors.page("projects", records, "project_id", cursor=cursor, limit=limit)


@router.post(BASE, operation_id="openProject")
async def open_project(request: Request, caller: Owner) -> JSONResponse:
    service: Projects = get_services(request).extension("projects")
    result, repeated = service.open(caller, await command(request, "ProjectOpen"))
    return JSONResponse(result, status_code=200 if repeated else 201)


@router.post(BASE + "/{project_id}/activate", operation_id="activateProject")
async def activate(request: Request, project_id: str, caller: Owner) -> JSONResponse:
    service: Projects = get_services(request).extension("projects")
    result, repeated = service.activate(
        caller, await command(request, "RevisionCommand", project_id=project_id)
    )
    return JSONResponse(result, status_code=200)


@router.post(BASE + "/{project_id}/rebind", operation_id="rebindProjectLocation")
async def rebind(request: Request, project_id: str, caller: Owner) -> JSONResponse:
    service: Projects = get_services(request).extension("projects")
    result, repeated = service.rebind(
        caller, await command(request, "ProjectRebind", project_id=project_id)
    )
    return JSONResponse(result, status_code=200)


@router.put(BASE + "/{project_id}/settings", operation_id="updateProjectSettings")
async def settings(request: Request, project_id: str, caller: Owner) -> JSONResponse:
    service: Projects = get_services(request).extension("projects")
    result, repeated = service.update_settings(
        caller, await command(request, "ProjectSettings", project_id=project_id)
    )
    return JSONResponse(result, status_code=200)


@router.get(BASE + "/{project_id}/snapshot", operation_id="getProjectSnapshot")
def snapshot(request: Request, project_id: str, caller: Owner) -> dict:
    service: Projects = get_services(request).extension("projects")
    return service.snapshot(project_id)
