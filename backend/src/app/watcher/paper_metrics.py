"""Low-cardinality Prometheus metrics for the paper Watcher runtime."""

from __future__ import annotations

from prometheus_client import Counter, Gauge

from app.observability.metrics import REGISTRY

WATCHER_PAPER_CYCLES_TOTAL = Counter(
    "watcher_paper_cycles_total",
    "Paper Watcher runtime cycles",
    labelnames=("result",),
    registry=REGISTRY,
)
WATCHER_PAPER_SCANS_TOTAL = Counter(
    "watcher_paper_scans_total",
    "Paper Watcher scans",
    labelnames=("result",),
    registry=REGISTRY,
)
WATCHER_PAPER_CANDIDATES_TOTAL = Counter(
    "watcher_paper_candidates_total",
    "Candidates persisted by the paper Watcher runtime",
    registry=REGISTRY,
)
WATCHER_PAPER_RUNTIME_ACTIVE = Gauge(
    "watcher_paper_runtime_active",
    "1 when the paper Watcher loop is running",
    registry=REGISTRY,
)
WATCHER_PAPER_BAR_CLOSE_WAITS_TOTAL = Counter(
    "watcher_paper_bar_close_waits_total",
    "Post-close settlement waits before a paper Watcher scan",
    registry=REGISTRY,
)
WATCHER_PAPER_FORMING_CANDLE_RETRIES_TOTAL = Counter(
    "watcher_paper_forming_candle_retries_total",
    "Single bounded retries after a forming-candle scan refusal",
    registry=REGISTRY,
)

_SCAN_RESULTS = frozenset(
    {
        "succeeded",
        "replay",
        "skipped",
        "blocked",
        "failed",
        "stale_evidence",
        "provider_outage",
        "organization_mismatch",
        "candidate_creation_failed",
        "watcher_disabled",
        "activation_refused",
        "lease_held",
        "lease_renewal_lost",
        "expired",
        "confirmed_setup",
        "watch",
        "no_setup",
        "partial_match",
        "invalidated",
        "other",
    }
)


def observe_cycle(result: str) -> None:
    WATCHER_PAPER_CYCLES_TOTAL.labels(result=_bounded(result)).inc()


def observe_scan(result: str) -> None:
    WATCHER_PAPER_SCANS_TOTAL.labels(result=_bounded(result)).inc()


def observe_candidates(count: int) -> None:
    if count > 0:
        WATCHER_PAPER_CANDIDATES_TOTAL.inc(count)


def set_runtime_active(active: bool) -> None:
    WATCHER_PAPER_RUNTIME_ACTIVE.set(1 if active else 0)


def observe_bar_close_wait() -> None:
    WATCHER_PAPER_BAR_CLOSE_WAITS_TOTAL.inc()


def observe_forming_candle_retry() -> None:
    WATCHER_PAPER_FORMING_CANDLE_RETRIES_TOTAL.inc()


def _bounded(result: str) -> str:
    token = result.strip().lower() or "other"
    if token in _SCAN_RESULTS:
        return token
    return "other"
