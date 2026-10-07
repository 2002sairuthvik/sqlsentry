from __future__ import annotations

import json
import logging

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..engine import SQLSentry
from .auth import Principal

audit_log = logging.getLogger("sqlsentry.audit")

# Declared so the OpenAPI docs (/docs) show an "Authorize" button for the API key.
bearer = HTTPBearer(auto_error=False, description="API key from `sqlsentry hash-key`")


def sentry(request: Request) -> SQLSentry:
    return request.app.state.sentry


def principal(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> Principal:
    """Authenticate the caller and apply the per-consumer rate limit."""
    key = credentials.credentials if credentials else request.headers.get("x-api-key")
    if key and key.lower().startswith("bearer "):  # "Bearer" pasted into the /docs Authorize box
        key = key[7:].strip()
    p = request.app.state.auth.authenticate(key)
    request.app.state.limiter.check(p.name)
    request.state.consumer = p.name
    return p


def audit(request: Request, event: str, **fields) -> None:
    record = {
        "event": event,
        "request_id": getattr(request.state, "request_id", None),
        "consumer": getattr(request.state, "consumer", None),
        **fields,
    }
    audit_log.info(json.dumps(record, default=str))
