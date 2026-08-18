from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .paths import FRONTEND_DIST
from backend.api.routes import OPENAPI_TAGS, box_setup_router, mail_router, router
from backend.logging_config import configure_logging, get_logger
from backend.services.container import get_services

configure_logging()
logger = get_logger(__name__)


def behavior_snapshot_interval() -> float:
    try:
        value = float(os.getenv("SENTERO_BEHAVIOR_SNAPSHOT_INTERVAL", "30"))
    except (TypeError, ValueError):
        value = 30.0
    return max(10.0, value)


async def behavior_snapshot_loop() -> None:
    interval = behavior_snapshot_interval()
    await asyncio.sleep(2)
    while True:
        try:
            written = await asyncio.to_thread(get_services().sentero.record_behavior_snapshot)
            logger.debug("Behavior snapshot refresh completed", extra={"component": "behavior", "written_events": written, "interval_seconds": interval})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Behavior snapshot refresh failed", extra={"component": "behavior"})
        await asyncio.sleep(interval)


async def network_startup_check() -> None:
    try:
        await asyncio.to_thread(get_services().network.ensure_first_boot_setup)
        await asyncio.to_thread(get_services().notification.process_pending_queue)
    except Exception:
        logger.exception("Network startup check failed", extra={"component": "network"})


async def network_maintenance_loop() -> None:
    await asyncio.sleep(5)
    while True:
        try:
            services = get_services()
            result = await asyncio.to_thread(services.network.maintain_once)
            if result.get("actions"):
                logger.info("Network maintenance actions applied", extra={"component": "network", "actions": result.get("actions")})
            await asyncio.to_thread(services.notification.process_pending_queue)
            interval = services.network.failover_config().check_interval_seconds
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Network maintenance failed", extra={"component": "network"})
            interval = 30
        await asyncio.sleep(interval)


async def mail_assistant_loop() -> None:
    await asyncio.sleep(8)
    backoff = 1
    while True:
        try:
            services = get_services()
            assistant = services.mail_assistant
            if not assistant.enabled():
                await asyncio.sleep(60)
                continue
            result = await asyncio.to_thread(assistant.poll_once)
            if result.get("processed"):
                logger.info("Mail assistant processed messages", extra={"component": "mail_assistant", "processed": result.get("processed")})
            backoff = 1
            await asyncio.sleep(assistant.config.poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mail assistant polling failed", extra={"component": "mail_assistant"})
            await asyncio.sleep(min(300, 5 * backoff))
            backoff = min(backoff * 2, 60)


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
    await network_startup_check()
    behavior_task = asyncio.create_task(behavior_snapshot_loop())
    network_task = asyncio.create_task(network_maintenance_loop())
    mail_task = asyncio.create_task(mail_assistant_loop())
    try:
        yield
    finally:
        behavior_task.cancel()
        network_task.cancel()
        mail_task.cancel()
        with suppress(asyncio.CancelledError):
            await behavior_task
        with suppress(asyncio.CancelledError):
            await network_task
        with suppress(asyncio.CancelledError):
            await mail_task
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
    "/api/setup/box-network/status",
    "/api/setup/box-network/wifi",
    "/api/setup/network/status",
    "/api/setup/network/wifi/networks",
    "/api/setup/network/wifi/connect",
    "/api/setup/network/cellular/connect",
    "/api/mail/discover",
    "/api/mail/verify",
}
PUBLIC_PREFIXES = (
    "/api/sentero/exchange/",
)
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

    if path.startswith("/api/") and path not in PUBLIC_PATHS and not any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
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
app.include_router(box_setup_router)
app.include_router(mail_router)


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
        if (
            not normalized_path.startswith("/api/")
            or normalized_path in PUBLIC_PATHS
            or any(normalized_path.startswith(prefix) for prefix in PUBLIC_PREFIXES)
        ):
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
