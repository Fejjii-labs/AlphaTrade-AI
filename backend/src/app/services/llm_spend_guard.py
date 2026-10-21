"""In-process LLM spend accumulator for warning thresholds (logs only)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Lock

import structlog

logger = structlog.get_logger(__name__)

_lock = Lock()
_day: date | None = None
_total_usd = Decimal("0")


def record_llm_spend(estimated_usd: Decimal, *, threshold_usd: Decimal | None) -> None:
    """Add spend for the current UTC day and log a warning when over threshold."""
    if estimated_usd <= 0 or threshold_usd is None or threshold_usd <= 0:
        return
    global _day, _total_usd
    today = datetime.now(UTC).date()
    with _lock:
        if _day != today:
            _day = today
            _total_usd = Decimal("0")
        _total_usd += estimated_usd
        total = _total_usd
    if total >= threshold_usd:
        logger.warning(
            "llm_daily_spend_threshold_exceeded",
            estimated_daily_spend_usd=str(total),
            threshold_usd=str(threshold_usd),
        )


def reset_for_tests() -> None:
    global _day, _total_usd
    with _lock:
        _day = None
        _total_usd = Decimal("0")
