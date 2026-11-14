from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from proscenium.api.composition import configure_services, feature_routers
from proscenium.api.errors import DomainError
from proscenium.api.router import router
from proscenium.configuration import Settings
from proscenium.services import Services


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
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.services = services or Services.create(settings)
        try:
            configure_services(app.state.services)
            yield
        finally:
            if services is None:
                app.state.services.close()

    app = FastAPI(
        title="Proscenium", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
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
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(frontend / "index.html")

    return app
