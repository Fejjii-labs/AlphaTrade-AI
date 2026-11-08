"""HTTP middleware: request-id propagation, access logging, and RED metrics.

Each request gets a stable ``request_id`` (honoring a well-formed inbound header
when present) which is bound to the structlog contextvars so every log line emitted
while handling the request carries it. The id is also echoed back in the
response header for client-side correlation.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.observability.context import set_trace_id
from app.observability.metrics import (
    normalize_route,
    observe_request,
    should_observe,
    track_in_progress,
)

logger = structlog.get_logger("http")

# Client-supplied correlation ids reach logs, response headers and audit rows
# (``request_id`` is String(64)). Anything else is replaced, not rejected (I10).
_CORRELATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def _correlation_id(value: str | None) -> str:
    if value and _CORRELATION_ID.fullmatch(value):
        return value
    return str(uuid.uuid4())


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind request context, time the request, emit access log + RED metrics."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        request_id_header: str = "X-Request-ID",
        trace_id_header: str = "X-Trace-ID",
        metrics_enabled: bool = False,
    ) -> None:
        super().__init__(app)
        self._header = request_id_header
        self._trace_header = trace_id_header
        self._metrics_enabled = metrics_enabled

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _correlation_id(request.headers.get(self._header))
        trace_id = _correlation_id(request.headers.get(self._trace_header))
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        set_trace_id(trace_id)

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
            endpoint=request.url.path,
        )

        observe = self._metrics_enabled and should_observe(request.url.path)
        route = normalize_route(request) if observe else ""
        if observe:
            track_in_progress(request.method, route, delta=1)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.error("request_failed", latency_ms=elapsed_ms)
            if observe:
                observe_request(
                    method=request.method,
                    route=route or "unknown",
                    status_code=500,
                    duration_seconds=time.perf_counter() - start,
                )
            raise
        finally:
            if observe:
                track_in_progress(request.method, route, delta=-1)
            structlog.contextvars.unbind_contextvars("method", "path")

        elapsed = time.perf_counter() - start
        elapsed_ms = round(elapsed * 1000, 2)
        response.headers[self._header] = request_id
        logger.info("request_completed", status_code=status_code, latency_ms=elapsed_ms)
        if observe:
            # Prefer template from matched route after routing.
            route = normalize_route(request)
            observe_request(
                method=request.method,
                route=route,
                status_code=status_code,
                duration_seconds=elapsed,
            )
        return response
