from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .paths import FRONTEND_DIST
from backend.api.routes import OPENAPI_TAGS, router
from backend.logging_config import configure_logging, get_logger
from backend.services.container import get_services

configure_logging()
logger = get_logger(__name__)


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                response = await super().get_response("index.html", scope)
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                return response
            raise
        if path in {"", ".", "/", "index.html"} or "." not in path.rsplit("/", 1)[-1]:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        elif path.startswith("assets/"):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    logger.info("Application started", extra={"component": "app"})
    try:
        yield
    finally:
        logger.info("Application stopped", extra={"component": "app"})


app = FastAPI(title="Sentero API", version="0.1.0", openapi_tags=OPENAPI_TAGS, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Only these endpoints may be called without an authenticated Sentero session.
# Every other /api/* endpoint is protected by the middleware below.
PUBLIC_PATHS = {
    "/health",
    "/api/sentero/auth/login",
    "/api/sentero/auth/me",
    "/api/sentero/auth/setup",
    "/api/sentero/auth/status",
    "/api/sentero/auth/forgot-password",
    "/api/sentero/auth/reset-password",
    "/api/sentero/auth/logout",
}
AUTH_SCHEME_NAME = "HTTPBearer"


@app.middleware("http")
async def require_sentero_auth(request, call_next):
    started = time.perf_counter()
    path = request.url.path.rstrip("/") or "/"
    logger.debug(
        "Request received",
        extra={"component": "api", "method": request.method, "path": path, "request_id": request.headers.get("x-request-id", "")},
    )

    # Let CORS preflight requests pass so browsers can reach protected endpoints
    # with authenticated requests afterwards.
    if request.method == "OPTIONS":
        return await call_next(request)

    if path.startswith("/api/") and path not in PUBLIC_PATHS:
        try:
            get_services().auth.user_from_request(request, required=True)
        except Exception as exc:
            logger.warning(
                "Authentication rejected",
                extra={"component": "auth", "path": path, "request_id": request.headers.get("x-request-id", "")},
            )
            return JSONResponse(
                {"detail": getattr(exc, "detail", "Nicht angemeldet.")},
                status_code=getattr(exc, "status_code", 401),
            )
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.debug(
        "Request completed",
        extra={
            "component": "api",
            "method": request.method,
            "path": path,
            "status_code": response.status_code,
            "elapsed_ms": elapsed_ms,
            "request_id": request.headers.get("x-request-id", ""),
        },
    )
    return response


app.include_router(router)


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
        tags=OPENAPI_TAGS,
    )
    security_schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    security_schemes.setdefault(AUTH_SCHEME_NAME, {"type": "http", "scheme": "bearer"})

    protected_security = {AUTH_SCHEME_NAME: []}
    for path, operations in schema.get("paths", {}).items():
        normalized_path = path.rstrip("/") or "/"
        if not normalized_path.startswith("/api/") or normalized_path in PUBLIC_PATHS:
            continue
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            security = operation.setdefault("security", [])
            if protected_security not in security:
                security.insert(0, protected_security)

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if FRONTEND_DIST.exists():
    app.mount("/", SPAStaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
