"""Protected Prometheus/OpenMetrics scrape endpoint (AT-016)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.config import Environment
from app.core.dependencies import SettingsDep
from app.core.errors import AuthError, NotFoundError
from app.observability.metrics import render_latest

router = APIRouter(tags=["metrics"])


@router.get(
    "/metrics",
    summary="Prometheus/OpenMetrics scrape (gated)",
    include_in_schema=False,
)
def scrape_metrics(request: Request, settings: SettingsDep) -> Response:
    """Expose RED metrics when explicitly enabled and authorized.

    Health/readiness remain separate. Default is disabled. Outside local, a
    scrape bearer token is required (enforced at Settings validation too).
    """
    if not settings.metrics_enabled:
        raise NotFoundError("Metrics endpoint is disabled.")

    token = settings.metrics_scrape_token.strip()
    if token:
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {token}"
        if not secrets.compare_digest(auth, expected):
            raise AuthError("Invalid metrics scrape credentials.")
    elif settings.environment is not Environment.LOCAL:
        # Belt-and-suspenders — Settings validator should already refuse this.
        raise AuthError("Metrics scrape token is required outside local.")

    body, content_type = render_latest()
    return Response(content=body, media_type=content_type)
