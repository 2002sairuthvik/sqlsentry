from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ... import __version__

router = APIRouter(tags=["health"])


@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root(request: Request) -> RedirectResponse:
    playground = request.app.state.sentry.settings.server.playground.enabled
    return RedirectResponse("/playground" if playground else "/docs")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.head("/health", include_in_schema=False)
def health_head() -> dict[str, str]:
    """For uptime checks and hosting health probes that use HEAD."""
    return health()
