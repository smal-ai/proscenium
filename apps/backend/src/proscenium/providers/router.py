from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response

from proscenium.api.dependencies import command, owner, tool, tool_or_worker
from proscenium.auth import Caller
from proscenium.providers.service import ProviderService
from proscenium.services import get_services

router = APIRouter()


def service(request: Request) -> ProviderService:
    return cast(ProviderService, get_services(request).extension("providers"))


@router.post("/internal/v1/generation-jobs", operation_id="submitGenerationJob")
async def submit_generation(
    request: Request, response: Response, caller: Annotated[Caller, Depends(tool)]
) -> dict[str, Any]:
    body = await command(request, "GenerationSubmit")
    result, replayed = service(request).submit(caller, body)
    response.status_code = 200 if replayed else 202
    return result


@router.post("/api/v1/projects/{project_id}/reviews", operation_id="requestOwnerReview")
async def owner_review(
    project_id: str, request: Request, response: Response, caller: Annotated[Caller, Depends(owner)]
) -> dict[str, Any]:
    body = await command(request, "ReviewRequest", project_id=project_id)
    result, replayed = service(request).request_review(caller, body)
    response.status_code = 200 if replayed else 202
    return result


@router.post("/internal/v1/reviews", operation_id="requestToolReview")
async def tool_review(
    request: Request, response: Response, caller: Annotated[Caller, Depends(tool)]
) -> dict[str, Any]:
    body = await command(request, "ReviewRequest")
    result, replayed = service(request).request_review(caller, body)
    response.status_code = 200 if replayed else 202
    return result


@router.post("/internal/v1/reviews/{review_id}/complete", operation_id="completeToolReview")
async def complete_review(
    review_id: str, request: Request, caller: Annotated[Caller, Depends(tool_or_worker)]
) -> dict[str, Any]:
    from proscenium.api.errors import DomainError

    body = await command(request, "ReviewComplete")
    if body["review_id"] != review_id:
        raise DomainError("scope_denied", 403)
    return service(request).complete_review(caller, body)[0]
