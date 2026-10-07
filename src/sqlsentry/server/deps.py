from __future__ import annotations

import json
import logging

from fastapi import Request

from ..engine import SQLSentry
from .auth import Principal

audit_log = logging.getLogger("sqlsentry.audit")


def sentry(request: Request) -> SQLSentry:
    return request.app.state.sentry


def principal(request: Request) -> Principal:
    """Authenticate the caller and apply the per-consumer rate limit."""
    auth = request.headers.get("authorization", "")
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key")
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
