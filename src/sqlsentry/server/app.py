"""FastAPI application factory (``pip install sqlsentry[server]``).

sqlsentry serve --config sqlsentry.yaml
# or: uvicorn --factory sqlsentry.server.app:create_app
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import Settings
from ..engine import SQLSentry
from ..errors import SQLSentryError
from .auth import Authenticator
from .ratelimit import RateLimiter
from .routes import datasources, generations, health, sql

log = logging.getLogger("sqlsentry.server")


def create_app(sentry: SQLSentry | None = None, settings: Settings | None = None) -> FastAPI:
    sentry = sentry or SQLSentry(settings or Settings.load())
    cfg = sentry.settings
    if not cfg.server.auth_enabled:
        log.warning("Authentication is DISABLED. Never expose this server beyond localhost.")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        sentry.close()

    app = FastAPI(
        lifespan=lifespan,
        title="sqlsentry",
        version=__version__,
        description="Safety-first, model-agnostic natural language to SQL.",
    )
    app.state.sentry = sentry
    app.state.auth = Authenticator(cfg)
    app.state.limiter = RateLimiter(cfg.server.rate_limit_per_minute)

    if cfg.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.server.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
        )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:16]}"
        request.state.request_id = rid[:64]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error_body(request: Request, code: str, message: str, details: dict | None = None) -> dict:
        err = {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}
        if details:
            err["details"] = details
        return {"error": err}

    @app.exception_handler(SQLSentryError)
    async def handle_sqlsentry_error(request: Request, exc: SQLSentryError):
        headers = {}
        if "retry_after_s" in exc.details:
            headers["Retry-After"] = str(exc.details["retry_after_s"])
        if exc.http_status >= 500:
            log.error("request %s failed: %s", getattr(request.state, "request_id", "?"), exc.message)
        return JSONResponse(
            status_code=exc.http_status,
            content=error_body(request, exc.code, exc.message, exc.details or None),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return JSONResponse(status_code=400, content=error_body(request, "bad_request", "; ".join(problems)))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        log.exception("unhandled error in request %s", getattr(request.state, "request_id", "?"))
        return JSONResponse(status_code=500, content=error_body(request, "internal_error", "Internal error."))

    for router in (health.router, sql.router, generations.router, datasources.router):
        app.include_router(router)
    return app
