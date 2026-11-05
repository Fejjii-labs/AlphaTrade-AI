"""Account daily equity stop (AT-ADR-152).

One account-level loss rule arms the organization kill switch: equity more
than the threshold below the 00:00 UTC baseline. Unreadable equity and a
missing or stale baseline fail closed. A single loss and a losing streak
do not arm it, and the peak latch and soft pauses no longer refuse.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.db.models import AuditLog, KillSwitchState, Organization
from app.db.risk_policy import RiskPolicyBreakerState
from app.schemas.common import AuditEventType
from app.schemas.risk import KillSwitchMutationRequest
from app.services.audit_service import AuditService
from app.services.risk.kill_switch import KillSwitchService
from app.services.risk_policy.equity_stop import (
    ACCOUNT_DAILY_DRAWDOWN,
    StopEquityReading,
    enforce_account_equity_stop,
    evaluate_equity_stop,
)

DAY = date(2026, 11, 5)
NOON = datetime(2026, 11, 5, 12, tzinfo=UTC)
TEN = Decimal("10")
GRACE = timedelta(minutes=15)


def _settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_internal",
        provider_mode="mock",
        market_data_provider="mock",
        global_kill_switch_active=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="account-equity-stop-secret-32bytes",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
    )


@pytest.fixture
def world() -> tuple[Session, UUID, UUID, UUID]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    org, user, account = uuid4(), uuid4(), uuid4()
    session.add(Organization(id=org, name="Equity stop org"))
    session.commit()
    yield session, org, user, account
    session.close()
    engine.dispose()


def _row(
    session: Session,
    org: UUID,
    account: UUID,
    *,
    baseline: Decimal | None,
    day: date | None,
) -> RiskPolicyBreakerState:
    row = RiskPolicyBreakerState(
        organization_id=org,
        account_id=account,
        equity_peak=Decimal("10000"),
        drawdown_latched=False,
        consecutive_losses=0,
        day_start_equity=baseline,
        day_start_on=day,
        updated_at=NOON,
    )
    session.add(row)
    session.flush()
    return row


def _enforce(session: Session, org: UUID, user: UUID, account: UUID, equity: object, now=NOON):
    return enforce_account_equity_stop(
        session,
        organization_id=org,
        user_id=user,
        account_id=account,
        equity=equity,
        now=now,
        settings=_settings(),
    )


def _active(session: Session, org: UUID) -> bool:
    row = session.scalar(select(KillSwitchState).where(KillSwitchState.organization_id == org))
    return row is not None and bool(row.active)


def _trips(session: Session, org: UUID) -> list[AuditLog]:
    return list(
        session.scalars(
            select(AuditLog).where(
                AuditLog.organization_id == org,
                AuditLog.action == AuditEventType.KILL_SWITCH_TRIGGERED,
                AuditLog.resource_type == "account_equity_stop",
            )
        ).all()
    )


def _verdict(baseline: object, equity: object, *, day: date | None = DAY, now=NOON):
    return evaluate_equity_stop(
        threshold_percent=TEN,
        grace=GRACE,
        baseline=baseline,
        baseline_day=day,
        equity=equity,
        now=now,
    )


# 1. Just under the threshold does not trip.
def test_just_under_ten_percent_does_not_trip(world) -> None:
    session, org, user, account = world
    assert _verdict(Decimal("10000"), Decimal("9000")).trip is False  # exactly 10%
    assert _verdict(Decimal("10000"), Decimal("9000.01")).trip is False
    _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    assert _enforce(session, org, user, account, Decimal("9000.01")) is False
    assert _active(session, org) is False
    assert _trips(session, org) == []


# 2. Just over the threshold trips, arms, and audits ACCOUNT_DAILY_DRAWDOWN.
def test_just_over_ten_percent_trips_arms_and_audits(world) -> None:
    session, org, user, account = world
    verdict = _verdict(Decimal("10000"), Decimal("8999.99"))
    assert verdict.trip is True and verdict.cause == "breach"
    _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    assert _enforce(session, org, user, account, Decimal("8999.99")) is True
    assert _active(session, org) is True
    [audit] = _trips(session, org)
    metadata = audit.redacted_metadata
    assert metadata["reason"] == ACCOUNT_DAILY_DRAWDOWN
    assert metadata["cause"] == "breach"
    assert metadata["baseline"] == "10000.00"
    assert metadata["equity"] == "8999.99"
    assert metadata["drawdown_percent"] == "10.0001"
    assert metadata["threshold_percent"] == "10.0000"
    switch = session.scalar(select(KillSwitchState).where(KillSwitchState.organization_id == org))
    assert switch is not None and switch.reason.startswith(ACCOUNT_DAILY_DRAWDOWN)
    assert switch.activated_by is None  # armed as the system

    # A later scan the same day does not arm again, so a human reset sticks.
    KillSwitchService(session, AuditService(session), _settings()).deactivate(
        organization_id=org,
        actor_user_id=user,
        payload=KillSwitchMutationRequest(confirm=True, reason="owner reviewed"),
    )
    assert _enforce(session, org, user, account, Decimal("8000")) is False
    assert _active(session, org) is False
    assert len(_trips(session, org)) == 1


def test_threshold_is_config_driven(world) -> None:
    session, org, user, account = world
    _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    settings = _settings().model_copy(
        update={"account_equity_stop_threshold_percent": Decimal("5")}
    )
    assert enforce_account_equity_stop(
        session,
        organization_id=org,
        user_id=user,
        account_id=account,
        equity=Decimal("9400"),
        now=NOON,
        settings=settings,
    )


# 3. UTC rollover snapshots a new baseline and does not auto-clear the stop.
def test_utc_rollover_snapshots_a_new_baseline_and_keeps_the_switch_on(world) -> None:
    session, org, user, account = world
    row = _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    assert _enforce(session, org, user, account, Decimal("8500")) is True
    assert _active(session, org) is True

    after_midnight = datetime(2026, 11, 6, 0, 5, tzinfo=UTC)
    assert _enforce(session, org, user, account, Decimal("8600"), now=after_midnight) is False
    assert row.day_start_on == date(2026, 11, 6)
    assert row.day_start_equity == Decimal("8600")
    assert _active(session, org) is True  # no auto-clear on a new day
    assert len(_trips(session, org)) == 1

    # The new day is measured from its own baseline.
    later = after_midnight + timedelta(hours=6)
    assert _enforce(session, org, user, account, Decimal("7800"), now=later) is False
    assert _enforce(session, org, user, account, Decimal("7739"), now=later) is True


# 4. Unreadable equity fails closed.
@pytest.mark.parametrize("equity", [None, Decimal("NaN"), Decimal("0"), Decimal("-5"), "9000"])
def test_unreadable_equity_fails_closed(world, equity: object) -> None:
    session, org, user, account = world
    assert _verdict(Decimal("10000"), equity).cause == "equity_unreadable"
    _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    assert _enforce(session, org, user, account, equity) is True
    assert _active(session, org) is True
    [audit] = _trips(session, org)
    assert audit.redacted_metadata["cause"] == "equity_unreadable"
    assert audit.redacted_metadata["equity"] == "unavailable"


# 5. Missing or stale baseline fails closed outside the rollover grace window.
@pytest.mark.parametrize(
    ("baseline", "day", "cause"),
    [
        (None, DAY, "baseline_missing"),
        (Decimal("10000"), DAY - timedelta(days=1), "baseline_stale"),
        (Decimal("0"), DAY, "baseline_missing"),
    ],
)
def test_missing_or_stale_baseline_fails_closed(
    world, baseline: Decimal | None, day: date | None, cause: str
) -> None:
    session, org, user, account = world
    row = _row(session, org, account, baseline=baseline, day=day)
    assert _enforce(session, org, user, account, Decimal("10000")) is True
    assert _active(session, org) is True
    [audit] = _trips(session, org)
    assert audit.redacted_metadata["cause"] == cause
    assert audit.redacted_metadata["reason"] == ACCOUNT_DAILY_DRAWDOWN
    # The stop guards the rest of the day from the current equity.
    assert row.day_start_on == DAY and row.day_start_equity == Decimal("10000")


def test_stale_baseline_inside_the_grace_window_is_snapshotted() -> None:
    just_after = datetime(2026, 11, 5, 0, 10, tzinfo=UTC)
    verdict = _verdict(
        Decimal("10000"), Decimal("9500"), day=DAY - timedelta(days=1), now=just_after
    )
    assert verdict.trip is False and verdict.new_baseline == Decimal("9500")
    late = datetime(2026, 11, 5, 0, 16, tzinfo=UTC)
    assert _verdict(Decimal("10000"), Decimal("9500"), day=DAY - timedelta(days=1), now=late).trip


# 6. A single, stale, or backfill loss does not arm the kill switch.
def test_single_loss_alerts_do_not_arm_the_kill_switch(world, monkeypatch) -> None:
    from app.schemas.execution_protocol import ClosePaperPlanRequest
    from app.services import paper_exit_protection

    session, org, user, account = world
    request = ClosePaperPlanRequest(
        organization_id=org,
        user_id=user,
        account_id=account,
        revision_id=uuid4(),
        command_id=uuid4(),
        exit_price=Decimal("99"),
        fees=Decimal("0"),
        funding=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="stop_hit",
        occurred_at=NOON,
        idempotency_key="single-loss",
    )
    recorded: list[str] = []
    monkeypatch.setattr(
        paper_exit_protection,
        "record_exit_monitor_alert",
        lambda _session, alert, **_kw: recorded.append(alert.code) or True,
    )
    for alert in (
        paper_exit_protection.alert_backfill_loss,
        paper_exit_protection.alert_stale_live_loss,
    ):
        result = alert(session, request=request, net_pnl=Decimal("-500"), settings=_settings())
        assert result.fields["refusal"] == "none"
    assert recorded == ["backfill_loss", "stale_live_loss"]
    assert _active(session, org) is False
    assert not hasattr(paper_exit_protection, "protect_account_from_backfill_loss")
    assert not hasattr(paper_exit_protection, "protect_account_from_stale_live_loss")


def _figures(monkeypatch: pytest.MonkeyPatch, equity: dict[str, Decimal]) -> None:
    class _Daily:
        @property
        def account_equity(self) -> Decimal:
            return equity["equity"]

        realized_pnl = Decimal("0")
        unrealized_pnl = Decimal("0")

    class _Accounting:
        def __init__(self, _session: object, _settings: object, clock: object) -> None:
            return None

        def sync_from_portfolio(self, **_kwargs: object) -> _Daily:
            return _Daily()

    monkeypatch.setattr("app.services.risk.daily_risk_accounting.DailyRiskAccounting", _Accounting)
    # The stop reads its own UTC equity, not the risk-day figure above.
    monkeypatch.setattr(
        "app.services.risk_policy.equity_stop.read_stop_equity",
        lambda _session, **_kwargs: StopEquityReading(equity["equity"]),
    )


# 6b/7. A streak past the limit neither pauses, refuses, nor arms (Sofien 2026-11-05).
def test_loss_streak_no_longer_pauses_or_refuses_and_ten_percent_still_arms(
    world, monkeypatch
) -> None:
    from app.services.risk_policy.breaker_trip import persist_profile_breakers
    from app.services.risk_policy.profile import PolicyKind, sofien_aggressive_profile

    session, org, user, account = world
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    figures = {"equity": Decimal("10000")}
    _figures(monkeypatch, figures)
    kwargs: dict[str, object] = {
        "organization_id": org,
        "user_id": user,
        "account_id": account,
        "profile": profile,
    }
    # First sight seeds the baseline without tripping.
    assert persist_profile_breakers(session, now=NOON, settings=_settings(), **kwargs) is False
    for losses in range(1, profile.consecutive_loss_limit + 3):
        figures["equity"] -= Decimal("50")
        refused = persist_profile_breakers(
            session,
            now=NOON + timedelta(minutes=losses),
            consecutive_losses=losses,
            settings=_settings(),
            **kwargs,
        )
        assert refused is False
    row = session.scalar(select(RiskPolicyBreakerState))
    assert row is not None and row.consecutive_losses == profile.consecutive_loss_limit + 2
    assert row.consecutive_paused_until is None and row.daily_paused_until is None
    assert _active(session, org) is False
    assert _trips(session, org) == []
    # A daily equity loss past 10% is what arms it.
    figures["equity"] = Decimal("8999")
    assert persist_profile_breakers(
        session, now=NOON + timedelta(hours=1), settings=_settings(), **kwargs
    )
    assert _active(session, org) is True
    assert [a.redacted_metadata["cause"] for a in _trips(session, org)] == ["breach"]


def test_peak_drawdown_no_longer_latches_or_refuses(world, monkeypatch) -> None:
    """The peak latch is removed. Only the SoD daily rule halts trading (AT-ADR-152)."""

    from app.services.risk_policy.breaker_trip import persist_profile_breakers
    from app.services.risk_policy.profile import PolicyKind, sofien_aggressive_profile

    session, org, user, account = world
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    figures = {"equity": Decimal("10000")}
    _figures(monkeypatch, figures)
    kwargs: dict[str, object] = {
        "organization_id": org,
        "user_id": user,
        "account_id": account,
        "profile": profile,
    }
    assert persist_profile_breakers(session, now=NOON, settings=_settings(), **kwargs) is False
    figures["equity"] = Decimal("9500")
    assert (
        persist_profile_breakers(
            session, now=NOON + timedelta(hours=1), settings=_settings(), **kwargs
        )
        is False
    )
    next_day = datetime(2026, 11, 6, 0, 5, tzinfo=UTC)
    persist_profile_breakers(session, now=next_day, settings=_settings(), **kwargs)
    # 10.1% under the peak, 5.4% under today's baseline.
    figures["equity"] = Decimal("8990")
    refused = persist_profile_breakers(
        session, now=next_day + timedelta(hours=3), settings=_settings(), **kwargs
    )
    row = session.scalar(select(RiskPolicyBreakerState))
    assert refused is False and row is not None and row.drawdown_latched is False
    assert _active(session, org) is False
    assert _trips(session, org) == []


def test_breaker_figures_never_refuse_on_drawdown_daily_loss_or_streak() -> None:
    from app.services.risk_policy.breakers import (
        BreakerConfig,
        BreakerSnapshot,
        evaluate_breakers,
    )

    verdict = evaluate_breakers(
        BreakerConfig(
            daily_loss_limit_percent=Decimal("3"),
            drawdown_breaker_percent=Decimal("10"),
            consecutive_loss_limit=3,
        ),
        BreakerSnapshot(
            equity=Decimal("8000"),
            equity_peak=Decimal("10000"),
            reference_equity=Decimal("10000"),
            realized_pnl_today=Decimal("-2000"),
            unrealized_pnl_today=Decimal("0"),
            consecutive_losses=5,
            drawdown_latched=True,
            now=NOON,
        ),
    )
    # The figures are still reported, but nothing refuses or pauses.
    assert verdict.drawdown_active and verdict.daily_loss_active
    assert verdict.consecutive_loss_active
    assert verdict.refuse_new_trades is False and verdict.active is False
    assert verdict.paused_until is None and verdict.human_reset_required is False


def test_entry_gate_ignores_a_legacy_latch_and_pauses_and_the_next_persist_clears_them(
    world, monkeypatch
) -> None:
    from app.services.automated_paper_loop import _breaker_refusing
    from app.services.risk_policy.breaker_trip import persist_profile_breakers
    from app.services.risk_policy.profile import PolicyKind, sofien_aggressive_profile

    session, org, user, account = world
    _book(session, org, user, account)
    row = _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    row.drawdown_latched = True
    row.daily_paused_until = NOON + timedelta(hours=12)
    row.consecutive_paused_until = NOON + timedelta(hours=24)
    row.consecutive_losses = 9
    session.flush()
    assert _breaker_refusing(session, org, NOON) is False
    figures = {"equity": Decimal("9500")}
    _figures(monkeypatch, figures)
    refused = persist_profile_breakers(
        session,
        organization_id=org,
        user_id=user,
        account_id=account,
        profile=sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER),
        now=NOON,
        settings=_settings(),
    )
    assert refused is False
    assert row.drawdown_latched is False
    assert row.daily_paused_until is None and row.consecutive_paused_until is None
    assert row.consecutive_losses == 9  # the streak is still counted, as a figure


def test_worker_sweep_snapshots_at_rollover_and_fails_closed_on_unreadable_equity(
    world, monkeypatch
) -> None:
    from app.db.models import ExecutionAccount, User
    from app.services.risk_policy.equity_stop import sweep_account_equity_stops

    session, org, user, account = world
    session.add(User(id=user, email=f"{uuid4()}@example.com", hashed_password="fixture"))
    session.flush()
    session.add(ExecutionAccount(id=account, organization_id=org, user_id=user, name="paper"))
    row = _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    figures = {"equity": Decimal("9700")}
    _figures(monkeypatch, figures)
    rollover = datetime(2026, 11, 6, 0, 1, tzinfo=UTC)
    assert sweep_account_equity_stops(session, now=rollover, settings=_settings()) == 0
    assert (row.day_start_on, row.day_start_equity) == (date(2026, 11, 6), Decimal("9700"))

    def _broken(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("portfolio unreadable")

    monkeypatch.setattr("app.services.risk_policy.equity_stop.read_stop_equity", _broken)
    assert (
        sweep_account_equity_stops(session, now=rollover + timedelta(hours=1), settings=_settings())
        == 1
    )
    assert _active(session, org) is True
    assert [a.redacted_metadata["cause"] for a in _trips(session, org)] == ["equity_unreadable"]


def test_equity_stop_migration_is_the_single_head_after_demo_leverage() -> None:
    from tests.support.alembic_heads import alembic_script_directory, single_alembic_head

    script = alembic_script_directory()
    assert single_alembic_head(script) == "a26equitystop002"
    assert script.get_revision("a26equitystop002").down_revision == "a25equitystop001"
    assert script.get_revision("a25equitystop001").down_revision == "a24demoleverage001"


def test_a_close_dated_before_todays_baseline_does_not_trip_or_re_date_it(world) -> None:
    session, org, user, account = world
    today = date(2026, 11, 6)
    row = _row(session, org, account, baseline=Decimal("10000"), day=today)
    bar_before_midnight = datetime(2026, 11, 5, 23, 50, tzinfo=UTC)
    assert _enforce(session, org, user, account, Decimal("5000"), now=bar_before_midnight) is False
    assert (row.day_start_on, row.day_start_equity) == (today, Decimal("10000"))
    assert _active(session, org) is False


# --- Rework after Griller NO-GO on #436 -------------------------------------


def _book(
    session: Session,
    org: UUID,
    user: UUID,
    account: UUID,
    *,
    balance: Decimal = Decimal("10000"),
    timezone: str = "UTC",
) -> None:
    from app.db.models import ExecutionAccount, User, UserRiskSettings

    session.add(User(id=user, email=f"{uuid4()}@example.com", hashed_password="fixture"))
    session.flush()
    session.add(ExecutionAccount(id=account, organization_id=org, user_id=user, name="paper"))
    session.add(
        UserRiskSettings(
            organization_id=org,
            user_id=user,
            default_account_balance=balance,
            timezone=timezone,
        )
    )
    session.flush()


def _position(
    session: Session,
    org: UUID,
    user: UUID,
    *,
    realized: Decimal = Decimal("0"),
    closed_at: datetime | None = None,
    unrealized: Decimal = Decimal("0"),
    marked_at: datetime = NOON,
) -> None:
    from app.db.models import Position
    from app.schemas.common import PositionStatus, TradeDirection

    session.add(
        Position(
            organization_id=org,
            user_id=user,
            symbol="BTCUSDT",
            direction=TradeDirection.LONG,
            size=Decimal("1"),
            entry_price=Decimal("100"),
            leverage=Decimal("1"),
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            status=PositionStatus.CLOSED if closed_at is not None else PositionStatus.OPEN,
            opened_at=(closed_at or marked_at) - timedelta(hours=1),
            closed_at=closed_at,
            updated_at=marked_at,
        )
    )
    session.flush()


def _sweep(session: Session, now: datetime) -> int:
    from app.services.risk_policy.equity_stop import sweep_account_equity_stops

    return sweep_account_equity_stops(session, now=now, settings=_settings())


def _read(session: Session, org: UUID, user: UUID, now: datetime = NOON) -> StopEquityReading:
    from app.services.risk_policy.equity_stop import read_stop_equity

    return read_stop_equity(
        session, organization_id=org, user_id=user, now=now, settings=_settings()
    )


# Blocker 1a: a non-UTC risk day must not drop a UTC-day loss mid-day.
def test_non_utc_risk_day_does_not_mask_a_utc_day_loss(world) -> None:
    session, org, user, account = world
    # Tokyo midnight is 15:00 UTC; the risk day resets inside the UTC day.
    _book(session, org, user, account, timezone="Asia/Tokyo")
    _row(session, org, account, baseline=None, day=None)
    assert _sweep(session, datetime(2026, 11, 5, 0, 5, tzinfo=UTC)) == 0  # baseline 10000
    _position(
        session,
        org,
        user,
        realized=Decimal("-1100"),
        closed_at=datetime(2026, 11, 5, 2, tzinfo=UTC),
    )
    after_tokyo_midnight = datetime(2026, 11, 5, 16, tzinfo=UTC)
    assert _read(session, org, user, after_tokyo_midnight).equity == Decimal("8900")
    assert _sweep(session, after_tokyo_midnight) == 1
    assert [a.redacted_metadata["cause"] for a in _trips(session, org)] == ["breach"]


# Blocker 1b: prior days' realized PnL is in equity, so the stop does not loosen.
def test_prior_day_losses_lower_the_baseline_so_ten_percent_stays_ten_percent(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account)
    _position(
        session,
        org,
        user,
        realized=Decimal("-5000"),
        closed_at=datetime(2026, 11, 4, 12, tzinfo=UTC),
    )
    _row(session, org, account, baseline=None, day=None)
    assert _sweep(session, datetime(2026, 11, 5, 0, 5, tzinfo=UTC)) == 0
    row = session.scalar(select(RiskPolicyBreakerState))
    assert row is not None and row.day_start_equity == Decimal("5000")
    # 600 is 6% of the configured balance but 12% of real equity.
    _position(session, org, user, realized=Decimal("-600"), closed_at=NOON)
    assert _sweep(session, NOON + timedelta(minutes=1)) == 1
    assert _active(session, org) is True


def test_stop_equity_is_balance_plus_all_realized_plus_fresh_unrealized(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account, balance=Decimal("20000"))
    _position(
        session, org, user, realized=Decimal("250"), closed_at=datetime(2026, 1, 2, tzinfo=UTC)
    )
    _position(session, org, user, realized=Decimal("-50"), closed_at=NOON - timedelta(hours=2))
    _position(session, org, user, unrealized=Decimal("-75"), marked_at=NOON - timedelta(minutes=5))
    assert _read(session, org, user) == StopEquityReading(
        Decimal("20125"), balance=Decimal("20000")
    )


# Blocker 2: a stale unrealized mark fails closed with its own cause.
def test_stale_unrealized_mark_fails_closed(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account)
    _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    _position(session, org, user, unrealized=Decimal("5"), marked_at=NOON - timedelta(minutes=16))
    assert _read(session, org, user) == StopEquityReading(None, "equity_stale")
    assert _sweep(session, NOON) == 1
    assert _active(session, org) is True
    [audit] = _trips(session, org)
    assert audit.redacted_metadata["reason"] == ACCOUNT_DAILY_DRAWDOWN
    assert audit.redacted_metadata["cause"] == "equity_stale"
    assert audit.redacted_metadata["equity"] == "unavailable"


def test_mark_freshness_bound_is_config_driven(world) -> None:
    from app.services.risk_policy.equity_stop import read_stop_equity

    session, org, user, account = world
    _book(session, org, user, account)
    _position(session, org, user, unrealized=Decimal("5"), marked_at=NOON - timedelta(minutes=16))
    wide = _settings().model_copy(update={"account_equity_stop_mark_max_age_seconds": 1800})
    reading = read_stop_equity(session, organization_id=org, user_id=user, now=NOON, settings=wide)
    assert reading == StopEquityReading(Decimal("10005"), balance=Decimal("10000"))


# Open point 1: a never-snapshotted row is first sight, even past the grace window.
def test_never_snapshotted_row_seeds_without_tripping_on_first_deploy(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account)
    row = _row(session, org, account, baseline=None, day=None)
    assert _sweep(session, NOON) == 0
    assert (row.day_start_on, row.day_start_equity) == (DAY, Decimal("10000"))
    assert _active(session, org) is False
    assert _trips(session, org) == []


# Open point 2: the entry gate refuses on an overdue baseline without the sweep.
def test_entry_gate_refuses_when_baseline_is_overdue_past_grace(world) -> None:
    from app.services.automated_paper_loop import _breaker_refusing
    from app.services.risk_policy.equity_stop import entry_baseline_overdue

    session, org, user, account = world
    _book(session, org, user, account)  # a real account; orphans are ignored (C4)
    row = _row(session, org, account, baseline=Decimal("10000"), day=DAY - timedelta(days=1))
    in_grace = datetime(2026, 11, 5, 0, 10, tzinfo=UTC)

    def overdue(now: datetime) -> bool:
        return entry_baseline_overdue(session, organization_id=org, now=now, settings=_settings())

    assert overdue(in_grace) is False
    assert overdue(NOON) is True
    assert _breaker_refusing(session, org, NOON) is True
    row.day_start_on = DAY
    assert overdue(NOON) is False
    assert _breaker_refusing(session, org, NOON) is False
    row.day_start_on = None  # never snapshotted: first sight, not overdue
    assert overdue(NOON) is False
    assert _active(session, org) is False  # the gate refuses; it does not arm


def test_sweep_heartbeat_alert_is_missing_stale_or_quiet() -> None:
    from types import SimpleNamespace

    from app.services.risk_policy.equity_stop import (
        SWEEP_MISSING_CODE,
        SWEEP_STALE_CODE,
        equity_stop_sweep_alert,
    )
    from app.services.safety_alerts import TELEGRAM_SAFETY_MANDATORY_CODES

    def alert(row: object | None):
        return equity_stop_sweep_alert(row, now=NOON, stale_after_seconds=900)

    assert alert(SimpleNamespace(heartbeat_at=NOON - timedelta(minutes=10))) is None
    stale = alert(SimpleNamespace(heartbeat_at=NOON - timedelta(minutes=16)))
    assert stale is not None and stale.code == SWEEP_STALE_CODE
    assert stale.fields["threshold_seconds"] == "900"
    missing = alert(None)
    assert missing is not None and missing.code == SWEEP_MISSING_CODE
    # Mandatory codes cannot be suppressed by the Telegram allowlist.
    assert {SWEEP_STALE_CODE, SWEEP_MISSING_CODE} <= TELEGRAM_SAFETY_MANDATORY_CODES


def test_worker_sweep_publishes_heartbeat_and_alerts_on_failure(monkeypatch) -> None:
    from app.db.runtime_status import ControlledRuntimeStatusRow
    from app.services import safety_alerts
    from app.services.risk_policy import equity_stop
    from app.workers.paper_exit_monitor import _sweep_account_equity_stops

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    _sweep_account_equity_stops(factory, _settings(), NOON)
    with factory() as session:
        beat = session.get(ControlledRuntimeStatusRow, equity_stop.EQUITY_STOP_SWEEP_COMPONENT)
        assert beat is not None and beat.activation_state == "swept"

    sent: list[str] = []
    monkeypatch.setattr(
        safety_alerts,
        "deliver_observed_alert_outside_transaction",
        lambda _settings, alert: sent.append(alert.code) or True,
    )

    def _fail(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("sweep broke")

    monkeypatch.setattr(equity_stop, "sweep_account_equity_stops", _fail)
    _sweep_account_equity_stops(factory, _settings(), NOON + timedelta(hours=1))
    assert sent == [equity_stop.SWEEP_FAILED_CODE]
    with factory() as session:
        beat = session.get(ControlledRuntimeStatusRow, equity_stop.EQUITY_STOP_SWEEP_COMPONENT)
        assert beat is not None
        assert beat.heartbeat_at.replace(tzinfo=UTC) == NOON  # not advanced by the failure
    engine.dispose()


# D1 (Sofien 2026-11-05): a balance edit applies from the next UTC day's baseline.
def _edit_balance(
    session: Session, org: UUID, user: UUID, balance: Decimal, now: datetime = NOON
) -> None:
    from app.api.routes.risk import _record_balance_edit
    from app.schemas.common import MembershipRole
    from app.schemas.risk import UserRiskSettingsUpdate
    from app.security.tenant import TenantContext
    from app.services.risk.settings_service import RiskSettingsService

    service = RiskSettingsService(session, AuditService(session))
    before = service.get(organization_id=org, user_id=user)
    after = service.update(
        UserRiskSettingsUpdate(default_account_balance=balance),
        organization_id=org,
        user_id=user,
    )
    tenant = TenantContext(
        user_id=user,
        organization_id=org,
        email="trader@example.com",
        membership_role=MembershipRole.TRADER,
    )
    _record_balance_edit(session, tenant, before, after, now=now)


def _edits(session: Session, org: UUID) -> list[dict[str, str]]:
    return [
        a.redacted_metadata
        for a in session.scalars(
            select(AuditLog)
            .where(
                AuditLog.organization_id == org,
                AuditLog.resource_type == "account_equity_stop_baseline",
            )
            .order_by(AuditLog.created_at)
        ).all()
    ]


def test_lowering_the_balance_leaves_todays_baseline_and_does_not_false_trip(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account)
    row = _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    _edit_balance(session, org, user, Decimal("8000"))
    assert (row.day_start_on, row.day_start_equity) == (DAY, Decimal("10000"))
    assert row.day_start_balance == Decimal("10000")  # pre-edit balance recorded
    # Today's check still reads the start-of-day balance, so -20% is not a loss.
    assert _sweep(session, NOON + timedelta(minutes=1)) == 0
    assert _active(session, org) is False


def test_raising_the_balance_mid_loss_does_not_dilute_todays_stop(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account)
    row = _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    _position(session, org, user, realized=Decimal("-900"), closed_at=NOON - timedelta(hours=1))
    assert _sweep(session, NOON) == 0  # 9%: under the threshold
    _edit_balance(session, org, user, Decimal("20000"))
    assert (row.day_start_on, row.day_start_equity) == (DAY, Decimal("10000"))
    # 101 more is 10.01% of today's 10000 baseline. Read against the new
    # 20000 balance it would be 18999 > 9000 and mask the loss.
    _position(session, org, user, realized=Decimal("-101"), closed_at=NOON)
    assert _sweep(session, NOON + timedelta(minutes=1)) == 1
    [trip] = _trips(session, org)
    assert trip.redacted_metadata["cause"] == "breach"
    assert (trip.redacted_metadata["baseline"], trip.redacted_metadata["equity"]) == (
        "10000.00",
        "8999.00",
    )


# Griller R2 blocker (c): an edit-and-revert must not forgive the day's losses.
def test_edit_and_revert_cannot_wipe_the_days_losses(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account)
    row = _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    _position(session, org, user, realized=Decimal("-900"), closed_at=NOON - timedelta(hours=1))
    assert _sweep(session, NOON) == 0  # 9%: under the threshold
    _edit_balance(session, org, user, Decimal("9999.99"))
    assert row.day_start_equity == Decimal("10000")
    _edit_balance(session, org, user, Decimal("10000"))
    assert row.day_start_equity == Decimal("10000")
    assert row.day_start_balance == Decimal("10000")  # the first edit's pre-edit balance
    # 101 more is 10.01% of the day; the baseline never moved.
    _position(session, org, user, realized=Decimal("-101"), closed_at=NOON)
    assert _sweep(session, NOON + timedelta(minutes=1)) == 1
    assert [a.redacted_metadata["cause"] for a in _trips(session, org)] == ["breach"]
    down, up = _edits(session, org)
    assert down["actor_user_id"] == str(user) and down["actor_role"] == "trader"
    assert (down["old_balance"], down["new_balance"]) == ("10000.00", "9999.99")
    assert (up["old_balance"], up["new_balance"]) == ("9999.99", "10000.00")
    assert down["outcome"] == up["outcome"] == "baseline_unchanged"
    assert down["baseline"] == up["baseline"] == "10000.00"
    assert down["baseline_day"] == down["day"] == DAY.isoformat()
    assert down["applies_from"] == "2026-11-06"
    assert down["account_id"] == str(account)


def test_balance_edit_applies_at_the_next_utc_day_snapshot(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account)
    row = _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    _position(session, org, user, realized=Decimal("-500"), closed_at=NOON - timedelta(hours=1))
    _edit_balance(session, org, user, Decimal("20000"))
    assert row.day_start_equity == Decimal("10000")
    tomorrow = datetime(2026, 11, 6, 0, 5, tzinfo=UTC)
    assert _sweep(session, tomorrow) == 0
    # New balance + all-time realized: 20000 - 500.
    assert (row.day_start_on, row.day_start_equity) == (date(2026, 11, 6), Decimal("19500"))
    assert row.day_start_balance == Decimal("20000")
    # The 10% stop on the new day is measured against the new baseline.
    _position(session, org, user, realized=Decimal("-1950"), closed_at=tomorrow)
    assert _sweep(session, tomorrow + timedelta(hours=1)) == 0  # exactly 10%
    _position(session, org, user, realized=Decimal("-0.01"), closed_at=tomorrow)
    assert _sweep(session, tomorrow + timedelta(hours=2)) == 1


def test_balance_edit_leaves_a_first_sight_row_to_seed_from_post_edit_equity(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account)
    row = _row(session, org, account, baseline=None, day=None)
    _edit_balance(session, org, user, Decimal("12000"))
    assert (row.day_start_on, row.day_start_equity) == (None, None)
    [audit] = _edits(session, org)
    assert audit["outcome"] == "first_sight_unchanged"
    assert _sweep(session, NOON) == 0
    assert (row.day_start_on, row.day_start_equity) == (DAY, Decimal("12000"))


def test_balance_edit_does_not_deactivate_an_armed_switch(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account)
    row = _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    _position(session, org, user, realized=Decimal("-1500"), closed_at=NOON - timedelta(hours=1))
    assert _sweep(session, NOON) == 1
    _edit_balance(session, org, user, Decimal("12000"))
    assert _active(session, org) is True
    assert row.equity_stop_tripped_on == DAY
    assert row.day_start_equity == Decimal("10000")  # unchanged; still a 15% day
    _edit_balance(session, org, user, Decimal("12000"))  # unchanged balance: no audit
    assert len(_edits(session, org)) == 1
    assert _sweep(session, NOON + timedelta(minutes=1)) == 0  # no second arm today
    assert len(_trips(session, org)) == 1


# Griller R2 C1: a canonical paper-trade realized loss alone reaches stop equity.
def test_canonical_paper_trade_realized_loss_trips_the_stop(world) -> None:
    from app.db.models import JournalTrade
    from app.schemas.common import JournalTradeSource, JournalTradeStatus, TradeDirection

    session, org, user, account = world
    _book(session, org, user, account)
    _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    session.add(
        JournalTrade(
            organization_id=org,
            user_id=user,
            source=JournalTradeSource.PAPER_EXECUTION,
            status=JournalTradeStatus.CLOSED,
            symbol="BTCUSDT",
            timeframe="15m",
            direction=TradeDirection.LONG,
            size=Decimal("1"),
            entry_price=Decimal("100"),
            exit_price=Decimal("90"),
            entry_time=NOON - timedelta(hours=2),
            exit_time=NOON - timedelta(hours=1),
            net_pnl=Decimal("-1000.01"),
            execution_lifecycle_id=uuid4(),
        )
    )
    session.flush()
    assert _read(session, org, user).equity == Decimal("8999.99")
    assert _sweep(session, NOON) == 1
    [audit] = _trips(session, org)
    assert audit.redacted_metadata["cause"] == "breach"
    assert audit.redacted_metadata["equity"] == "8999.99"


# Griller R2 C4: an orphan breaker row does not block the org; it is paged.
def test_orphan_breaker_row_is_paged_and_does_not_block_the_org(world, monkeypatch) -> None:
    from app.services.automated_paper_loop import _breaker_refusing
    from app.services.risk_policy.equity_stop import (
        ORPHAN_ROWS_CODE,
        entry_baseline_overdue,
        orphan_breaker_rows,
    )
    from app.services.safety_alerts import TELEGRAM_SAFETY_MANDATORY_CODES

    session, org, user, account = world
    _book(session, org, user, account)
    _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    orphan = uuid4()  # no ExecutionAccount: never swept, its day ages
    _row(session, org, orphan, baseline=Decimal("10000"), day=DAY - timedelta(days=3))
    assert [tuple(r) for r in orphan_breaker_rows(session)] == [(org, orphan)]
    assert (
        entry_baseline_overdue(session, organization_id=org, now=NOON, settings=_settings())
        is False
    )
    assert _breaker_refusing(session, org, NOON) is False
    # A real account's stale baseline still refuses entries.
    session.scalar(
        select(RiskPolicyBreakerState).where(RiskPolicyBreakerState.account_id == account)
    ).day_start_on = DAY - timedelta(days=1)
    assert _breaker_refusing(session, org, NOON) is True
    session.commit()
    assert ORPHAN_ROWS_CODE in TELEGRAM_SAFETY_MANDATORY_CODES

    from app.services import safety_alerts
    from app.workers.paper_exit_monitor import _sweep_account_equity_stops

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        safety_alerts,
        "deliver_safety_alert",
        lambda _settings, alert, **_kw: sent.append((alert.code, alert.fields["rows"])) or True,
    )
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    _sweep_account_equity_stops(factory, _settings(), NOON)
    assert sent == [(ORPHAN_ROWS_CODE, f"{org}:{orphan}")]


# Extra pin: the exact 10% boundary does not trip through the books path.
def test_exactly_ten_percent_from_the_books_does_not_trip(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account, balance=Decimal("12345.60"))
    _row(session, org, account, baseline=Decimal("12345.60"), day=DAY)
    _position(session, org, user, realized=Decimal("-1234.56"), closed_at=NOON - timedelta(hours=1))
    assert _read(session, org, user).equity == Decimal("11111.04")
    assert _sweep(session, NOON) == 0
    assert _active(session, org) is False and _trips(session, org) == []
    _position(session, org, user, realized=Decimal("-0.01"), closed_at=NOON)
    assert _sweep(session, NOON) == 1


# Extra pin: once per UTC day through the sweep, after an owner deactivation.
def test_sweep_arms_once_per_day_after_owner_deactivation(world) -> None:
    session, org, user, account = world
    _book(session, org, user, account)
    _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    _position(session, org, user, realized=Decimal("-1200"), closed_at=NOON - timedelta(hours=1))
    assert _sweep(session, NOON) == 1
    KillSwitchService(session, AuditService(session), _settings()).deactivate(
        organization_id=org,
        actor_user_id=user,
        payload=KillSwitchMutationRequest(confirm=True, reason="owner reviewed"),
    )
    for minutes, loss in ((5, "-300"), (30, "-800")):
        _position(session, org, user, realized=Decimal(loss), closed_at=NOON)
        assert _sweep(session, NOON + timedelta(minutes=minutes)) == 0
    assert _active(session, org) is False
    assert len(_trips(session, org)) == 1
    # The next UTC day re-baselines; a new breach that day arms again.
    tomorrow = datetime(2026, 11, 6, 0, 5, tzinfo=UTC)
    assert _sweep(session, tomorrow) == 0
    _position(session, org, user, realized=Decimal("-900"), closed_at=tomorrow)
    assert _sweep(session, tomorrow + timedelta(hours=1)) == 1
    assert len(_trips(session, org)) == 2


# Griller R3 D2: one user's balance edit touches no baseline, theirs or another user's.
def test_balance_edit_leaves_both_users_baselines_unchanged(world) -> None:
    session, org, user, account = world
    other, other_account = uuid4(), uuid4()
    _book(session, org, user, account)
    _book(session, org, other, other_account)
    mine = _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    theirs = _row(session, org, other_account, baseline=Decimal("10000"), day=DAY)
    _edit_balance(session, org, user, Decimal("8000"))
    assert (mine.day_start_on, mine.day_start_equity) == (DAY, Decimal("10000"))
    assert (theirs.day_start_on, theirs.day_start_equity) == (DAY, Decimal("10000"))
    assert theirs.day_start_balance is None  # the other user's row is not written
    [audit] = _edits(session, org)
    assert (audit["account_id"], audit["outcome"]) == (str(account), "baseline_unchanged")
    assert _sweep(session, NOON + timedelta(minutes=1)) == 0
    assert _active(session, org) is False


# Griller R4 B1 (Sofien 2026-11-05): a 3% to 9.9% realized day still trades.
# The cash daily-loss lock, the autonomous daily-loss percent and the sizer's
# realized-loss term are not brakes; only the 10% equity stop is.
@pytest.mark.parametrize("loss", [Decimal("300"), Decimal("600"), Decimal("990")])
def test_realized_loss_below_ten_percent_still_opens_and_sizes(world, loss: Decimal) -> None:
    from types import SimpleNamespace

    from app.db.models import UserRiskSettings
    from app.schemas.common import RiskAction, TradeDirection
    from app.schemas.risk import RiskCheckRequest
    from app.services.automated_paper_loop import _profile_open_book
    from app.services.execution_claim import ReservationIntent, evaluate_claim_predicate
    from app.services.risk.daily_risk_accounting import DailyRiskAccounting
    from app.services.risk.engine import RiskEngine
    from app.services.risk.rules import RiskEvaluationContext
    from app.services.risk.settings_service import RiskSettingsService
    from app.services.risk_policy.profile import Grade, PolicyKind, sofien_aggressive_profile
    from app.services.risk_policy.sizer import SizingInput, size_trade

    session, org, user, account = world
    _book(session, org, user, account)
    settings_row = session.scalar(select(UserRiskSettings))
    assert settings_row is not None
    settings_row.daily_loss_limit = Decimal("250")  # cash limit below every case
    _row(session, org, account, baseline=Decimal("10000"), day=DAY)
    _position(session, org, user, realized=-loss, closed_at=NOON - timedelta(hours=1))

    # (a) The cash lock is set for display, but nothing refuses on it.
    snapshot = DailyRiskAccounting(
        session, RiskSettingsService(session, AuditService(session)), clock=lambda: NOON
    ).sync_from_portfolio(organization_id=org, user_id=user)
    assert snapshot.daily_locked is True
    accounting = SimpleNamespace(
        daily_locked=True,
        max_notional=Decimal("100000"),
        reserved_notional=Decimal("0"),
        actual_notional=Decimal("0"),
        max_daily_loss=Decimal("1000"),
        reserved_daily_loss=Decimal("0"),
        actual_daily_loss=Decimal("0"),
        max_trade_slots=10,
        reserved_trade_slots=0,
        actual_trade_count=0,
        max_symbol_notional=Decimal("100000"),
        symbol_reserved={},
        symbol_actual={},
    )
    intent = ReservationIntent(
        pending_notional=Decimal("100"),
        daily_loss=Decimal("10"),
        trade_slots=1,
        instrument="BTC-USDT",
        exposure_unit="USDT",
        remaining_quantity=Decimal("1"),
        quantity_unit="base",
        contract_multiplier=Decimal("1"),
        contract_type="linear",
    )
    assert (
        evaluate_claim_predicate(
            blocking_epoch=False,
            kill_active=False,
            accounting=accounting,  # type: ignore[arg-type]
            intent=intent,
        )
        is None
    )
    verdict = RiskEngine().evaluate(
        RiskCheckRequest(
            symbol="BTCUSDT",
            direction=TradeDirection.LONG,
            entry_price=Decimal("100"),
            stop_loss=Decimal("98"),
            position_size=Decimal("1"),
            leverage=Decimal("1"),
            account_equity=snapshot.account_equity,
        ),
        context=RiskEvaluationContext(
            daily_locked=snapshot.daily_locked,
            realized_pnl_today=snapshot.realized_pnl,
            daily_loss_limit=snapshot.daily_loss_limit,
            is_weekend=False,
        ),
    )
    assert verdict.action is not RiskAction.BLOCK

    # (c) The sizer's daily room is the profile limit, not limit minus realized.
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    book = _profile_open_book(
        session,
        SimpleNamespace(organization_id=org, user_id=user),  # type: ignore[arg-type]
        now=NOON,
        daily_loss_limit_percent=profile.daily_loss_limit_percent,
    )
    assert not isinstance(book, str), book
    assert book.remaining_daily_budget_percent == profile.daily_loss_limit_percent
    sized = size_trade(
        profile,
        SizingInput(
            equity=snapshot.account_equity,
            entry=Decimal("100"),
            stop=Decimal("97"),
            direction="long",
            grade=Grade.A,
            grade_risk_percent=Decimal("0.5"),
            atr=Decimal("1"),
            gap_beyond_stop=Decimal("0"),
            fee_rate=Decimal("0"),
            funding_rate=Decimal("0"),
            funding_periods=Decimal("0"),
            slippage_rate=Decimal("0"),
            lot_step=Decimal("0.00000001"),
            min_quantity=Decimal("0.00000001"),
            remaining_daily_budget_percent=book.remaining_daily_budget_percent,
            open_risk_percent=book.open_risk_percent,
            open_notional=book.open_notional,
            open_positions=book.open_positions,
            leverage=Decimal("1"),
            margin_mode="isolated",
        ),
    )
    assert sized.effective_risk_percent > 0

    # The equity stop does not arm below 10%...
    assert _sweep(session, NOON) == 0
    assert _active(session, org) is False
    # ...and arms at 10.01% (ACCOUNT_DAILY_DRAWDOWN).
    _position(
        session, org, user, realized=loss - Decimal("1001"), closed_at=NOON - timedelta(minutes=30)
    )
    assert _read(session, org, user).equity == Decimal("8999")
    assert _sweep(session, NOON + timedelta(minutes=1)) == 1
    assert _active(session, org) is True
    [audit] = _trips(session, org)
    assert audit.redacted_metadata["reason"] == ACCOUNT_DAILY_DRAWDOWN
