"""UTC day boundary used by loosening and by the daily breakers."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from app.services.risk_policy.errors import RiskPolicyRejected

LOOSENING_DELAY = timedelta(hours=24)


def next_utc_day(now: datetime) -> datetime:
    """Midnight at the start of the next UTC date."""

    if now.tzinfo is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    current = now.astimezone(UTC)
    return datetime.combine(current.date() + timedelta(days=1), time.min, tzinfo=UTC)


def loosening_effective_at(now: datetime) -> datetime:
    """Later of the next UTC midnight and 24 hours after the apply."""

    current = now.astimezone(UTC) if now.tzinfo is not None else None
    if current is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    return max(next_utc_day(current), current + LOOSENING_DELAY)
