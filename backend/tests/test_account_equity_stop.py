"""Account daily equity stop (AT-ADR-152).

One account-level loss rule arms the organization kill switch: equity more
than the threshold below the 00:00 UTC baseline. Unreadable equity and a
missing or stale baseline fail closed. A single loss and a losing streak
do not arm it.
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
        (None, None, "baseline_missing"),
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


# 6b/7. Losing closes and a streak past the limit refuse without arming.
def test_loss_streak_and_peak_drawdown_do_not_arm_the_kill_switch(world, monkeypatch) -> None:
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
        persist_profile_breakers(
            session,
            now=NOON + timedelta(minutes=losses),
            consecutive_losses=losses,
            settings=_settings(),
            **kwargs,
        )
    row = session.scalar(select(RiskPolicyBreakerState))
    assert row is not None and row.consecutive_paused_until is not None  # soft pause holds
    assert _active(session, org) is False
    assert _trips(session, org) == []
    # A daily equity loss past 10% is what arms it.
    figures["equity"] = Decimal("8999")
    assert persist_profile_breakers(
        session, now=NOON + timedelta(hours=1), settings=_settings(), **kwargs
    )
    assert _active(session, org) is True
    assert [a.redacted_metadata["cause"] for a in _trips(session, org)] == ["breach"]


def test_peak_drawdown_latch_refuses_without_arming_the_kill_switch(world, monkeypatch) -> None:
    """The peak latch is a soft refusal now. Only the SoD daily rule arms (AT-ADR-152)."""

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
    assert refused is True and row is not None and row.drawdown_latched is True
    assert _active(session, org) is False
    assert _trips(session, org) == []


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

    class _Broken:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def sync_from_portfolio(self, **_kwargs: object) -> object:
            raise RuntimeError("portfolio unreadable")

    monkeypatch.setattr("app.services.risk.daily_risk_accounting.DailyRiskAccounting", _Broken)
    assert (
        sweep_account_equity_stops(session, now=rollover + timedelta(hours=1), settings=_settings())
        == 1
    )
    assert _active(session, org) is True
    assert [a.redacted_metadata["cause"] for a in _trips(session, org)] == ["equity_unreadable"]


def test_equity_stop_migration_is_the_single_head_after_demo_leverage() -> None:
    from tests.support.alembic_heads import alembic_script_directory, single_alembic_head

    script = alembic_script_directory()
    assert single_alembic_head(script) == "a25equitystop001"
    assert script.get_revision("a25equitystop001").down_revision == "a24demoleverage001"


def test_a_close_dated_before_todays_baseline_does_not_trip_or_re_date_it(world) -> None:
    session, org, user, account = world
    today = date(2026, 11, 6)
    row = _row(session, org, account, baseline=Decimal("10000"), day=today)
    bar_before_midnight = datetime(2026, 11, 5, 23, 50, tzinfo=UTC)
    assert _enforce(session, org, user, account, Decimal("5000"), now=bar_before_midnight) is False
    assert (row.day_start_on, row.day_start_equity) == (today, Decimal("10000"))
    assert _active(session, org) is False
