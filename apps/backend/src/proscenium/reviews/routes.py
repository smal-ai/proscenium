from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from proscenium.api.dependencies import command, owner
from proscenium.auth import Caller
from proscenium.contracts import validate
from proscenium.services import get_services

router = APIRouter()
Owner = Annotated[Caller, Depends(owner)]
BASE = "/api/v1/projects/{project_id}"


def page(
    request: Request,
    project_id: str,
    namespace: str,
    key: str,
    schema: str,
    cursor: str | None,
    limit: int,
    **filters: str | None,
) -> dict[str, Any]:
    services = get_services(request)
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        records = tx.all(namespace)
        if namespace == "annotations":
            records = [
                r
                for r in records
                if not filters.get("version_id")
                or r["target"]["version_id"] == filters["version_id"]
            ]
        else:
            records = [
                r
                for r in records
                if all(value is None or r.get(name) == value for name, value in filters.items())
            ]
        if namespace == "findings":
            tx.require("reviews", str(filters["review_id"]))
        if namespace == "reviews":
            reviews_ext = services.extension("reviews")
            filter_mode = filters.get("filter") or "all"
            if filter_mode not in {"pending", "all"}:
                from proscenium.api.errors import DomainError

                raise DomainError("invalid_request")
            # Sequence is durable creation order, not a lexicographic UUID approximation.
            rows = tx.connection.execute(
                "SELECT id FROM records WHERE namespace='reviews' ORDER BY rowid DESC"
            ).fetchall()
            order = {row[0]: i for i, row in enumerate(rows)}
            scope = (
                f"{project_id}:reviews:newest"
                if filters.get("filter") is None
                else f"{project_id}:reviews:newest:{filter_mode}"
            )
            after = services.cursors.decode(scope, cursor)
            projected = [reviews_ext.ticket_view(tx, r) for r in tx.all("reviews")]
            if filter_mode == "pending":
                projected = [r for r in projected if r.get("awaiting_human") is True]
            projected.sort(key=lambda r: order[r[key]])
            if after:
                if after not in order:
                    from proscenium.api.errors import DomainError

                    raise DomainError("invalid_cursor")
                projected = [r for r in projected if order[r[key]] > order[after]]
            if not 1 <= limit <= 100:
                from proscenium.api.errors import DomainError

                raise DomainError("invalid_cursor")
            selected = projected[:limit]
            return validate(
                schema,
                dict(
                    schema_version="1.0",
                    items=selected,
                    next_cursor=services.cursors.encode(scope, selected[-1][key])
                    if len(projected) > limit
                    else None,
                ),
            )
        result = services.cursors.page(
            f"{project_id}:{namespace}:{filters}", records, key, cursor=cursor, limit=limit
        )
        return validate(schema, result)


@router.get(BASE + "/annotations", operation_id="listAnnotations")
def list_annotations(
    request: Request,
    project_id: str,
    caller: Owner,
    cursor: str | None = None,
    limit: int = 50,
    version_id: str | None = None,
) -> dict:
    return page(
        request,
        project_id,
        "annotations",
        "annotation_id",
        "AnnotationPage",
        cursor,
        limit,
        version_id=version_id,
    )


@router.post(BASE + "/annotations", operation_id="saveAnnotation")
async def save_annotation(request: Request, project_id: str, caller: Owner) -> JSONResponse:
    body = await command(request, "AnnotationSave", project_id=project_id)
    result = get_services(request).extension("reviews").save_annotation(caller, body)
    return JSONResponse(result)


@router.get(BASE + "/selections", operation_id="listSelections")
def list_selections(
    request: Request, project_id: str, caller: Owner, cursor: str | None = None, limit: int = 50
) -> dict:
    return page(request, project_id, "selections", "selection_id", "SelectionPage", cursor, limit)


@router.post(BASE + "/selections", operation_id="saveSelection")
async def save_selection(request: Request, project_id: str, caller: Owner) -> JSONResponse:
    body = await command(request, "SelectionSave", project_id=project_id)
    return JSONResponse(get_services(request).extension("reviews").save_selection(caller, body))


@router.get(BASE + "/reviews", operation_id="listOwnerReviews")
def list_reviews(
    request: Request,
    project_id: str,
    caller: Owner,
    cursor: str | None = None,
    limit: int = 50,
    filter: str | None = None,
) -> dict:
    return page(
        request, project_id, "reviews", "review_id", "ReviewPage", cursor, limit, filter=filter
    )


@router.get(BASE + "/reviews/{review_id}", operation_id="getOwnerReview")
def get_review(request: Request, project_id: str, review_id: str, caller: Owner) -> dict:
    services = get_services(request)
    reviews = services.extension("reviews")
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        return reviews.detail(tx, review_id)


@router.post(BASE + "/reviews/{review_id}/resolve", operation_id="resolveOwnerReview")
async def resolve_review(
    request: Request, project_id: str, review_id: str, caller: Owner
) -> JSONResponse:
    body = await command(request, "RevisionCommand", project_id=project_id)
    return JSONResponse(get_services(request).extension("reviews").resolve(caller, review_id, body))


@router.get(BASE + "/reviews/{review_id}/findings", operation_id="listOwnerReviewFindings")
def list_findings(
    request: Request,
    project_id: str,
    review_id: str,
    caller: Owner,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    return page(
        request,
        project_id,
        "findings",
        "finding_id",
        "FindingPage",
        cursor,
        limit,
        review_id=review_id,
    )


@router.post(
    BASE + "/reviews/{review_id}/findings/{finding_id}/reject", operation_id="rejectReviewFinding"
)
async def reject_finding(
    request: Request, project_id: str, review_id: str, finding_id: str, caller: Owner
) -> JSONResponse:
    body = await command(request, "RevisionCommand", project_id=project_id)
    return JSONResponse(
        get_services(request).extension("reviews").reject(caller, review_id, finding_id, body)
    )
