"""Hand-traded BloFin demo journal mirror. Fixture payloads only. No venue calls."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.blofin_readonly_access import BloFinReadOnlyClient
from app.core.config import ExecutionMode, Settings
from app.core.errors import ExchangeDemoInactiveError, ValidationAppError
from app.db.base import Base
from app.db.models import JournalTrade, Organization, User
from app.schemas.common import (
    JournalTradeSource,
    JournalTradeStatus,
    MembershipRole,
    Timeframe,
    TradeDirection,
    TradeResult,
)
from app.schemas.dashboard_demo_account import DashboardDemoAccount
from app.schemas.journal_trades import (
    JournalTradeAnnotation,
    JournalTradeCreate,
    JournalTradeUpdate,
)
from app.security.tenant import TenantContext
from app.services.audit_service import AuditService
from app.services.blofin_demo_manual_mirror import (
    METHOD,
    BloFinDemoManualMirror,
    MirrorReadError,
    drafts_from_raw,
    publish_mirror_performance,
    record_mirror_cycle,
    strategy_group_key,
)
from app.services.journal_trade_service import JournalTradeService

NOW = datetime(2026, 10, 18, tzinfo=UTC)
WINDOW_BEGIN = datetime(2026, 10, 11, 0, 0, 1, tzinfo=UTC)
STUCK = uuid.UUID("912f2bfd-9061-4196-82bd-a17a0650c14e")
ORG = uuid.UUID("00000000-0000-0000-0000-00000000b101")
USER = uuid.UUID("00000000-0000-0000-0000-00000000b111")
_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "blofin_demo_manual_mirror.json").read_text()
)
_Q = Decimal("0.00000001")


def _settings(**updates: object) -> Settings:
    base: dict[str, object] = {
        "environment": "local",
        "execution_mode": "paper",
        "enable_real_trading": False,
        "exchange_mode": "paper_internal",
        "provider_mode": "mock",
        "blofin_demo_manual_mirror_enabled": True,
    }
    base.update(updates)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


class _Client:
    def __init__(self, bundle: dict[str, list[dict[str, object]]]) -> None:
        self.bundle = bundle
        self.calls: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
        signed: bool = False,
        before_send: object = None,
    ) -> list[dict[str, object]]:
        del before_send
        assert method == "GET"
        assert body is None
        self.calls.append((method, path))
        if path == "/api/v1/market/instruments":
            assert signed is False and not params
            override = self.bundle.get(path)
            return _FIXTURE["instruments"] if override is None else override
        if path == "/api/v1/account/positions":
            assert signed is True
            return self.bundle["positions"]
        assert signed is True
        assert params is not None
        if params.get("after"):
            return []
        return self.bundle[path]


def _paths(bundle: dict[str, list[dict[str, object]]]) -> dict[str, list[dict[str, object]]]:
    return {
        "positions": bundle["positions"],
        "/api/v1/account/positions-history": bundle["history"],
        "/api/v1/trade/fills-history": bundle["fills"],
        "/api/v1/account/funding-fees": bundle["funding"],
        "/api/v1/trade/orders-history": bundle["orders"],
    }


def _engine() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session() -> Iterator[Session]:
    maker = _engine()
    with maker() as current:
        current.add(Organization(id=ORG, name="Mirror Org"))
        current.add(User(id=USER, email="mirror@test.example", hashed_password="not-a-real-hash"))
        current.commit()
        yield current


def _apply(session: Session, bundle: dict[str, object], **settings: object) -> object:
    client = _Client(_paths(bundle))  # type: ignore[arg-type]
    report = BloFinDemoManualMirror(client, now=NOW).apply(
        session,
        organization_id=ORG,
        user_id=USER,
        settings=_settings(**settings),
    )
    session.commit()
    return report


def _rows(session: Session) -> list[JournalTrade]:
    return [
        row
        for row in session.scalars(select(JournalTrade).order_by(JournalTrade.external_ref)).all()
        if row.external_ref != "blofin-demo-mirror-cycle"
    ]


def _money(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(_Q)


def _drafts(history: list[dict[str, object]], **extra: object) -> object:
    payload = {
        "instruments": _FIXTURE["instruments"],
        "positions": [],
        "history": history,
        "fills": [],
        "funding": [],
        "orders": [],
        "window_begin": WINDOW_BEGIN,
        "window_end": NOW,
    }
    payload.update(extra)
    return drafts_from_raw(**payload)  # type: ignore[arg-type]


def test_long_matches_price_fee_and_paid_funding_and_ignores_fill_pnl(session: Session) -> None:
    report = _apply(session, _FIXTURE["long"])
    assert report.created == 1  # type: ignore[attr-defined]
    row = _rows(session)[0]
    assert row.source is JournalTradeSource.BLOFIN_DEMO_MANUAL
    assert row.symbol == "BTCUSDT"
    assert row.direction is TradeDirection.LONG
    assert row.status is JournalTradeStatus.CLOSED
    assert row.external_ref == "blofin-demo-history:h-long"
    assert _money(row.size) == Decimal("0.02000000")
    assert row.leverage == Decimal("5")
    assert _money(row.gross_pnl) == Decimal("0.20000000")
    assert _money(row.fees) == Decimal("0.01000000")
    assert _money(row.funding) == Decimal("0.00400000")
    assert _money(row.net_pnl) == Decimal("0.18600000")
    assert row.gross_pnl != Decimal("999")
    assert row.result is TradeResult.WIN
    assert row.planned_stop_price == Decimal("90")
    assert _money(row.planned_risk_amount) == Decimal("0.20000000")
    assert row.planned_targets[0]["price"] == "120"
    assert "pnl-verified" in row.tags
    assert "blofin-order:o-long" in row.tags
    assert row.exit_reason == "closed"


def test_short_stores_a_fee_rebate_and_funding_received(session: Session) -> None:
    _apply(session, _FIXTURE["short"])
    row = _rows(session)[0]
    assert row.direction is TradeDirection.SHORT
    assert _money(row.gross_pnl) == Decimal("0.20000000")
    assert _money(row.fees) == Decimal("-0.00300000")
    assert _money(row.funding) == Decimal("-0.00500000")
    assert _money(row.net_pnl) == Decimal("0.20800000")
    assert row.planned_stop_price == Decimal("110")
    assert "pnl-verified" in row.tags


def test_partial_close_keeps_one_row_per_slice_and_cancels_the_open_tracker(
    session: Session,
) -> None:
    first = _apply(session, _FIXTURE["partial_open"])
    assert first.closed == 1 and first.opens == 1  # type: ignore[attr-defined]
    open_row = next(row for row in _rows(session) if row.status is JournalTradeStatus.OPEN)
    assert open_row.external_ref == "blofin-demo-open:p-part:1792195200000"
    assert _money(open_row.size) == Decimal("0.01000000")
    closed = next(row for row in _rows(session) if row.status is JournalTradeStatus.CLOSED)
    assert closed.exit_reason == "partially_closed"
    assert _money(closed.net_pnl) == Decimal("0.04900000")

    second = _apply(session, _FIXTURE["partial_flat"])
    assert second.created == 1  # type: ignore[attr-defined]
    rows = _rows(session)
    assert len(rows) == 3
    by_ref = {row.external_ref: row for row in rows}
    assert by_ref["blofin-demo-open:p-part:1792195200000"].status is JournalTradeStatus.CANCELLED
    assert by_ref["blofin-demo-open:p-part:1792195200000"].exit_reason == "flat_on_venue"
    assert by_ref["blofin-demo-open:p-part:1792195200000"].net_pnl is None
    later = by_ref["blofin-demo-history:h-part-2"]
    assert _money(later.gross_pnl) == Decimal("0.10000000")
    assert _money(later.funding) == Decimal("0.00200000")
    assert _money(later.net_pnl) == Decimal("0.09600000")
    again = _apply(session, _FIXTURE["partial_flat"])
    assert again.created == 0  # type: ignore[attr-defined]
    assert len(_rows(session)) == 3


def test_rerun_is_idempotent_and_keeps_strategy_grade_and_notes(session: Session) -> None:
    _apply(session, _FIXTURE["long"])
    row = _rows(session)[0]
    original = row.id
    service = JournalTradeService(session, AuditService(session))
    service.annotate(
        original,
        JournalTradeAnnotation(strategy_label="Breakout", grade="A-", notes="Held the plan"),
        organization_id=ORG,
    )
    session.commit()
    again = _apply(session, _FIXTURE["long"])
    assert again.created == 0 and again.updated == 1  # type: ignore[attr-defined]
    rows = _rows(session)
    assert len(rows) == 1
    assert rows[0].id == original
    assert rows[0].strategy_label == "Breakout"
    assert rows[0].notes == "Held the plan"
    assert rows[0].grade == "A-"
    assert _money(rows[0].net_pnl) == Decimal("0.18600000")
    assert func_count(session) == len(rows) + 1


def func_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(JournalTrade)) or 0)


def test_rerun_does_not_rewrite_a_row_whose_history_fell_out_of_the_window(
    session: Session,
) -> None:
    _apply(session, _FIXTURE["long"])
    row = _rows(session)[0]
    row.tags = [*row.tags, "blofin-history:older-than-window"]
    row.net_pnl = Decimal("9")
    session.commit()
    report = _apply(session, _FIXTURE["long"])
    assert report.updated == 0 and report.created == 0  # type: ignore[attr-defined]
    kept = _rows(session)[0]
    assert kept.net_pnl == Decimal("9")
    assert len(_rows(session)) == 1


def test_reconciles_the_stuck_open_manual_demo_row_in_place(session: Session) -> None:
    session.add(
        JournalTrade(
            id=STUCK,
            organization_id=ORG,
            user_id=USER,
            source=JournalTradeSource.MANUAL_DEMO_TEST,
            status=JournalTradeStatus.OPEN,
            symbol="BTCUSDT",
            exchange="BLOFIN_DEMO",
            timeframe="manual",
            direction=TradeDirection.LONG,
            entry_price=Decimal("100"),
            size=Decimal("0.02"),
            result=TradeResult.OPEN,
        )
    )
    session.commit()
    report = _apply(session, _FIXTURE["long"])
    assert report.reconciled == 1 and report.created == 0  # type: ignore[attr-defined]
    rows = _rows(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == STUCK
    assert row.source is JournalTradeSource.MANUAL_DEMO_TEST
    assert row.status is JournalTradeStatus.CLOSED
    assert row.external_ref == "blofin-demo-history:h-long"
    assert _money(row.net_pnl) == Decimal("0.18600000")
    service = JournalTradeService(session, AuditService(session))
    tagged = service.annotate(
        STUCK,
        JournalTradeAnnotation(
            strategy_label="Manual catch-up",
            grade="B+",
            notes="Closed on venue",
        ),
        organization_id=ORG,
    )
    assert tagged.strategy_label == "Manual catch-up"
    assert tagged.grade == "B+"
    assert tagged.source is JournalTradeSource.MANUAL_DEMO_TEST


def test_ambiguous_manual_rows_are_not_duplicated(session: Session) -> None:
    for suffix in ("a", "b"):
        session.add(
            JournalTrade(
                organization_id=ORG,
                user_id=USER,
                source=JournalTradeSource.MANUAL,
                status=JournalTradeStatus.OPEN,
                symbol="BTC-USDT",
                exchange=None,
                timeframe="manual",
                direction=TradeDirection.LONG,
                entry_price=Decimal("100"),
                size=Decimal("0.02"),
                result=TradeResult.OPEN,
                notes=suffix,
            )
        )
    session.commit()
    report = _apply(session, _FIXTURE["long"])
    assert report.created == 0 and report.reconciled == 0  # type: ignore[attr-defined]
    rows = _rows(session)
    assert len(rows) == 2
    assert all(row.status is JournalTradeStatus.OPEN for row in rows)
    assert all(row.external_ref is None for row in rows)
    cycle = session.scalar(
        select(JournalTrade).where(JournalTrade.external_ref == "blofin-demo-mirror-cycle")
    )
    assert cycle is not None and cycle.tags == ["mirror-cycle:incomplete"]
    tenant = TenantContext(USER, ORG, "mirror@test.example", MembershipRole.TRADER)
    held = DashboardDemoAccount(status="ok", message="fixture")
    publish_mirror_performance(held, session=session, tenant=tenant, settings=_settings())
    assert held.performance.manual_mirror_status == "withheld"
    assert held.performance.win_rate is None
    assert held.performance.realized_pnl is None


def test_flag_off_does_not_read_or_write(session: Session) -> None:
    client = _Client(_paths(_FIXTURE["long"]))
    report = BloFinDemoManualMirror(client, now=NOW).apply(
        session,
        organization_id=ORG,
        user_id=USER,
        settings=_settings(blofin_demo_manual_mirror_enabled=False),
    )
    session.commit()
    assert report.skipped_flag is True
    assert client.calls == []
    assert _rows(session) == []


def test_real_trading_and_non_paper_modes_are_refused(session: Session) -> None:
    client = _Client(_paths(_FIXTURE["long"]))
    mirror = BloFinDemoManualMirror(client, now=NOW)
    forced = Settings.model_construct(
        enable_real_trading=True,
        execution_mode=ExecutionMode.PAPER,
        blofin_demo_manual_mirror_enabled=True,
    )
    with pytest.raises(ExchangeDemoInactiveError):
        mirror.apply(
            session,
            organization_id=ORG,
            user_id=USER,
            settings=forced,
        )
    with pytest.raises(ExchangeDemoInactiveError):
        mirror.apply(
            session,
            organization_id=ORG,
            user_id=USER,
            settings=_settings(execution_mode="read_only"),
        )
    assert client.calls == []


def test_incomplete_history_cursor_writes_nothing(session: Session) -> None:
    bundle = _paths(_FIXTURE["long"])
    bundle["/api/v1/account/positions-history"] = [{"historyId": ""}]
    client = _Client(bundle)
    with pytest.raises(MirrorReadError):
        BloFinDemoManualMirror(client, now=NOW).apply(
            session,
            organization_id=ORG,
            user_id=USER,
            settings=_settings(),
        )
    session.commit()
    assert _rows(session) == []


def test_eth_doc_price_matches_and_bch_sell_example_does_not() -> None:
    eth = _drafts(_FIXTURE["eth_doc"]["history"]).closed[0]  # type: ignore[attr-defined]
    assert eth.price_matches is True
    assert eth.verified is False
    assert eth.gross_pnl is None
    bch = _drafts(_FIXTURE["bch_doc"]["history"]).closed[0]  # type: ignore[attr-defined]
    assert bch.price_matches is False
    assert bch.verified is False
    assert bch.direction is TradeDirection.SHORT


def test_annotation_rejects_other_sources_and_a_bad_grade(session: Session) -> None:
    _apply(session, _FIXTURE["long"])
    mirrored = _rows(session)[0]
    paper = JournalTrade(
        organization_id=ORG,
        user_id=USER,
        source=JournalTradeSource.PAPER_EXECUTION,
        status=JournalTradeStatus.CLOSED,
        symbol="BTCUSDT",
        timeframe="manual",
        direction=TradeDirection.LONG,
    )
    session.add(paper)
    session.commit()
    service = JournalTradeService(session, AuditService(session))
    with pytest.raises(ValidationAppError) as blocked:
        service.annotate(paper.id, JournalTradeAnnotation(grade="A"), organization_id=ORG)
    assert blocked.value.details["reason"] == "annotation_source"
    with pytest.raises(ValidationAppError) as invalid:
        service.annotate(mirrored.id, JournalTradeAnnotation(grade="Z"), organization_id=ORG)
    assert invalid.value.details["reason"] == "invalid_grade"
    cleared = service.annotate(
        mirrored.id,
        JournalTradeAnnotation(strategy_label="", grade="", notes=""),
        organization_id=ORG,
    )
    assert cleared.strategy_label is None
    assert cleared.grade is None
    assert cleared.notes is None


def test_annotation_allows_a_reconciled_manual_row(session: Session) -> None:
    reconciled = JournalTrade(
        organization_id=ORG,
        user_id=USER,
        source=JournalTradeSource.MANUAL,
        status=JournalTradeStatus.CLOSED,
        symbol="BTCUSDT",
        timeframe="manual",
        direction=TradeDirection.LONG,
        external_ref="blofin-demo-history:h-reconciled",
    )
    plain = JournalTrade(
        organization_id=ORG,
        user_id=USER,
        source=JournalTradeSource.MANUAL,
        status=JournalTradeStatus.OPEN,
        symbol="ETHUSDT",
        timeframe="manual",
        direction=TradeDirection.LONG,
    )
    session.add_all([reconciled, plain])
    session.commit()
    service = JournalTradeService(session, AuditService(session))
    tagged = service.annotate(
        reconciled.id,
        JournalTradeAnnotation(strategy_label="Breakout", grade="B+"),
        organization_id=ORG,
    )
    assert tagged.strategy_label == "Breakout"
    assert tagged.grade == "B+"
    with pytest.raises(ValidationAppError) as blocked:
        service.annotate(plain.id, JournalTradeAnnotation(grade="A"), organization_id=ORG)
    assert blocked.value.details["reason"] == "annotation_source"


def test_dashboard_publishes_only_when_every_closed_mirror_row_is_verified(
    session: Session,
) -> None:
    tenant = TenantContext(USER, ORG, "mirror@test.example", MembershipRole.TRADER)
    off = DashboardDemoAccount(status="ok", message="fixture")
    publish_mirror_performance(
        off,
        session=session,
        tenant=tenant,
        settings=_settings(blofin_demo_manual_mirror_enabled=False),
    )
    assert off.performance.manual_mirror_status == "disabled"
    assert "fillPnl" in off.performance.manual_mirror_reason
    assert off.performance.win_rate is None

    empty = DashboardDemoAccount(status="ok", message="fixture")
    publish_mirror_performance(empty, session=session, tenant=tenant, settings=_settings())
    assert empty.performance.manual_mirror_status == "ready"
    assert empty.performance.trade_count == 0
    assert empty.performance.win_rate is None
    assert empty.performance.realized_pnl == Decimal("0")

    _apply(session, _FIXTURE["long"])
    ready = DashboardDemoAccount(status="ok", message="fixture")
    publish_mirror_performance(ready, session=session, tenant=tenant, settings=_settings())
    assert ready.performance.manual_mirror_status == "ready"
    assert ready.performance.pnl_method == METHOD
    assert ready.performance.trade_count == 1
    assert ready.performance.wins == 1
    assert ready.performance.losses == 0
    assert ready.performance.win_rate == Decimal("1.0000")
    assert ready.performance.realized_pnl == Decimal("0.18600000")
    assert ready.performance.average_r == Decimal("0.9300")

    session.add(
        JournalTrade(
            organization_id=ORG,
            user_id=USER,
            source=JournalTradeSource.BLOFIN_DEMO_MANUAL,
            status=JournalTradeStatus.CLOSED,
            symbol="ETHUSDT",
            timeframe="manual",
            direction=TradeDirection.LONG,
            result=TradeResult.OPEN,
            tags=["pnl-unverified"],
            external_ref="blofin-demo-history:unverified",
        )
    )
    session.commit()
    held = DashboardDemoAccount(status="ok", message="fixture")
    publish_mirror_performance(held, session=session, tenant=tenant, settings=_settings())
    assert held.performance.manual_mirror_status == "withheld"
    assert held.performance.win_rate is None
    assert held.performance.realized_pnl is None
    assert held.performance.trade_count is None
    assert "fillPnl" in held.performance.manual_mirror_reason


def test_create_and_generic_update_cannot_change_mirror_economics(session: Session) -> None:
    service = JournalTradeService(session, AuditService(session))
    with pytest.raises(ValidationAppError) as created:
        service.create(
            JournalTradeCreate(
                source=JournalTradeSource.BLOFIN_DEMO_MANUAL,
                symbol="BTCUSDT",
                timeframe=Timeframe.M15,
                direction=TradeDirection.LONG,
                tags=["pnl-verified"],
                net_pnl=Decimal("1"),
                result=TradeResult.WIN,
            ),
            organization_id=ORG,
            user_id=USER,
        )
    assert created.value.details["reason"] == "mirror_source_immutable"
    _apply(session, _FIXTURE["long"])
    mirrored = _rows(session)[0]
    with pytest.raises(ValidationAppError) as edited:
        service.update(
            mirrored.id,
            JournalTradeUpdate(net_pnl=Decimal("5"), tags=["pnl-verified", "grade:A"]),
            organization_id=ORG,
        )
    assert edited.value.details["reason"] == "mirror_venue_immutable"
    tagged = service.annotate(
        mirrored.id,
        JournalTradeAnnotation(strategy_label="Breakout"),
        organization_id=ORG,
    )
    assert tagged.strategy_label == "Breakout"
    assert tagged.net_pnl == Decimal("0.18600000")


def test_extra_history_tag_cannot_freeze_the_next_recompute(session: Session) -> None:
    service = JournalTradeService(session, AuditService(session))
    _apply(session, _FIXTURE["long"])
    mirrored = _rows(session)[0]
    extra = [*(mirrored.tags or []), "blofin-history:not-in-the-fetch"]
    with pytest.raises(ValidationAppError) as edited:
        service.update(mirrored.id, JournalTradeUpdate(tags=extra), organization_id=ORG)
    assert edited.value.details["reason"] == "mirror_venue_immutable"
    session.refresh(mirrored)
    assert "blofin-history:not-in-the-fetch" not in (mirrored.tags or [])
    mirrored.net_pnl = Decimal("9")
    session.commit()
    _apply(session, _FIXTURE["long"])
    session.refresh(mirrored)
    assert mirrored.net_pnl == Decimal("0.18600000")


def test_publish_ignores_a_hand_written_verified_net(session: Session) -> None:
    session.add(
        JournalTrade(
            organization_id=ORG,
            user_id=USER,
            source=JournalTradeSource.BLOFIN_DEMO_MANUAL,
            status=JournalTradeStatus.CLOSED,
            symbol="BTCUSDT",
            timeframe="manual",
            direction=TradeDirection.LONG,
            external_ref="blofin-demo-history:forged",
            entry_price=Decimal("1"),
            exit_price=Decimal("2"),
            size=Decimal("1"),
            fees=Decimal("0"),
            funding=Decimal("0"),
            gross_pnl=Decimal("999"),
            net_pnl=Decimal("999"),
            result=TradeResult.WIN,
            tags=["pnl-verified"],
        )
    )
    session.commit()
    tenant = TenantContext(USER, ORG, "mirror@test.example", MembershipRole.TRADER)
    held = DashboardDemoAccount(status="ok", message="fixture")
    publish_mirror_performance(held, session=session, tenant=tenant, settings=_settings())
    assert held.performance.manual_mirror_status == "withheld"
    assert held.performance.realized_pnl is None
    assert held.performance.win_rate is None


def test_failed_cycle_withholds_even_when_older_rows_match(session: Session) -> None:
    _apply(session, _FIXTURE["long"])
    record_mirror_cycle(session, organization_id=ORG, user_id=USER, outcome="failed")
    session.commit()
    tenant = TenantContext(USER, ORG, "mirror@test.example", MembershipRole.TRADER)
    held = DashboardDemoAccount(status="ok", message="fixture")
    publish_mirror_performance(held, session=session, tenant=tenant, settings=_settings())
    assert held.performance.manual_mirror_status == "withheld"
    assert held.performance.trade_count is None
    assert "page cap" in held.performance.manual_mirror_reason


def test_capped_fills_keep_positions_history_and_withhold(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.blofin_demo_manual_mirror._MAX_PAGES", 1)
    bundle = _paths(_FIXTURE["long"])
    bundle["/api/v1/trade/fills-history"] = [
        {
            "tradeId": f"t{index}",
            "instId": "BTC-USDT",
            "ts": "1792195205000",
            "orderId": "o-long",
            "fillPnl": "999",
        }
        for index in range(100)
    ]
    client = _Client(bundle)
    BloFinDemoManualMirror(client, now=NOW).apply(
        session,
        organization_id=ORG,
        user_id=USER,
        settings=_settings(),
    )
    session.commit()
    row = _rows(session)[0]
    assert row.external_ref == "blofin-demo-history:h-long"
    assert row.net_pnl is None
    assert "pnl-unverified" in (row.tags or [])
    cycle = session.scalar(
        select(JournalTrade).where(JournalTrade.external_ref == "blofin-demo-mirror-cycle")
    )
    assert cycle is not None
    assert cycle.tags == ["mirror-cycle:page-cap"]
    tenant = TenantContext(USER, ORG, "mirror@test.example", MembershipRole.TRADER)
    held = DashboardDemoAccount(status="ok", message="fixture")
    publish_mirror_performance(held, session=session, tenant=tenant, settings=_settings())
    assert held.performance.manual_mirror_status == "withheld"
    assert held.performance.realized_pnl is None


def test_cycle_marker_cannot_be_rewritten_or_deleted(session: Session) -> None:
    _apply(session, _FIXTURE["long"])
    service = JournalTradeService(session, AuditService(session))
    cycle = session.scalar(
        select(JournalTrade).where(JournalTrade.external_ref == "blofin-demo-mirror-cycle")
    )
    assert cycle is not None
    with pytest.raises(ValidationAppError) as rewritten:
        service.update(
            cycle.id,
            JournalTradeUpdate(tags=["mirror-cycle:failed"]),
            organization_id=ORG,
        )
    assert rewritten.value.details["reason"] == "mirror_cycle_immutable"
    with pytest.raises(ValidationAppError) as deleted:
        service.delete(cycle.id, organization_id=ORG)
    assert deleted.value.details["reason"] == "mirror_cycle_immutable"
    session.refresh(cycle)
    assert cycle.tags == ["mirror-cycle:ok"]
    cycle.tags = []
    session.commit()
    tenant = TenantContext(USER, ORG, "mirror@test.example", MembershipRole.TRADER)
    held = DashboardDemoAccount(status="ok", message="fixture")
    publish_mirror_performance(held, session=session, tenant=tenant, settings=_settings())
    assert held.performance.manual_mirror_status == "withheld"
    assert held.performance.realized_pnl is None


def test_equal_close_times_leave_funding_unverified() -> None:
    base = dict(_FIXTURE["long"]["history"][0])
    second = dict(base)
    second["historyId"] = "h-same-close"
    second["closePositions"] = "1"
    second["maxPositions"] = "2"
    second["realizedPnl"] = "0.1"
    built = _drafts([base, second], funding=_FIXTURE["long"]["funding"])
    closed = built.closed  # type: ignore[attr-defined]
    assert len(closed) == 2
    assert all(draft.verified is False for draft in closed)
    assert all(draft.funding is None for draft in closed)


def test_touching_positions_do_not_verify_a_shared_funding_bill() -> None:
    left = dict(_FIXTURE["long"]["history"][0])
    left["positionId"] = "p-left"
    left["historyId"] = "h-left"
    left["updateTime"] = "1792224000000"
    right = dict(left)
    right["positionId"] = "p-right"
    right["historyId"] = "h-right"
    right["createTime"] = "1792224000000"
    right["updateTime"] = "1792238400000"
    funding = [
        {
            "billId": "b-touch",
            "instId": "BTC-USDT",
            "fundingFee": "-0.004",
            "fundingTime": "1792224000000",
        }
    ]
    built = _drafts([left, right], funding=funding)
    closed = built.closed  # type: ignore[attr-defined]
    assert len(closed) == 2
    assert {draft.position_id for draft in closed} == {"p-left", "p-right"}
    assert all(draft.verified is False for draft in closed)
    assert all(draft.funding is None for draft in closed)


def test_unparsed_history_and_funding_withhold_and_a_duplicate_does_not(
    session: Session,
) -> None:
    broken = dict(_FIXTURE["long"])
    broken["history"] = [{"historyId": "", "side": "buy"}, *_FIXTURE["long"]["history"]]
    _apply(session, broken)
    row = _rows(session)[0]
    assert row.net_pnl is None
    cycle = session.scalar(
        select(JournalTrade).where(JournalTrade.external_ref == "blofin-demo-mirror-cycle")
    )
    assert cycle is not None
    assert cycle.tags == ["mirror-cycle:incomplete"]
    tenant = TenantContext(USER, ORG, "mirror@test.example", MembershipRole.TRADER)
    held = DashboardDemoAccount(status="ok", message="fixture")
    publish_mirror_performance(held, session=session, tenant=tenant, settings=_settings())
    assert held.performance.manual_mirror_status == "withheld"

    session.delete(cycle)
    for trade in _rows(session):
        session.delete(trade)
    session.commit()
    duplicate = dict(_FIXTURE["long"])
    duplicate["history"] = [*_FIXTURE["long"]["history"], dict(_FIXTURE["long"]["history"][0])]
    _apply(session, duplicate)
    verified = _rows(session)[0]
    assert verified.net_pnl is not None
    cycle = session.scalar(
        select(JournalTrade).where(JournalTrade.external_ref == "blofin-demo-mirror-cycle")
    )
    assert cycle is not None and cycle.tags == ["mirror-cycle:ok"]

    bad_fee = dict(_FIXTURE["long"])
    bad_fee["funding"] = [
        {
            "billId": "b-bad",
            "instId": "BTC-USDT",
            "fundingFee": 0.004,
            "fundingTime": "1792224000000",
        }
    ]
    _apply(session, bad_fee)
    assert _rows(session)[0].net_pnl is None
    session.refresh(cycle)
    assert cycle.tags == ["mirror-cycle:incomplete"]


def test_closed_mirror_row_cannot_be_deleted_and_a_bad_position_is_not_flat(
    session: Session,
) -> None:
    _apply(session, _FIXTURE["partial_open"])
    service = JournalTradeService(session, AuditService(session))
    closed = next(row for row in _rows(session) if row.status is JournalTradeStatus.CLOSED)
    tracker = next(row for row in _rows(session) if row.status is JournalTradeStatus.OPEN)
    with pytest.raises(ValidationAppError) as blocked:
        service.delete(closed.id, organization_id=ORG)
    assert blocked.value.details["reason"] == "mirror_row_immutable"
    session.refresh(closed)
    assert closed.status is JournalTradeStatus.CLOSED
    bundle = _paths(_FIXTURE["partial_open"])
    bundle["positions"] = [{"positionId": 1}]
    BloFinDemoManualMirror(_Client(bundle), now=NOW).apply(
        session,
        organization_id=ORG,
        user_id=USER,
        settings=_settings(),
    )
    session.commit()
    session.refresh(tracker)
    assert tracker.status is JournalTradeStatus.OPEN
    assert tracker.exit_reason != "flat_on_venue"


def test_non_dict_position_keeps_the_open_tracker(session: Session) -> None:
    _apply(session, _FIXTURE["partial_open"])
    tracker = next(row for row in _rows(session) if row.status is JournalTradeStatus.OPEN)
    bundle = _paths(_FIXTURE["partial_open"])
    bundle["positions"] = ["not-a-position"]
    BloFinDemoManualMirror(_Client(bundle), now=NOW).apply(
        session,
        organization_id=ORG,
        user_id=USER,
        settings=_settings(),
    )
    session.commit()
    session.refresh(tracker)
    assert tracker.status is JournalTradeStatus.OPEN
    assert tracker.exit_reason != "flat_on_venue"
    cycle = session.scalar(
        select(JournalTrade).where(JournalTrade.external_ref == "blofin-demo-mirror-cycle")
    )
    assert cycle is not None and cycle.tags == ["mirror-cycle:incomplete"]
    bundle["positions"] = _FIXTURE["partial_open"]["positions"]
    bundle["/api/v1/market/instruments"] = ["not-an-instrument", *_FIXTURE["instruments"]]
    client = _Client(bundle)

    def request(method: str, path: str, **kwargs: object) -> list[object]:
        if path == "/api/v1/market/instruments":
            return bundle["/api/v1/market/instruments"]
        return _Client.request(client, method, path, **kwargs)

    client.request = request  # type: ignore[method-assign]
    BloFinDemoManualMirror(client, now=NOW).apply(
        session,
        organization_id=ORG,
        user_id=USER,
        settings=_settings(),
    )
    session.commit()
    session.refresh(cycle)
    assert cycle.tags == ["mirror-cycle:incomplete"]


def test_conflicting_instrument_metadata_withholds_the_cycle(session: Session) -> None:
    bundle = _paths(_FIXTURE["long"])
    bundle["/api/v1/market/instruments"] = [
        *_FIXTURE["instruments"],
        {"instId": "BTC-USDT", "contractValue": "1", "settleCurrency": "USDT"},
    ]
    BloFinDemoManualMirror(_Client(bundle), now=NOW).apply(
        session,
        organization_id=ORG,
        user_id=USER,
        settings=_settings(),
    )
    session.commit()
    assert _rows(session)[0].net_pnl is None
    cycle = session.scalar(
        select(JournalTrade).where(JournalTrade.external_ref == "blofin-demo-mirror-cycle")
    )
    assert cycle is not None and cycle.tags == ["mirror-cycle:incomplete"]


def test_strategy_group_uses_the_hand_label_only_for_mirrored_rows() -> None:
    strategy = uuid.uuid4()
    assert strategy_group_key(
        source=JournalTradeSource.BLOFIN_DEMO_MANUAL,
        strategy_id=strategy,
        strategy_label="ignored",
    ) == str(strategy)
    assert (
        strategy_group_key(
            source=JournalTradeSource.BLOFIN_DEMO_MANUAL,
            strategy_id=None,
            strategy_label=" Breakout ",
        )
        == "Breakout"
    )
    assert (
        strategy_group_key(
            source=JournalTradeSource.MANUAL,
            strategy_id=None,
            strategy_label="Breakout",
        )
        is None
    )


def test_readonly_client_allows_positions_history_get_and_refuses_writes() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    client = BloFinReadOnlyClient(
        base_url="https://demo-trading-openapi.blofin.com",
        api_key="fixture-key",
        api_secret="fixture-secret",
        api_passphrase="fixture-passphrase",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    assert (
        client.request(
            "GET",
            "/api/v1/account/positions-history",
            params={"begin": "1", "end": "2", "limit": "100"},
            signed=True,
        )
        == []
    )
    assert seen == ["GET /api/v1/account/positions-history"]
    with pytest.raises(ExchangeDemoInactiveError):
        client.request("POST", "/api/v1/trade/order", body={"instId": "BTC-USDT"})
    with pytest.raises(ExchangeDemoInactiveError):
        client.request("POST", "/api/v1/account/positions-history", params={"limit": "1"})
    assert seen == ["GET /api/v1/account/positions-history"]
