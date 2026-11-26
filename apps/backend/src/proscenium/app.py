import asyncio
import gzip
import os
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from proscenium.api.composition import configure_services, feature_routers
from proscenium.api.entity_tags import gzip_etag, if_none_match
from proscenium.api.errors import DomainError
from proscenium.api.lifecycle import FeatureLifecycle
from proscenium.api.router import router
from proscenium.configuration import Settings
from proscenium.services import Services


class _ResponseFinished(Exception):
    """Stop an inner response after emitting our own status and body."""


class SelectiveGZipMiddleware:
    minimum_size = 512
    thread_threshold = 65_536
    _quality = re.compile(r"(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)", re.ASCII)
    _artefact_content = re.compile(r"/api/v1/projects/[^/]+/artifacts/[^/]+/content")
    compressible_types = frozenset(
        {
            "application/json",
            "application/javascript",
            "application/xml",
            "image/svg+xml",
            "model/gltf+json",
        }
    )

    def __init__(self, app: ASGIApp):
        self.app = app

    @classmethod
    def encoding_quality(cls, headers: Headers) -> tuple[float, float]:
        qualities: dict[str, float] = {}
        for value in headers.getlist("accept-encoding"):
            for encoding in value.split(","):
                name, separator, parameter = encoding.partition(";")
                name = name.strip().lower()
                if name not in {"gzip", "identity", "*"}:
                    continue
                quality = 1.0
                if separator:
                    key, _, weight = parameter.partition("=")
                    weight = weight.strip()
                    quality = (
                        float(weight)
                        if key.strip().lower() == "q" and cls._quality.fullmatch(weight)
                        else 0.0
                    )
                qualities[name] = max(quality, qualities.get(name, 0.0))
        return qualities.get("gzip", qualities.get("*", 0.0)), qualities.get(
            "identity", qualities.get("*", 1.0)
        )

    @classmethod
    def compressible_type(cls, headers: Headers) -> bool:
        media_type = headers.get("content-type", "").partition(";")[0].strip().lower()
        return media_type in cls.compressible_types or (
            media_type.startswith("text/") and media_type != "text/event-stream"
        )

    @classmethod
    def compressible(cls, status: int, headers: Headers) -> bool:
        return (
            status >= 200
            and status not in {204, 206, 304}
            and "content-encoding" not in headers
            and "content-range" not in headers
            and headers.get("accept-ranges", "none").strip().lower() == "none"
            and cls.compressible_type(headers)
        )

    @classmethod
    def selects_gzip(cls, status: int, headers: Headers, gzip_q: float, identity_q: float) -> bool:
        if not (gzip_q > 0 and gzip_q >= identity_q and cls.compressible(status, headers)):
            return False
        length = headers.get("content-length", "")
        return (
            "etag" in headers
            or identity_q == 0
            or not length.isdecimal()
            or int(length) >= cls.minimum_size
        )

    @staticmethod
    def mark_gzip(headers: MutableHeaders) -> None:
        headers["content-encoding"] = "gzip"
        if etag := headers.get("etag"):
            headers["etag"] = gzip_etag(etag)

    @classmethod
    def native_conditionals(cls, scope: Scope) -> bool:
        # Static routes either negotiate their own validator or only serve identity.
        # Artefact routes evaluate the original strong SHA-256 tag, before Range.
        path = scope.get("path", "")
        root = scope.get("root_path", "").rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        return path.startswith(("/assets/", "/fonts/", "/licences/")) or bool(
            cls._artefact_content.fullmatch(path)
        )

    @staticmethod
    def vary_on_encoding(headers: MutableHeaders) -> None:
        values = headers.getlist("vary")
        if not any(
            token.strip().lower() in {"*", "accept-encoding"}
            for value in values
            for token in value.split(",")
        ):
            values.append("Accept-Encoding")
        if len(values) > 1 or not headers.get("vary"):
            headers["vary"] = ", ".join(values)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_headers = Headers(scope=scope)
        gzip_q, identity_q = self.encoding_quality(request_headers)
        if gzip_q == 0 and identity_q == 0:
            await Response(
                status_code=406, headers={"vary": "Accept-Encoding", "cache-control": "no-store"}
            )(scope, receive, send)
            return
        prefer_gzip = gzip_q > 0 and gzip_q >= identity_q
        method = scope["method"]
        conditional = request_headers.getlist("if-none-match") if method in {"GET", "HEAD"} else []
        evaluate_conditional = bool(conditional) and not self.native_conditionals(scope)
        if prefer_gzip and evaluate_conditional:
            # Defer evaluation until the response identifies the selected representation.
            # If-None-Match supersedes If-Modified-Since, so defer both together.
            scope = {
                **scope,
                "headers": [
                    (key, value)
                    for key, value in scope["headers"]
                    if key not in {b"if-none-match", b"if-modified-since"}
                ],
            }
        if (
            prefer_gzip
            and method != "HEAD"
            and "http.response.pathsend" in scope.get("extensions", {})
        ):
            # FileResponse must send body chunks rather than delegating the file to the server.
            scope = {
                **scope,
                "extensions": {
                    k: v for k, v in scope["extensions"].items() if k != "http.response.pathsend"
                },
            }
        start: Message | None = None
        body = bytearray()

        async def finish(response: Response) -> None:
            await response(scope, receive, send)
            raise _ResponseFinished

        async def reject(headers: Headers) -> None:
            response = Response(status_code=406, headers={"cache-control": "no-store"})
            for vary in headers.getlist("vary"):
                response.headers.append("vary", vary)
            self.vary_on_encoding(response.headers)
            await finish(response)

        async def check_conditional(status: int, headers: Headers, use_gzip: bool) -> None:
            if (
                status == 200
                and evaluate_conditional
                and if_none_match(conditional, headers.get("etag"))
            ):
                response = NotModifiedResponse(headers)
                if use_gzip:
                    response.headers["content-encoding"] = "gzip"
                await finish(response)

        async def send_response(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                status = message["status"]
                if status >= 200:
                    self.vary_on_encoding(headers)
                use_gzip = self.selects_gzip(status, headers, gzip_q, identity_q)
                if use_gzip:
                    self.mark_gzip(headers)
                    # The compressed length is only known after encoding the body.
                    del headers["content-length"]
                if (
                    status >= 200
                    and status != 204
                    and identity_q == 0
                    and not (headers.get("content-encoding") == "gzip" and gzip_q > 0)
                ):
                    await reject(headers)
                await check_conditional(status, headers, use_gzip)
                if use_gzip and method != "HEAD":
                    start = message
                    return
            elif message["type"] == "http.response.body" and start is not None:
                chunk: bytes = message.get("body", b"")
                if message.get("more_body", False):
                    body.extend(chunk)
                    return
                payload: bytes | bytearray = chunk
                if body:
                    body.extend(chunk)
                    payload = body
                headers = MutableHeaders(scope=start)
                data = (
                    await asyncio.to_thread(gzip.compress, payload, 6, mtime=0)
                    if len(payload) >= self.thread_threshold
                    else gzip.compress(payload, compresslevel=6, mtime=0)
                )
                headers["content-length"] = str(len(data))
                await send(start)
                start = None
                message = {**message, "body": data}
            if method == "HEAD" and message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        try:
            await self.app(scope, receive, send_response)
        except* _ResponseFinished:
            pass


class ImmutableStaticFiles(StaticFiles):
    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = FileResponse(full_path, status_code=status_code, stat_result=stat_result)
        request_headers = Headers(scope=scope)
        headers = response.headers
        if "range" not in request_headers and SelectiveGZipMiddleware.compressible_type(headers):
            # A full fingerprinted text asset can negotiate gzip. A Range request still
            # uses FileResponse's byte-range support and its identity validator.
            del headers["accept-ranges"]
        gzip_q, identity_q = SelectiveGZipMiddleware.encoding_quality(request_headers)
        gzip_selected = SelectiveGZipMiddleware.selects_gzip(
            status_code, headers, gzip_q, identity_q
        )
        etag = gzip_etag(headers["etag"]) if gzip_selected else headers["etag"]
        conditional = request_headers.getlist("if-none-match")
        if (gzip_selected or identity_q > 0) and (
            if_none_match(conditional, etag)
            if conditional
            else self.is_not_modified(headers, request_headers)
        ):
            not_modified = NotModifiedResponse(headers)
            not_modified.headers["etag"] = etag
            if gzip_selected:
                not_modified.headers["content-encoding"] = "gzip"
            SelectiveGZipMiddleware.vary_on_encoding(not_modified.headers)
            return not_modified
        return response

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_cached(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] in {200, 304}:
                headers = MutableHeaders(scope=message)
                headers["cache-control"] = "public, max-age=31536000, immutable"
            await send(message)

        await super().__call__(scope, receive, send_cached)


class OriginBoundary:
    def __init__(self, app: ASGIApp, settings: Settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        origin = headers.get("origin")
        browser_write = scope.get("method") not in {None, "GET", "HEAD", "OPTIONS"} and not scope[
            "path"
        ].startswith("/internal/")
        denied = (
            headers.get("host") != self.settings.host
            or (origin is not None and origin != self.settings.origin)
            or ((browser_write or scope["type"] == "websocket") and origin != self.settings.origin)
        )
        if denied:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse(DomainError("origin_denied", 403).envelope(), 403)(
                    scope, receive, send
                )
            return
        await self.app(scope, receive, send)


def create_app(
    settings: Settings | None = None,
    *,
    routers: Sequence[APIRouter] = (),
    services: Services | None = None,
    manage_features: bool | None = None,
) -> FastAPI:
    settings = settings or (services.settings if services is not None else Settings())
    manage = services is None if manage_features is None else manage_features

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.services = services or Services.create(settings)
        lifecycle = FeatureLifecycle(app.state.services) if manage else None
        try:
            configure_services(app.state.services)
            if lifecycle is not None:
                await lifecycle.start()
            yield
        finally:
            try:
                if lifecycle is not None:
                    await lifecycle.close()
            finally:
                if services is None:
                    app.state.services.close()

    app = FastAPI(
        title="Proscenium", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.add_middleware(SelectiveGZipMiddleware)
    app.add_middleware(OriginBoundary, settings=settings)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(exc.envelope(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(DomainError("invalid_request").envelope(), status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            DomainError(
                "not_found" if exc.status_code == 404 else "invalid_request", exc.status_code
            ).envelope(),
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(DomainError("internal_error", 500).envelope(), status_code=500)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "component": "foundation"}

    app.include_router(router)
    seen = set()
    for extension in (*feature_routers(), *routers):
        if id(extension) not in seen:
            app.include_router(extension)
            seen.add(id(extension))
    frontend = settings.frontend_directory
    if frontend and (frontend / "index.html").is_file():
        app.mount("/assets", ImmutableStaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-cache"})

        if (frontend / "favicon.svg").is_file():

            @app.get("/favicon.svg", include_in_schema=False)
            @app.get("/favicon.ico", include_in_schema=False)
            def favicon() -> FileResponse:
                return FileResponse(
                    frontend / "favicon.svg",
                    media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"},
                )

        # Vite copies these bundled, licensed public resources unchanged.
        for directory in ("fonts", "licences"):
            if (frontend / directory).is_dir():
                app.mount(
                    f"/{directory}", StaticFiles(directory=frontend / directory), name=directory
                )

    return app
