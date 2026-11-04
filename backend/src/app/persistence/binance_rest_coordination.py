"""PostgreSQL store for the cross-instance Binance REST deadline and lease.

Each call is one short transaction on the single ``binance_usdm`` row, taken
with ``SELECT ... FOR UPDATE`` so concurrent instances serialize. Callers in
the worker catch every exception and fall back to the in-process gate.

The lease is timed by the database clock (``now()``) unless a caller passes
``now``, so clock skew between instances cannot stretch or shrink the TTL.
Deadlines keep the writer's wall clock: they come from the process gate, which
counts in wall-clock epoch seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.binance_rest_coordination import BinanceRestCoordinationRow

BINANCE_USDM_SCOPE = "binance_usdm"
_GATE_KINDS = ("ban", "rate_limit")


@dataclass(frozen=True, slots=True)
class SharedDeadline:
    kind: str
    until: datetime


@dataclass(frozen=True, slots=True)
class LeaseClaim:
    acquired: bool
    holder: str
    expires_at: datetime | None


def record_gate_deadline(
    session_factory: sessionmaker[Session],
    *,
    kind: str,
    until: datetime,
    writer: str,
    now: datetime,
    scope: str = BINANCE_USDM_SCOPE,
) -> SharedDeadline:
    """Store a 418/429 deadline, keeping the later one. Returns the stored deadline.

    A live ``ban`` is never downgraded to ``rate_limit`` by a later 429 deadline.
    """

    if kind not in _GATE_KINDS:
        raise ValueError(f"unknown gate kind {kind!r}")
    with session_factory() as session, session.begin():
        row = _locked_row(session, scope)
        status = 418 if kind == "ban" else 429
        current = _aware(row.gate_until)
        if current is not None and current > now and row.gate_kind in _GATE_KINDS:
            until = max(until, current)
            if row.gate_kind == "ban":
                kind = "ban"
        row.gate_until = until
        row.gate_kind = kind
        row.gate_status_code = status
        row.gate_updated_at = now
        row.gate_updated_by = writer[:80]
        return SharedDeadline(kind=kind, until=until)


def load_gate_deadline(
    session_factory: sessionmaker[Session],
    *,
    now: datetime,
    scope: str = BINANCE_USDM_SCOPE,
) -> SharedDeadline | None:
    """The stored deadline while it is still in the future."""

    with session_factory() as session:
        row = session.get(BinanceRestCoordinationRow, scope)
        if row is None or row.gate_kind not in _GATE_KINDS:
            return None
        until = _aware(row.gate_until)
        if until is None or until <= now:
            return None
        return SharedDeadline(kind=row.gate_kind, until=until)


def try_acquire_market_lease(
    session_factory: sessionmaker[Session],
    *,
    owner: str,
    ttl_seconds: float,
    now: datetime | None = None,
    scope: str = BINANCE_USDM_SCOPE,
) -> LeaseClaim:
    """Claim or renew the market-read lease. A live lease of another owner wins.

    ``now`` defaults to the database clock, read in the same transaction.
    """

    owner = owner[:80]
    with session_factory() as session, session.begin():
        row = _locked_row(session, scope)
        if now is None:
            now = _database_now(session)
        expires = _aware(row.lease_expires_at)
        held_by_other = row.lease_owner not in ("", owner) and expires is not None and expires > now
        if held_by_other:
            return LeaseClaim(acquired=False, holder=row.lease_owner, expires_at=expires)
        row.lease_owner = owner
        row.lease_expires_at = now + timedelta(seconds=ttl_seconds)
        row.lease_heartbeat_at = now
        return LeaseClaim(acquired=True, holder=owner, expires_at=row.lease_expires_at)


def release_market_lease(
    session_factory: sessionmaker[Session],
    *,
    owner: str,
    scope: str = BINANCE_USDM_SCOPE,
) -> bool:
    """Release the lease only if ``owner`` still holds it."""

    with session_factory() as session, session.begin():
        row = session.get(BinanceRestCoordinationRow, scope, with_for_update=True)
        if row is None or row.lease_owner != owner[:80]:
            return False
        row.lease_owner = ""
        row.lease_expires_at = None
        return True


def _locked_row(session: Session, scope: str) -> BinanceRestCoordinationRow:
    """Lock the scope row, creating it first. Concurrent creators do not conflict."""

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    session.execute(
        insert(BinanceRestCoordinationRow)
        .values(scope=scope)
        .on_conflict_do_nothing(index_elements=["scope"])
    )
    row = session.get(BinanceRestCoordinationRow, scope, with_for_update=True)
    assert row is not None
    return row


def _database_now(session: Session) -> datetime:
    """``now()`` of the database: one clock for every instance."""

    moment = _aware(session.execute(select(func.now())).scalar_one())
    assert moment is not None
    return moment


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; stored values are UTC."""

    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)
