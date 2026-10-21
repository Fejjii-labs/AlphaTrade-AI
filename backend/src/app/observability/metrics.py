"""Prometheus/OpenMetrics RED metrics (AT-016).

Low-cardinality labels only: HTTP method, normalized route template, status class.
Never label with user/org/symbol/request IDs, raw paths, query strings, or errors.
"""

from __future__ import annotations

from typing import Final

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client import generate_latest as _generate_latest
from starlette.requests import Request

# Process-local registry so tests can isolate collectors when needed.
REGISTRY: Final[CollectorRegistry] = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=("method", "route", "status_class"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=("method", "route", "status_class"),
    registry=REGISTRY,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently in progress",
    labelnames=("method", "route"),
    registry=REGISTRY,
)

MODEL_ROUTER_TIER_A_TIMEOUT_FALLBACK_TOTAL = Counter(
    "model_router_tier_a_timeout_fallback_total",
    "Tier A provider timeouts that fell back to Tier B",
    registry=REGISTRY,
)

_METRICS_PATHS = frozenset({"/metrics"})


def status_class(status_code: int) -> str:
    """Map an HTTP status code to a bounded class label (2xx, 4xx, …)."""
    if status_code < 100:
        return "1xx"
    family = status_code // 100
    if family in (1, 2, 3, 4, 5):
        return f"{family}xx"
    return "unknown"


def normalize_route(request: Request) -> str:
    """Return a low-cardinality route template for metric labels."""
    route = request.scope.get("route")
    path_format = getattr(route, "path", None)
    if isinstance(path_format, str) and path_format:
        return path_format
    # Unmatched routes collapse to one label — never emit raw paths/query strings.
    return "unmatched"


def should_observe(path: str) -> bool:
    return path not in _METRICS_PATHS


def observe_request(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    """Record RED observations for a completed request."""
    labels = {
        "method": method.upper(),
        "route": route,
        "status_class": status_class(status_code),
    }
    HTTP_REQUESTS_TOTAL.labels(**labels).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(**labels).observe(max(duration_seconds, 0.0))


def track_in_progress(method: str, route: str, *, delta: int) -> None:
    gauge = HTTP_REQUESTS_IN_PROGRESS.labels(method=method.upper(), route=route)
    if delta >= 0:
        gauge.inc(delta)
    else:
        gauge.dec(abs(delta))


def render_latest() -> tuple[bytes, str]:
    """Return OpenMetrics/Prometheus exposition body and content type."""
    return _generate_latest(REGISTRY), CONTENT_TYPE_LATEST
