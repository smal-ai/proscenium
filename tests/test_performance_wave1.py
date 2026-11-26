"""Offline regressions for Wave 1 of the performance plan."""

import asyncio
import hashlib
import zlib
from collections import Counter
from pathlib import Path
from threading import Barrier, current_thread
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import Response, StreamingResponse
from fastapi.testclient import TestClient
from proscenium import app as app_module
from proscenium.api.entity_tags import if_none_match
from proscenium.api.errors import DomainError
from proscenium.app import SelectiveGZipMiddleware, create_app
from proscenium.catalogue import artifacts as artifact_module
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.copilot import capabilities
from proscenium.db.store import Transaction
from proscenium.sources.common import git
from proscenium.sources.resolver import closure_digest
from test_local_runtime import command, pair
from test_request_targeting import offline as offline
from test_request_targeting import plan

PAYLOAD = b"x" * 1024


@pytest.fixture
def transport(tmp_path):
    frontend = tmp_path / "dist"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text("<html></html>")
    (frontend / "assets/index-abcd.js").write_bytes(PAYLOAD)
    (frontend / "favicon.svg").write_text("<svg/>")
    settings = Settings(data_directory=tmp_path / "machine", frontend_directory=frontend)
    router = APIRouter()
    tagged_calls = []

    @router.api_route("/sample", methods=["GET", "HEAD"])
    def sample(media_type: str, status: int = 200, size: int = 1024, skip: str = "") -> Response:
        headers = {"Vary": "Origin", "ETag": '"sample"'}
        if skip == "encoding":
            headers["Content-Encoding"] = "identity"
        elif skip == "range":
            headers["Content-Range"] = "bytes 0-1023/2048"
        elif skip == "etag":
            del headers["ETag"]
        body = b"" if status in (204, 304) else PAYLOAD[:size]
        return Response(body, status_code=status, media_type=media_type, headers=headers)

    @router.api_route("/chunks", methods=["GET", "HEAD"])
    def chunks(
        request: Request, size: int = 512, media_type: str = "text/css"
    ) -> StreamingResponse:
        body = [b"x" * (size // 2), b"x" * (size - size // 2)] if request.method == "GET" else []
        return StreamingResponse(iter(body), media_type=media_type)

    @router.api_route("/tagged/{kind}", methods=["GET", "HEAD"])
    def tagged(request: Request, kind: str, size: int = 1024) -> Response:
        etag = '"p"' if kind == "png" else f'"{kind}"'
        conditions = request.headers.getlist("if-none-match")
        tagged_calls.append((request.method, conditions, request.headers.get("if-modified-since")))
        headers = {"ETag": etag}
        if if_none_match(conditions, etag):
            return Response(status_code=304, headers=headers)
        if kind in {"doc", "png"}:
            headers["Content-Length"] = str(size)
            return Response(
                PAYLOAD[:size] if request.method == "GET" else b"",
                media_type="image/png" if kind == "png" else "text/plain",
                headers=headers,
            )
        chunks = (
            [PAYLOAD[: size // 2], PAYLOAD[size // 2 : size]] if request.method == "GET" else []
        )
        return StreamingResponse(iter(chunks), media_type="text/plain", headers=headers)

    @router.websocket("/sample-ws")
    async def sample_ws(socket: WebSocket) -> None:
        await socket.accept()
        await socket.send_bytes(PAYLOAD)

    with TestClient(
        create_app(settings, routers=[router], manage_features=False), base_url=settings.origin
    ) as browser:
        browser.app.state.tagged_calls = tagged_calls
        yield browser


@pytest.mark.parametrize(
    "media_type",
    [
        "application/json",
        "application/javascript",
        "text/css",
        "image/svg+xml",
        "model/gltf+json",
    ],
)
def test_gzip_compresses_eligible_types(transport, media_type):
    response = transport.get(
        "/sample",
        params={"media_type": media_type},
        headers={"Accept-Encoding": "br, GZIP;q=0.5, identity;q=0.4"},
    )
    assert response.content == PAYLOAD
    assert response.headers["content-encoding"] == "gzip"
    assert int(response.headers["content-length"]) < len(PAYLOAD)
    assert response.headers["vary"] == "Origin, Accept-Encoding"
    assert response.headers["etag"] == '"sample-gzip"'


@pytest.mark.parametrize(
    ("method", "encoding", "params"),
    [
        ("GET", "gzip", {"media_type": mime})
        for mime in [
            "image/png",
            "image/jpeg",
            "image/webp",
            "video/mp4",
            "audio/flac",
            "font/woff2",
            "application/octet-stream",
            "model/gltf-binary",
            "text/event-stream",
        ]
    ]
    + [
        ("GET", "gzip", {"status": 204}),
        ("GET", "gzip", {"status": 206}),
        ("GET", "gzip", {"status": 304}),
        ("HEAD", "gzip", {"media_type": "image/png"}),
        ("GET", "gzip", {"size": 511, "skip": "etag"}),
        ("GET", "gzip", {"skip": "encoding"}),
        ("GET", "gzip", {"skip": "range"}),
        ("GET", "gzip;q=0, br", {}),
        ("GET", "x-gzip", {}),
    ],
)
def test_gzip_skips_ineligible_responses(transport, method, encoding, params):
    response = transport.request(
        method,
        "/sample",
        params={"media_type": "application/json", **params},
        headers={"Accept-Encoding": encoding},
    )
    assert response.status_code == params.get("status", 200)
    assert response.headers.get("content-encoding") == (
        "identity" if params.get("skip") == "encoding" else None
    )
    assert response.headers.get("etag") == (None if params.get("skip") == "etag" else '"sample"')
    if encoding == "gzip;q=0, br":
        assert response.headers["vary"] == "Origin, Accept-Encoding"


def test_gzip_buffers_chunks_and_passes_websocket_through(transport):
    response = transport.get("/chunks", headers={"Accept-Encoding": "gzip"})
    assert response.content == PAYLOAD[:512]
    assert response.headers["content-encoding"] == "gzip"
    with transport.websocket_connect(
        f"ws://{transport.base_url.netloc.decode()}/sample-ws",
        headers={"Origin": str(transport.base_url).rstrip("/"), "Accept-Encoding": "gzip"},
    ) as socket:
        assert socket.receive_bytes() == PAYLOAD


@pytest.mark.parametrize("size", [65_535, 65_536])
def test_gzip_offloads_at_64_kib(transport, monkeypatch, size):
    callers = []
    workers = []
    to_thread = app_module.asyncio.to_thread

    async def track_offload(function, data, level, **kwargs):
        callers.append(current_thread())

        def compress():
            workers.append((current_thread(), len(data), level))
            return function(data, level, **kwargs)

        return await to_thread(compress)

    monkeypatch.setattr(app_module, "asyncio", SimpleNamespace(to_thread=track_offload))
    response = transport.get(
        "/chunks", params={"media_type": "application/javascript", "size": size}
    )
    assert response.content == b"x" * size
    assert response.headers["content-encoding"] == "gzip"
    if size == 65_536:
        [caller] = callers
        [(worker, length, level)] = workers
        assert worker is not caller
        assert (length, level) == (65_536, 6)
    else:
        assert not callers and not workers


@pytest.mark.parametrize(
    ("encoding", "status", "selected"),
    [
        ("*;q=1, identity;q=0", 200, "gzip"),
        ("*, gzip;q=0", 200, None),
        ("gzip;q=0, identity;q=0", 406, None),
        ("br;q=1, *;q=0", 406, None),
        ("gzip;q=0.5", 200, None),
        ("", 200, None),
    ],
)
def test_gzip_negotiation(transport, encoding, status, selected):
    response = transport.get("/assets/index-abcd.js", headers={"Accept-Encoding": encoding})
    assert response.status_code == status
    assert response.headers.get("content-encoding") == selected
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.content == (PAYLOAD if status == 200 else b"")


def test_gzip_combines_encoding_headers_and_rejects_unacceptable_identity(transport):
    headers = [("Accept-Encoding", "*;q=1"), ("Accept-Encoding", "identity;q=0")]
    assert (
        transport.get("/assets/index-abcd.js", headers=headers).headers["content-encoding"]
        == "gzip"
    )
    response = transport.get("/sample", params={"media_type": "image/png"}, headers=headers)
    assert response.status_code == 406 and response.content == b""
    assert response.headers["vary"] == "Origin, Accept-Encoding"


def pairing_command(transport):
    operation_id = str(uuid4())
    return (
        {
            "schema_version": "1.0",
            "operation_id": operation_id,
            "client_nonce": str(uuid4()),
            "pairing_code": transport.app.state.services.auth.issue_pairing(),
        },
        {"Origin": str(transport.base_url).rstrip("/"), "Idempotency-Key": operation_id},
    )


def test_only_gzip_accepted_keeps_short_pairing_response_and_replay(transport):
    payload, headers = pairing_command(transport)
    headers["Accept-Encoding"] = "gzip, identity;q=0"
    first = transport.post("/api/v1/owner/session", headers=headers, json=payload)
    assert first.status_code == 201
    replay = transport.post("/api/v1/owner/session", headers=headers, json=payload)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert len(first.content) < SelectiveGZipMiddleware.minimum_size
    for response in (first, replay):
        assert response.headers["content-encoding"] == "gzip"
        assert response.headers["vary"] == "Accept-Encoding"
        assert response.cookies["proscenium_owner"] == first.cookies["proscenium_owner"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]


def test_unacceptable_encodings_do_not_consume_pairing_code(transport):
    payload, headers = pairing_command(transport)
    rejected = transport.post(
        "/api/v1/owner/session",
        headers={**headers, "Accept-Encoding": "gzip;q=0, identity;q=0"},
        json=payload,
    )
    assert rejected.status_code == 406
    assert rejected.headers["vary"] == "Accept-Encoding"
    assert "set-cookie" not in rejected.headers
    accepted = transport.post("/api/v1/owner/session", headers=headers, json=payload)
    assert accepted.status_code == 201
    assert accepted.cookies["proscenium_owner"]


@pytest.mark.parametrize("encoding", [None, b"gzip"])
@pytest.mark.parametrize("media_type", [b"text/event-stream", b"text/plain"])
def test_asgi_sse_streams_immediately_and_other_streams_vary(encoding, media_type):
    sent = []
    first = {"type": "http.response.body", "body": b"hello", "more_body": True}
    last = {"type": "http.response.body", "body": b" world"}

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", media_type)],
            }
        )
        await send(first)
        if media_type == b"text/event-stream":
            assert sent[1] is first
        await send(last)

    async def send(message):
        sent.append(message)

    async def receive():
        raise AssertionError("No request body expected")

    asyncio.run(
        SelectiveGZipMiddleware(app)(
            {
                "type": "http",
                "method": "GET",
                "headers": [(b"accept-encoding", encoding)] if encoding else [],
            },
            receive,
            send,
        )
    )
    headers = dict(sent[0]["headers"])
    body = b"".join(message["body"] for message in sent[1:])
    assert headers[b"vary"] == b"Accept-Encoding"
    if encoding == b"gzip" and media_type == b"text/plain":
        assert headers[b"content-encoding"] == b"gzip"
        body = zlib.decompress(body, wbits=31)
    else:
        assert b"content-encoding" not in headers
    assert body == b"hello world"


def test_short_untagged_stream_selects_gzip_for_get_and_head(transport):
    responses = [
        transport.request(
            method,
            "/chunks",
            params={"size": 1, "media_type": "text/plain"},
            headers={"Accept-Encoding": "gzip"},
        )
        for method in ("GET", "HEAD")
    ]
    get, head = responses
    for response in responses:
        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert response.headers["vary"] == "Accept-Encoding"
        assert "etag" not in response.headers
    assert get.content == b"x"
    assert head.content == b"" and "content-length" not in head.headers


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_if_none_match_wildcard_revalidates_untagged_stream_without_compression(
    transport, monkeypatch, method
):
    def unexpected_compression(*args, **kwargs):
        pytest.fail("A 304 response must not compress the body")

    monkeypatch.setattr(app_module, "gzip", SimpleNamespace(compress=unexpected_compression))
    response = transport.request(
        method,
        "/chunks",
        params={"size": 1, "media_type": "text/plain"},
        headers={"Accept-Encoding": "gzip", "If-None-Match": "*"},
    )
    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["vary"] == "Accept-Encoding"
    assert "etag" not in response.headers
    assert "content-length" not in response.headers


@pytest.mark.parametrize("encoding", ["gzip", "identity"])
def test_static_get_head_and_conditional_select_the_same_representation(transport, encoding):
    path = "/assets/index-abcd.js"
    headers = {"Accept-Encoding": encoding}
    get = transport.get(path, headers=headers)
    head = transport.head(path, headers=headers)
    assert get.status_code == head.status_code == 200
    assert head.content == b"" and get.content == PAYLOAD
    for name in ("content-encoding", "etag", "vary"):
        assert get.headers.get(name) == head.headers.get(name)
    if encoding == "gzip":
        assert "content-length" not in head.headers
    else:
        assert get.headers["content-length"] == head.headers["content-length"] == str(len(PAYLOAD))
    etag = get.headers["etag"]
    other = ('"' + etag[1:-6] + '"') if encoding == "gzip" else etag[:-1] + '-gzip"'
    assert etag.endswith('-gzip"') == (encoding == "gzip")
    assert not etag.startswith("W/")
    for method in ("GET", "HEAD"):
        matched = transport.request(
            method, path, headers={**headers, "If-None-Match": f'"a,b", W/{etag}'}
        )
        assert matched.status_code == 304 and matched.content == b""
        assert matched.headers["etag"] == etag
        assert matched.headers["vary"] == "Accept-Encoding"
    mismatched = transport.get(path, headers={**headers, "If-None-Match": other})
    assert mismatched.status_code == 200 and mismatched.headers["etag"] == etag


@pytest.mark.parametrize(
    ("kind", "size"), [("doc", 1024), ("doc", 64), ("stream", 64), ("stream", 1024)]
)
def test_tagged_responses_negotiate_and_revalidate_in_one_invocation(
    transport, monkeypatch, kind, size
):
    path = f"/tagged/{kind}"
    calls = transport.app.state.tagged_calls
    compress = app_module.gzip.compress
    encoded = []

    def spy(data, *args, **kwargs):
        encoded.append(len(data))
        return compress(data, *args, **kwargs)

    monkeypatch.setattr(app_module, "gzip", SimpleNamespace(compress=spy))
    for method in ("GET", "HEAD"):
        for condition, expected_status in (
            (None, 200),
            (f'"{kind}"', 200),
            (f', "a,*,b", , W/"{kind}-gzip",', 304),
            ("*", 304),
        ):
            headers = {"Accept-Encoding": "gzip"}
            if condition is not None:
                headers["If-None-Match"] = condition
                headers["If-Modified-Since"] = "Wed, 25 Nov 2026 14:00:00 GMT"
            calls.clear()
            encoded.clear()
            response = transport.request(method, path, params={"size": size}, headers=headers)
            assert calls == [(method, [], None)]
            assert response.status_code == expected_status
            assert response.headers["etag"] == f'"{kind}-gzip"'
            assert response.headers["vary"] == "Accept-Encoding"
            assert response.headers["content-encoding"] == "gzip"
            if method == "HEAD" or expected_status == 304:
                assert response.content == b""
                assert "content-length" not in response.headers
                assert not encoded
            else:
                assert response.content == PAYLOAD[:size]
                assert encoded == [size]


def test_png_conditionals_match_only_the_identity_representation(transport):
    for condition, expected_status in ((None, 200), ('"p-gzip"', 200), ("*", 304), ('"p"', 304)):
        headers = {"Accept-Encoding": "gzip"}
        if condition is not None:
            headers["If-None-Match"] = condition
        response = transport.get("/tagged/png", headers=headers)
        assert response.status_code == expected_status
        assert response.headers["etag"] == '"p"'
        assert response.headers["vary"] == "Accept-Encoding"
        assert "content-encoding" not in response.headers
        assert response.content == (PAYLOAD if expected_status == 200 else b"")


def test_static_cache_policy(transport):
    path = "/assets/index-abcd.js"
    response = transport.get(path, headers={"Accept-Encoding": "gzip"})
    cache = "public, max-age=31536000, immutable"
    assert response.content == PAYLOAD
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["cache-control"] == cache
    revalidated = transport.get(path, headers={"If-None-Match": response.headers["etag"]})
    assert revalidated.status_code == 304
    assert revalidated.headers["cache-control"] == cache
    assert revalidated.headers["vary"] == response.headers["vary"] == "Accept-Encoding"
    assert transport.get("/").headers["cache-control"] == "no-cache"
    for path in ("/favicon.svg", "/favicon.ico"):
        assert transport.get(path).headers["cache-control"] == "public, max-age=86400"


@pytest.fixture
def project(transport, tmp_path):
    pair(transport)
    root = tmp_path / "project"
    record = command(transport, "/api/v1/projects", {"path": str(root), "create": True})
    project_id = record["project_id"]
    base = f"/api/v1/projects/{project_id}"
    command(
        transport,
        base + "/activate",
        {"project_id": project_id, "expected_revision": record["state_revision"]},
    )
    services = transport.app.state.services
    return root, record, base, services


def install_authored(root, record, store, kind, entry, data):
    (root / entry).write_bytes(data)
    git(root, "add", "--", entry)
    git(
        root,
        "-c",
        "user.name=Offline",
        "-c",
        "user.email=offline@example.invalid",
        "commit",
        "-m",
        "test authored read",
    )
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    closure = dict(
        entry_path=entry,
        files=[
            dict(
                destination=entry,
                origin=dict(
                    kind="git",
                    repository_id=record["repository_id"],
                    commit=commit,
                    path=entry,
                    sha256=hashlib.sha256(data).hexdigest(),
                ),
            )
        ],
        dependencies=[],
        required_runtime=dict(application="JSON", version="1", features=[]),
    )
    closure["digest"] = closure_digest(closure)
    version = dict(
        project_id=record["project_id"],
        item_id=f"{kind}-item",
        version_id=f"{kind}-v1",
        source_commit=commit,
        source=closure,
        kind=kind,
        artifact_ids=[],
        preview_job_ids=[],
    )
    with store.transaction() as tx:
        tx.put("versions", version["version_id"], version)
    oid = git(root, "rev-parse", f"{commit}:{entry}").decode().strip()
    return root / ".git" / "objects" / oid[:2] / oid[2:]


@pytest.mark.parametrize(
    ("kind", "route"), [("storyboard", "shot-plan"), ("sequence", "sequence-map")]
)
def test_authored_projection_rechecks_git_without_snapshots(
    transport, project, monkeypatch, kind, route
):
    root, record, base, services = project
    project_id = record["project_id"]
    store = services.registry.project_store(project_id)
    version_ref = dict(item_id=f"{kind}-item", version_id=f"{kind}-v1")
    with store.transaction() as tx:
        if kind == "storyboard":
            document = plan(project_id)
            shot = document["shots"][0]
            frame = shot["frames"][0]
            frame.update(artifact_id="frame-image", state="available")
            shot["frames"].append({**frame, "frame_id": "frame-two", "role": "end"})
            tx.put("artifacts", "frame-image", dict(kind="image", state="ready"))
            slot = dict(
                kind="storyboard_frame",
                storyboard_version=version_ref,
                shot_id=shot["shot_id"],
                frame_id=frame["frame_id"],
            )
            choice = dict(kind="artifact", artifact_id="frame-image")
        else:
            scene = dict(item_id="scene-item", version_id="scene-v1")
            fps = dict(numerator=24, denominator=1)
            entries = [
                dict(
                    entry_id=f"entry-{i}",
                    shot_id=None,
                    version_id=scene["version_id"],
                    job_id="render-one",
                    source_frames=dict(start=i, end=i),
                    sequence_frames=dict(start=i, end=i),
                    transform="identity",
                    mapping_artifact_id=None,
                    choice="owner",
                )
                for i in (1, 2)
            ]
            document = dict(
                schema_version="1.0",
                project_id=project_id,
                frame_count=2,
                fps=fps,
                sequence_version_id=version_ref["version_id"],
                silent=True,
                entries=entries,
            )
            tx.put("versions", scene["version_id"], scene)
            tx.put(
                "jobs",
                "render-one",
                dict(
                    kind="render",
                    target=dict(version=scene, shot_id=None),
                    render_spec=dict(frames=dict(start=1, end=2), fps=fps),
                ),
            )
            slot = dict(kind="assembly_shot", sequence_version=version_ref, entry_id="entry-1")
            choice = dict(kind="version", version=scene)
        tx.put("selections", "selection-one", dict(slot=slot, state="selected", choice=choice))
    data = canonical(document)
    blob = install_authored(root, record, store, kind, f"{route}.json", data)
    url = f"{base}/versions/{version_ref['version_id']}/{route}"
    scans = []
    all_records = Transaction.all

    def count_scans(tx, namespace):
        if namespace == "selections":
            scans.append(namespace)
        return all_records(tx, namespace)

    monkeypatch.setattr(Transaction, "all", count_scans)
    response = transport.get(url)
    assert response.status_code == 200, response.text
    if kind == "storyboard":
        assert [frame["owner_selected"] for frame in response.json()["shots"][0]["frames"]] == [
            True,
            False,
        ]
    else:
        assert [entry["choice"] for entry in response.json()["entries"]] == ["owner", "agent"]
    assert scans == ["selections"]

    with store.transaction() as tx:
        tx.put("selections", "duplicate", dict(slot=slot, state="selected", choice=choice))
    response = transport.get(url)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "source_conflict"
    assert (
        response.json()["error"]["message"] == "More than one current selection record for a slot"
    )

    # Replace the loose object's bytes at the original object ID, not the worktree file.
    blob.chmod(0o600)
    blob.write_bytes(zlib.compress(f"blob {len(data)}\0".encode() + b" " + data[1:]))
    response = transport.get(url)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "hash_mismatch"
    assert not (root / ".proscenium/snapshots").exists()


def test_invalid_authored_json_preserves_source_error(transport, project):
    root, record, base, services = project
    store = services.registry.project_store(record["project_id"])
    blob = install_authored(root, record, store, "storyboard", "invalid.json", b"{")
    url = f"{base}/versions/storyboard-v1/shot-plan"
    response = transport.get(url)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "source_unavailable"
    assert response.json()["error"]["message"] == "source unavailable"
    blob.unlink()
    response = transport.get(url)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "source_unavailable"
    assert response.json()["error"]["message"] == "Local Git operation failed"


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("duplicate", 200, None),
        ("missing-dependency", 422, "dependency_missing"),
        ("wrong-item", 403, "scope_denied"),
        ("missing-entry", 422, "dependency_missing"),
        ("missing-retained-record", 422, "retained_source_missing"),
        ("missing-retained-file", 422, "retained_source_missing"),
        ("corrupt-retained", 422, "retained_source_missing"),
        ("different-bytes", 409, "dependency_collision"),
    ],
)
def test_shot_plan_preserves_resolver_closure_semantics(transport, project, case, status, code):
    root, record, base, services = project
    project_id = record["project_id"]
    store = services.registry.project_store(project_id)
    version_id = "storyboard-v1"
    entry = "plan.json"
    retained = b"test retained closure file"
    digest = hashlib.sha256(retained).hexdigest()
    install_authored(root, record, store, "storyboard", entry, canonical(plan(project_id)))
    details = {}
    with store.transaction() as tx:
        version = tx.require("versions", version_id)
        closure = version["source"]
        if case == "duplicate":
            closure["files"].append(closure["files"][0].copy())
        elif case in {"missing-dependency", "wrong-item"}:
            closure["dependencies"].append(dict(item_id="expected-item", version_id="dependency"))
            if case == "wrong-item":
                tx.put("versions", "dependency", {"item_id": "wrong-item"})
            else:
                details = {"version_id": "dependency"}
        elif case == "missing-entry":
            closure["entry_path"] = "missing.json"
            details = {"path": "missing.json"}
        else:
            retained_id = "retained-one"
            closure["files"].append(
                dict(
                    destination=entry if case == "different-bytes" else "retained.json",
                    origin=dict(kind="retained", artifact_id=retained_id, sha256=digest),
                )
            )
            if case != "missing-retained-record":
                tx.put("retained_sources", retained_id, {"sha256": digest})
            if case in {"corrupt-retained", "different-bytes"}:
                path = root / ".proscenium" / "retained" / digest / "content"
                path.parent.mkdir(parents=True)
                path.write_bytes(b"corrupt bytes" if case == "corrupt-retained" else retained)
            details = (
                {"destination": entry}
                if case == "different-bytes"
                else {"artifact_id": retained_id, "sha256": digest}
            )
        closure["digest"] = closure_digest(closure)
        tx.put(
            "versions", version_id, version, expected_revision=tx.revision("versions", version_id)
        )
    response = transport.get(f"{base}/versions/{version_id}/shot-plan")
    assert response.status_code == status, response.text
    if code:
        error = response.json()["error"]
        assert (error["code"], error["details"]) == (code, details)
    else:
        assert response.json()["plan_id"] == "plan-one"
    assert not (root / ".proscenium/snapshots").exists()


@pytest.mark.parametrize("media_type", ["image/png", "application/json"])
def test_artefact_content_verifies_once_and_preserves_conditional_and_range_reads(
    transport, project, monkeypatch, media_type
):
    _, record, base, services = project
    project_id = record["project_id"]
    artifact_id = "image-one"
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    path = services.extension("artifacts").path(project_id, digest)
    path.parent.mkdir(parents=True)
    path.write_bytes(PAYLOAD)
    with services.registry.project_store(project_id).transaction() as tx:
        tx.put(
            "artifacts",
            artifact_id,
            dict(
                state="ready",
                bytes=len(PAYLOAD),
                sha256=digest,
                media_type=media_type,
                dimensions=dict(width=16, height=9),
            ),
        )
    url = f"{base}/artifacts/{artifact_id}/content"
    counts = Counter()
    read_bytes = Path.read_bytes
    sha = artifact_module.sha

    def count_read(file):
        if file == path:
            counts["read"] += 1
        return read_bytes(file)

    def count_hash(data):
        counts["sha"] += 1
        return sha(data)

    monkeypatch.setattr(Path, "read_bytes", count_read)
    monkeypatch.setattr(artifact_module, "sha", count_hash)
    response = transport.get(url)
    assert response.status_code == 200 and response.content == PAYLOAD
    assert response.headers["content-length"] == str(len(PAYLOAD))
    assert "content-encoding" not in response.headers
    assert counts == {"read": 1, "sha": 1}
    etag = response.headers["etag"]
    assert etag == f'"{digest}"'
    assert response.headers["x-image-width"] == "16"
    assert response.headers["x-image-height"] == "9"
    for method, tag in [("GET", etag), ("GET", f'"unrelated", W/{etag}'), ("HEAD", "*")]:
        counts.clear()
        conditional = transport.request(
            method, url, headers={"If-None-Match": tag, "Range": "bytes=1-3"}
        )
        assert conditional.status_code == 304 and conditional.content == b""
        for name in ["etag", "cache-control", "x-content-type-options", "content-security-policy"]:
            assert conditional.headers[name] == response.headers[name]
        assert "content-length" not in conditional.headers
        assert "content-range" not in conditional.headers
        assert conditional.headers["accept-ranges"] == "bytes"
        assert counts == {"read": 1, "sha": 1}
    for tag in ('"a,*,b"', '"other", *', "*,", etag[:-1] + '-gzip"'):
        rejected_tag = transport.get(url, headers={"If-None-Match": tag})
        assert rejected_tag.status_code == 200 and rejected_tag.content == PAYLOAD
    assert transport.get(url, headers={"If-None-Match": f", {etag},"}).status_code == 304
    assert (
        transport.get(
            url, headers=[("If-None-Match", '"other"'), ("If-None-Match", f"W/{etag}")]
        ).status_code
        == 304
    )
    head = transport.head(url)
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == str(len(PAYLOAD))
    partial = transport.get(url, headers={"Range": "bytes=1-3", "If-Range": etag})
    assert partial.status_code == 206 and partial.content == PAYLOAD[1:4]
    assert partial.headers["content-range"] == f"bytes 1-3/{len(PAYLOAD)}"
    assert partial.headers["content-length"] == "3"
    for representation in (response, head, partial):
        assert representation.headers["etag"] == etag
        assert representation.headers["accept-ranges"] == "bytes"
        assert "content-encoding" not in representation.headers
    assert (
        transport.get(url, headers={"Range": "bytes=1-3", "If-Range": f"W/{etag}"}).status_code
        == 200
    )
    invalid = transport.get(url, headers={"Range": "bytes=9999-"})
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == f"bytes */{len(PAYLOAD)}"
    path.write_bytes(b"y" * len(PAYLOAD))
    tampered = transport.get(url, headers={"If-None-Match": etag})
    assert tampered.status_code == 422 and tampered.json()["error"]["code"] == "hash_mismatch"


def provider_services(*providers):
    return SimpleNamespace(
        extension=lambda _: SimpleNamespace(options={}),
        settings=SimpleNamespace(services=providers, rendering={}),
    )


def provider(identity, *caps, model=None, available=None):
    return dict(
        provider_id=identity,
        endpoint=f"http://{identity}.invalid",
        capabilities=list(caps),
        model_id=model,
        available=available,
    )


def test_provider_capabilities_share_a_single_comfy_probe(monkeypatch):
    calls = []
    caller = current_thread()

    def get(client, service, path):
        assert current_thread() is caller
        calls.append((service["provider_id"], path))
        return {}

    monkeypatch.setattr(capabilities, "bounded_get", get)
    monkeypatch.setattr(capabilities.stable_audio, "resolve_audio_verifier", lambda _: None)
    rows = capabilities.probe_provider_capabilities(
        provider_services(provider("comfy", "image", "image_to_3d", "audio"))
    )
    assert calls == [("comfy", "/object_info")]
    assert [row["capability"] for row in rows] == ["image", "image_to_3d", "audio"]
    assert [row["reason_code"] for row in rows] == ["nodes_or_models_missing"] * 3


def test_provider_probes_run_concurrently_and_keep_order_and_errors(monkeypatch):
    barrier = Barrier(2, timeout=5)
    calls = []

    def get(client, service, path):
        calls.append((service["provider_id"], path))
        barrier.wait()
        if path == "/object_info":
            raise httpx.ConnectError("offline")
        return {"data": [{"id": "vision"}]}

    def verifier(rendering):
        raise DomainError("audio_verifier_missing", 422, "No verifier")

    monkeypatch.setattr(capabilities, "bounded_get", get)
    monkeypatch.setattr(capabilities.stable_audio, "resolve_audio_verifier", verifier)
    rows = capabilities.probe_provider_capabilities(
        provider_services(
            provider("comfy", "image", "image_to_3d"),
            provider("disabled", "image", available=False),
            provider("no-model", "vlm"),
            provider("audio-only", "audio"),
            provider("vlm", "vlm", model="vision"),
        )
    )
    assert Counter(calls) == {("comfy", "/object_info"): 1, ("vlm", "/v1/models"): 1}
    assert [(row["provider_id"], row["reason_code"]) for row in rows] == [
        ("comfy", "provider_unreachable"),
        ("comfy", "provider_unreachable"),
        ("disabled", "configured_unavailable"),
        ("no-model", "model_not_configured"),
        ("audio-only", "audio_verifier_missing"),
        ("vlm", None),
    ]
    assert rows[-1]["status"] == "configured"
    assert rows[-1]["selected_models"] == ["vision"]
