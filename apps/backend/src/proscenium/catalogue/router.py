import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from proscenium.api.dependencies import command, owner, tool
from proscenium.api.errors import DomainError
from proscenium.auth import Caller
from proscenium.catalogue.artifacts import Artifacts
from proscenium.catalogue.service import Catalogue
from proscenium.services import get_services

router = APIRouter()
Owner = Annotated[Caller, Depends(owner)]
Tool = Annotated[Caller, Depends(tool)]
BASE = "/api/v1/projects/{project_id}"


@router.get(BASE + "/artifacts", operation_id="listRetainedArtifacts")
def retained_artifacts(
    request: Request,
    project_id: str,
    caller: Owner,
    cursor: str | None = None,
    limit: int = 50,
    kind: str | None = None,
    purpose: str | None = None,
) -> dict:
    artifacts: Artifacts = get_services(request).extension("artifacts")
    return artifacts.list_retained(
        project_id, cursor=cursor, limit=limit, kind=kind, purpose=purpose
    )


@router.get(BASE + "/items", operation_id="listItems")
def items(
    request: Request,
    project_id: str,
    caller: Owner,
    cursor: str | None = None,
    limit: int = 50,
    kind: str | None = None,
) -> dict:
    services = get_services(request)
    if kind is not None and kind not in {
        "asset",
        "scene",
        "storyboard",
        "sequence",
        "unity_result",
    }:
        raise DomainError("invalid_request")
    catalogue: Catalogue = services.extension("catalogue")
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        records = [
            catalogue.item_view(tx, row)
            for row in tx.all("items")
            if kind is None or row["kind"] == kind
        ]
    return services.cursors.page(
        f"{project_id}:items:{kind}", records, "item_id", cursor=cursor, limit=limit
    )


@router.get(BASE + "/items/{item_id}/versions", operation_id="listItemVersions")
def versions(
    request: Request,
    project_id: str,
    item_id: str,
    caller: Owner,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    services = get_services(request)
    catalogue: Catalogue = services.extension("catalogue")
    with services.registry.project_store(project_id).transaction(write=False) as tx:
        tx.require("items", item_id)
        records = [
            catalogue.version_view(tx, row)
            for row in tx.all("versions")
            if row["item_id"] == item_id
        ]
    return services.cursors.page(
        f"{project_id}:versions:{item_id}", records, "version_id", cursor=cursor, limit=limit
    )


@router.get(BASE + "/versions/{version_id}/shot-plan", operation_id="getVersionShotPlan")
def shot_plan(request: Request, project_id: str, version_id: str, caller: Owner) -> dict:
    catalogue: Catalogue = get_services(request).extension("catalogue")
    return catalogue.shot_plan(project_id, version_id)


@router.get(BASE + "/versions/{version_id}/sequence-map", operation_id="getVersionSequenceMap")
def sequence_map(request: Request, project_id: str, version_id: str, caller: Owner) -> dict:
    catalogue: Catalogue = get_services(request).extension("catalogue")
    return catalogue.sequence_map(project_id, version_id)


@router.get(BASE + "/versions/{version_id}/proxy-mapping", operation_id="getVersionProxyMapping")
def proxy_mapping(
    request: Request,
    project_id: str,
    version_id: str,
    caller: Owner,
    mapping_artifact_id: str | None = None,
) -> dict:
    catalogue: Catalogue = get_services(request).extension("catalogue")
    return catalogue.proxy_mapping(project_id, version_id, mapping_artifact_id=mapping_artifact_id)


@router.put(BASE + "/uploads/{upload_id}", operation_id="uploadImage")
async def upload(request: Request, project_id: str, upload_id: str, caller: Owner) -> JSONResponse:
    if request.headers.get("Idempotency-Key") != upload_id:
        raise DomainError("invalid_request")
    artifacts: Artifacts = get_services(request).extension("artifacts")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > artifacts.limits.bytes:
            raise DomainError("upload_too_large", 413)
        data.extend(chunk)
    result, repeated = artifacts.upload(
        caller,
        project_id,
        upload_id,
        bytes(data),
        request.headers.get("content-type", "").split(";")[0].strip().lower(),
    )
    return JSONResponse(result, status_code=200 if repeated else 201)


@router.get(BASE + "/artifacts/{artifact_id}", operation_id="getArtifact")
def artifact(request: Request, project_id: str, artifact_id: str, caller: Owner) -> dict:
    artifacts: Artifacts = get_services(request).extension("artifacts")
    return artifacts.get(project_id, artifact_id)


@router.get(BASE + "/artifacts/{artifact_id}/content", operation_id="getArtifactContent")
@router.head(BASE + "/artifacts/{artifact_id}/content", operation_id="headArtifactContent")
def content(request: Request, project_id: str, artifact_id: str, caller: Owner) -> Response:
    from proscenium.sources.common import sha

    artifacts: Artifacts = get_services(request).extension("artifacts")
    record = artifacts.get(project_id, artifact_id)
    # Serve this verified byte buffer, not a mutable path re-opened later by FileResponse.
    data = artifacts.ready_path(project_id, artifact_id).read_bytes()
    if sha(data) != record["sha256"] or len(data) != record["bytes"]:
        raise DomainError("hash_mismatch", 422)
    size = len(data)
    etag = '"' + record["sha256"] + '"'
    headers = {
        "ETag": etag,
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
        "Content-Length": str(size),
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if "dimensions" in record:
        headers.update(
            {
                "X-Image-Width": str(record["dimensions"]["width"]),
                "X-Image-Height": str(record["dimensions"]["height"]),
            }
        )
    range_header = request.headers.get("range")
    if request.headers.get("if-range", etag) != etag:
        range_header = None
    status = 200
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        invalid = not match or not any(match.groups())
        start, end = 0, size - 1
        if match and not invalid:
            first, last = match.groups()

            def number(value: str) -> int:
                digits = value.lstrip("0") or "0"
                return size + 1 if len(digits) > 20 else int(digits)

            if first:
                start = number(first)
                end = min(number(last), size - 1) if last else size - 1
            else:
                suffix = number(last)
                invalid = suffix == 0
                start = max(0, size - suffix)
            invalid = invalid or start >= size or start > end
        if invalid:
            headers.update({"Content-Range": f"bytes */{size}", "Content-Length": "0"})
            return Response(status_code=416, headers=headers)
        headers.update(
            {"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)}
        )
        data, status = data[start : end + 1], 206
    return Response(
        b"" if request.method == "HEAD" else data,
        status_code=status,
        media_type=record["media_type"],
        headers=headers,
    )


@router.post("/internal/v1/artifacts", operation_id="registerToolArtifact")
async def register(request: Request, caller: Tool) -> JSONResponse:
    body = await command(request, "ArtifactRegister")
    artifacts: Artifacts = get_services(request).extension("artifacts")
    result, repeated = artifacts.register(caller, body)
    return JSONResponse(result, status_code=200 if repeated else 201)


@router.post("/internal/v1/publications", operation_id="publishOutputs")
async def publish(request: Request, caller: Tool) -> JSONResponse:
    body = await command(request, "PublicationSubmit")
    catalogue: Catalogue = get_services(request).extension("catalogue")
    result, repeated = catalogue.publish(caller, body)
    return JSONResponse(result, status_code=200 if repeated else 201)
