"""BloFin demo P&L and fees come from BloFin, never from app price math.

Regression for the CCUSDT manual demo trade closed 2026-11-12 14:20Z: the
mirror left net/gross/fees NULL, the dashboard then showed size x (exit -
entry) = +627.75 using a contract count as the size, and the sample counted
two cancelled orders as trades.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JournalTrade
from app.repositories.journal_trades import JournalTradeStatsRow
from app.schemas.common import (
    JournalTradeSource,
    JournalTradeStatus,
    MembershipRole,
    TradeDirection,
    TradeResult,
)
from app.schemas.dashboard_demo_account import DashboardDemoAccount
from app.security.tenant import TenantContext
from app.services.blofin_demo_manual_mirror import (
    BloFinDemoManualMirror,
    publish_mirror_performance,
    record_mirror_cycle,
)
from tests.test_blofin_demo_manual_mirror import (  # noqa: F401 - fixtures
    _PIN_UID,
    NOW,
    ORG,
    USER,
    WINDOW_BEGIN,
    _drafts,
    _pinned_mirror_account,
    _rows,
    _settings,
    session,
)
from tests.test_journal_performance_dashboard import _build, _row

_CC_INSTRUMENT = {"instId": "CC-USDT", "contractValue": "10", "settleCurrency": "USDT"}
_CC_OPEN = datetime(2026, 11, 12, 9, 5, tzinfo=UTC)
_CC_CLOSE = datetime(2026, 11, 12, 14, 20, tzinfo=UTC)


def _ms(when: datetime) -> str:
    return str(int(when.timestamp() * 1000))


def _cc_history(**updates: str) -> dict[str, str]:
    """BloFin positions-history row whose price move does NOT equal realizedPnl."""
    row = {
        "historyId": "cc-h1",
        "positionId": "cc-p1",
        "instId": "CC-USDT",
        "positionSide": "net",
        "side": "buy",
        "closePositions": "225980",
        "maxPositions": "225980",
        "openAveragePrice": "0.07",
        "closeAveragePrice": "0.0702778",
        "createTime": _ms(_CC_OPEN),
        "updateTime": _ms(_CC_CLOSE),
        "realizedPnl": "62.78",
        "fee": "9.51",
    }
    row.update(updates)
    return row


# --------------------------------------------------------------------------- #
# Task 1: no price-derived P&L anywhere on the dashboard
# --------------------------------------------------------------------------- #


def _pending_cc_row() -> JournalTradeStatsRow:
    return replace(
        _row(None, _CC_CLOSE, source=JournalTradeSource.BLOFIN_DEMO_MANUAL),
        direction=TradeDirection.LONG,
        entry_price=Decimal("0.07"),
        exit_price=Decimal("0.0702778"),
        size=Decimal("2259800"),
        exit_reason="unverified:pnl_mismatch",
    )


def test_cc_trade_without_blofin_pnl_is_pending_not_price_math() -> None:
    result = _build([_pending_cc_row()])
    s = result.summary
    assert s.net_pnl is None
    assert s.pnl_sample_count == 0
    assert s.pnl_pending_count == 1
    assert s.trade_count == 1
    assert s.pnl_trade_coverage == "0 of 1 trades verified"
    [day] = result.days
    assert day.net_pnl is None
    assert day.pnl_pending_count == 1
    assert result.cumulative == []
    assert any("P&L pending from BloFin" in w for w in result.warnings)
    assert "627" not in result.model_dump_json()


def test_pending_rows_do_not_change_verified_totals() -> None:
    verified = replace(
        _row("5", _CC_CLOSE - timedelta(days=1), source=JournalTradeSource.BLOFIN_DEMO_MANUAL),
        exit_reason="closed",
    )
    result = _build([verified, _pending_cc_row()])
    assert result.summary.net_pnl == Decimal("5")
    assert result.summary.pnl_sample_count == 1
    assert result.summary.pnl_pending_count == 1
    assert [d.net_pnl for d in result.days] == [Decimal("5"), None]


# --------------------------------------------------------------------------- #
# Task 3: cancelled / unfilled orders are not trades
# --------------------------------------------------------------------------- #


def _cancelled(reason: str | None, **updates: Any) -> JournalTradeStatsRow:
    base = replace(
        _row(None, _CC_CLOSE, source=JournalTradeSource.BLOFIN_DEMO_MANUAL),
        exit_reason=reason,
    )
    return replace(base, **updates)


def test_cancelled_and_unfilled_orders_are_not_in_sample_win_rate_or_calendar() -> None:
    filled = replace(
        _row("-2", _CC_CLOSE, source=JournalTradeSource.BLOFIN_DEMO_MANUAL),
        entry_price=Decimal("1"),
        exit_price=Decimal("0.9"),
        size=Decimal("20"),
        exit_reason="closed",
    )
    rows = [
        filled,
        _cancelled("demo_cancelled_unfilled", entry_price=Decimal("1"), size=Decimal("5")),
        _cancelled("Order CANCELLED on venue", entry_price=Decimal("1"), size=Decimal("5")),
        _cancelled("closed", entry_price=Decimal("1"), size=Decimal("0")),
        # A venue-backed demo row with no entry fill or no exit fill never traded.
        _cancelled("closed", entry_price=None, exit_price=None, size=Decimal("5")),
        _cancelled(
            None,
            entry_price=Decimal("1"),
            exit_price=None,
            size=Decimal("5"),
        ),
    ]
    result = _build(rows)
    s = result.summary
    assert s.trade_count == 1
    assert s.losses == 1 and s.wins == 0
    assert s.win_rate == 0.0
    assert s.pnl_pending_count == 0
    assert s.pnl_trade_coverage == "1 of 1 trades verified"
    assert [(d.trade_count, d.net_pnl) for d in result.days] == [(1, Decimal("-2"))]


def test_manual_journal_rows_without_prices_still_count() -> None:
    # Hand-logged journal rows may omit prices; only venue-backed rows need fills.
    rows = [_row("3", _CC_CLOSE), _row(None, _CC_CLOSE, result=TradeResult.WIN)]
    assert _build(rows).summary.trade_count == 2


# --------------------------------------------------------------------------- #
# Task 2: the mirror stores BloFin's realizedPnl and fee as-is
# --------------------------------------------------------------------------- #


def test_mirror_takes_gross_and_fees_from_blofin_even_when_price_math_disagrees() -> None:
    built = _drafts(
        [_cc_history(createTime=_ms(WINDOW_BEGIN - timedelta(days=1)))],
        instruments=[_CC_INSTRUMENT],
    )
    [draft] = built.closed
    assert draft.gross_pnl == Decimal("62.78")
    assert draft.fees == Decimal("9.51")
    assert draft.net_pnl == Decimal("53.27")
    assert draft.result is TradeResult.WIN
    assert draft.exit_reason == "closed"
    assert draft.verified is True


def test_mirror_subtracts_blofin_funding_when_blofin_reports_it() -> None:
    built = _drafts(
        [_cc_history(createTime=_ms(NOW - timedelta(days=2)), updateTime=_ms(NOW))],
        instruments=[_CC_INSTRUMENT],
        funding=[
            {
                "billId": "cc-f1",
                "instId": "CC-USDT",
                "fundingFee": "-0.25",
                "fundingTime": _ms(NOW - timedelta(days=1)),
            }
        ],
    )
    [draft] = built.closed
    assert draft.funding == Decimal("0.25")
    assert draft.net_pnl == Decimal("53.02")


def test_mirror_leaves_pnl_pending_when_blofin_omits_realized_pnl_or_fee() -> None:
    for missing in ("realizedPnl", "fee"):
        row = _cc_history()
        del row[missing]
        [draft] = _drafts([row], instruments=[_CC_INSTRUMENT]).closed
        assert draft.net_pnl is None and draft.gross_pnl is None and draft.fees is None
        assert draft.exit_reason == f"unverified:missing_blofin_{'pnl' if missing == 'realizedPnl' else 'fee'}"
        assert draft.result is TradeResult.OPEN


# --------------------------------------------------------------------------- #
# Backfill: an already-closed pending row is filled on the next sync
# --------------------------------------------------------------------------- #


class _WindowClient:
    """BloFin stub that honours begin/end on positions-history like the venue."""

    def __init__(self, history: list[dict[str, str]]) -> None:
        self.history = history
        self.history_windows: list[tuple[int, int]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
        signed: bool = False,
        before_send: object = None,
    ) -> object:
        del body, signed, before_send
        assert method == "GET"
        if path == "/api/v1/user/query-apikey":
            return {"readOnly": 1, "uid": _PIN_UID}
        if path == "/api/v1/market/instruments":
            return [_CC_INSTRUMENT]
        if path == "/api/v1/account/positions":
            return []
        assert params is not None
        if params.get("after"):
            return []
        if path != "/api/v1/account/positions-history":
            return []
        begin, end = int(str(params["begin"])), int(str(params["end"]))
        self.history_windows.append((begin, end))
        return [row for row in self.history if begin <= int(row["updateTime"]) <= end]


def _pending_cc_journal(session: Session) -> JournalTrade:
    row = JournalTrade(
        organization_id=ORG,
        user_id=USER,
        source=JournalTradeSource.BLOFIN_DEMO_MANUAL,
        status=JournalTradeStatus.CLOSED,
        symbol="CCUSDT",
        exchange="BLOFIN_DEMO",
        timeframe="manual",
        direction=TradeDirection.LONG,
        entry_price=Decimal("0.07"),
        exit_price=Decimal("0.0702778"),
        entry_time=_CC_OPEN,
        exit_time=_CC_CLOSE,
        size=Decimal("2259800"),
        result=TradeResult.OPEN,
        exit_reason="unverified:pnl_mismatch",
        external_ref="blofin-demo-history:cc-h1",
        tags=["my note", "blofin-history:cc-h1", "blofin-position:cc-p1", "pnl-unverified"],
    )
    session.add(row)
    session.commit()
    return row


def test_next_sync_backfills_blofin_pnl_for_an_old_pending_close(session: Session) -> None:
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)  # CC close is outside the 7-day window
    row = _pending_cc_journal(session)
    client = _WindowClient([_cc_history()])
    report = BloFinDemoManualMirror(client, now=now).apply(
        session, organization_id=ORG, user_id=USER, settings=_settings()
    )
    session.commit()
    # A narrow read around the pending trade, not a wider main window.
    assert any(
        begin <= int(_ms(_CC_OPEN)) and int(_ms(_CC_CLOSE)) <= end and end - begin < 7 * 86_400_000
        for begin, end in client.history_windows
    )
    [stored] = _rows(session)  # updated in place: nothing created, nothing deleted
    assert stored.id == row.id
    assert stored.gross_pnl == Decimal("62.78")
    assert stored.fees == Decimal("9.51")
    assert stored.net_pnl == Decimal("53.27")
    assert stored.result is TradeResult.WIN
    assert stored.exit_reason == "closed"
    assert "my note" in (stored.tags or [])
    assert report.created == 0

    # The account card counts it: BloFin's figures, not a price recomputation.
    record_mirror_cycle(session, organization_id=ORG, user_id=USER, outcome="ok")
    session.commit()
    account = DashboardDemoAccount(status="ok", message="fixture")
    tenant = TenantContext(USER, ORG, "mirror@test.example", MembershipRole.TRADER)
    publish_mirror_performance(account, session=session, tenant=tenant, settings=_settings())
    assert account.performance.realized_pnl == Decimal("53.27")
    assert account.performance.trade_count == 1


def test_backfill_leaves_row_pending_when_blofin_no_longer_returns_it(session: Session) -> None:
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)
    row = _pending_cc_journal(session)
    BloFinDemoManualMirror(_WindowClient([]), now=now).apply(
        session, organization_id=ORG, user_id=USER, settings=_settings()
    )
    session.commit()
    stored = session.scalar(select(JournalTrade).where(JournalTrade.id == row.id))
    assert stored is not None
    assert stored.status is JournalTradeStatus.CLOSED
    assert stored.net_pnl is None and stored.gross_pnl is None
    assert stored.exit_reason == "unverified:pnl_mismatch"

