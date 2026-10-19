"""Breaker trips are persisted. A drawdown latch is not cleared here."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.services.risk_policy.breaker_trip import trip_breakers
from app.services.risk_policy.breakers import BreakerConfig, BreakerSnapshot

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
ORG = uuid4()
ACCOUNT = uuid4()


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_breaker(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return kwargs


def _snapshot(**overrides: object) -> BreakerSnapshot:
    base: dict[str, object] = {
        "equity": Decimal("10000"),
        "equity_peak": Decimal("10000"),
        "reference_equity": Decimal("10000"),
        "realized_pnl_today": Decimal("0"),
        "unrealized_pnl_today": Decimal("0"),
        "consecutive_losses": 0,
        "drawdown_latched": False,
        "now": NOW,
    }
    base.update(overrides)
    return BreakerSnapshot(**base)  # type: ignore[arg-type]


def _config() -> BreakerConfig:
    return BreakerConfig(
        daily_loss_limit_percent=Decimal("6"),
        drawdown_breaker_percent=Decimal("10"),
        consecutive_loss_limit=4,
    )


def test_daily_loss_pause_is_recorded_without_a_consecutive_pause() -> None:
    recorder = _Recorder()
    verdict = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(realized_pnl_today=Decimal("-700")),
        now=NOW,
    )
    assert verdict.daily_loss_active and verdict.refuse_new_trades
    assert verdict.consecutive_loss_active is False
    written = recorder.calls[0]
    assert written["daily_paused_until"] == verdict.paused_until
    assert written["consecutive_paused_until"] is None
    assert written["drawdown_latched"] is False


def test_consecutive_losses_pause_without_touching_the_daily_pause() -> None:
    from datetime import timedelta

    from app.services.risk_policy.breaker_trip import consecutive_pause_expiry

    recorder = _Recorder()
    verdict = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(consecutive_losses=4),
        now=NOW,
        cooldown_hours=24,
    )
    assert verdict.consecutive_loss_active and verdict.refuse_new_trades
    written = recorder.calls[0]
    expiry = consecutive_pause_expiry(NOW, cooldown_hours=24, existing_paused_until=None)
    assert expiry == NOW + timedelta(hours=24)
    assert written["consecutive_paused_until"] == expiry
    assert written["consecutive_paused_until"] != verdict.paused_until
    assert written["daily_paused_until"] is None
    assert written["consecutive_losses"] == 4


def test_drawdown_latches_and_stays_latched() -> None:
    recorder = _Recorder()
    verdict = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(equity=Decimal("8000"), equity_peak=Decimal("10000")),
        now=NOW,
    )
    assert verdict.drawdown_active and verdict.trip_kill_switch
    assert recorder.calls[0]["drawdown_latched"] is True
    held = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(
            equity=Decimal("9500"),
            equity_peak=Decimal("10000"),
            drawdown_latched=True,
        ),
        now=NOW,
    )
    assert held.drawdown_active
    assert recorder.calls[1]["drawdown_latched"] is True


def test_consecutive_pause_is_not_extended_and_expires() -> None:
    from datetime import timedelta

    from app.services.risk_policy.breaker_trip import streak_after_expiry

    recorder = _Recorder()
    trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(consecutive_losses=4),
        now=NOW,
        cooldown_hours=24,
    )
    expiry = recorder.calls[0]["consecutive_paused_until"]
    assert isinstance(expiry, datetime)
    later = NOW + timedelta(hours=18)
    trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(consecutive_losses=4, now=later),
        now=later,
        existing_consecutive_paused_until=expiry,
        cooldown_hours=24,
    )
    assert recorder.calls[1]["consecutive_paused_until"] == expiry
    assert recorder.calls[1]["consecutive_losses"] == 4
    assert streak_after_expiry(4, expiry, expiry) == 0
    cleared = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(consecutive_losses=0, now=expiry),
        now=expiry,
        existing_consecutive_paused_until=None,
        cooldown_hours=24,
    )
    assert cleared.refuse_new_trades is False
    assert recorder.calls[2]["consecutive_losses"] == 0
    assert recorder.calls[2]["consecutive_paused_until"] is None


def test_profile_close_consecutive_increments_a_loss_and_resets_a_win() -> None:
    from app.services.risk_policy.breaker_trip import profile_close_consecutive

    assert profile_close_consecutive(3, Decimal("-1")) == 4
    assert profile_close_consecutive(3, Decimal("0")) == 0
    assert profile_close_consecutive(4, Decimal("2")) == 0
    assert profile_close_consecutive(4, Decimal("2"), pause_in_force=True) == 4
    assert profile_close_consecutive(4, Decimal("-1"), pause_in_force=True) == 5
    assert profile_close_consecutive(3, Decimal("NaN")) == 4


def test_stored_consecutive_count_still_refuses_the_next_entry() -> None:
    recorder = _Recorder()
    first = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(consecutive_losses=4),
        now=NOW,
    )
    stored = recorder.calls[0]["consecutive_losses"]
    assert first.refuse_new_trades and stored == 4
    second = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(consecutive_losses=stored),  # type: ignore[arg-type]
        now=NOW,
    )
    assert second.consecutive_loss_active and second.refuse_new_trades
    first_pause = recorder.calls[0]["consecutive_paused_until"]
    assert recorder.calls[1]["consecutive_paused_until"] == first_pause


def test_demo_profile_close_persists_the_demo_streak(monkeypatch) -> None:
    from app.services.canonical_paper_execution import _note_profile_close
    from app.services.demo_lifecycle_resolution import signed_verified_demo_pnl
    from app.services.risk_policy.plan_sizing import ActiveProfile
    from app.services.risk_policy.profile import PolicyKind, sofien_aggressive_profile

    profile = sofien_aggressive_profile(PolicyKind.DEMO)
    stored: dict[str, object] = {"count": 0, "kind": None, "written": []}

    def load(_session: object, _organization_id: object, kind_value: str, _now: object) -> object:
        stored["kind"] = kind_value
        return ActiveProfile(profile=profile, version_id=uuid4())

    class _Row:
        def __init__(self, count: int) -> None:
            self.consecutive_losses = count

    class _Service:
        def __init__(self, _session: object, _audit: object) -> None:
            return None

        def load_breaker(self, *, organization_id: object, account_id: object) -> _Row:
            count = stored["count"]
            assert isinstance(count, int)
            return _Row(count)

    def persist(_session: object, **kwargs: object) -> bool:
        written = stored["written"]
        assert isinstance(written, list)
        count = kwargs["consecutive_losses"]
        assert isinstance(count, int)
        written.append(count)
        stored["count"] = count
        return count >= profile.consecutive_loss_limit

    monkeypatch.setattr("app.services.automated_paper_loop._load_active_profile", load)
    monkeypatch.setattr("app.services.risk_policy.persistence.RiskPolicyService", _Service)
    monkeypatch.setattr("app.services.audit_service.AuditService", lambda _session: None)
    monkeypatch.setattr("app.services.risk_policy.breaker_trip.persist_profile_breakers", persist)
    org, user, account = uuid4(), uuid4(), uuid4()
    loss = signed_verified_demo_pnl(
        is_long=True,
        entry_price=Decimal("100"),
        exit_price=Decimal("98"),
        quantity=Decimal("1"),
        contract_multiplier=Decimal("0.001"),
        reported_pnl=Decimal("-1"),
    )
    assert loss < 0
    for _ in range(3):
        _note_profile_close(
            object(),  # type: ignore[arg-type]
            organization_id=org,
            user_id=user,
            account_id=account,
            net_pnl=loss,
            now=NOW,
            kind=PolicyKind.DEMO,
        )
    assert stored["kind"] == "demo"
    assert stored["written"] == [1, 2, 3]
    paused = _note_profile_close(
        object(),  # type: ignore[arg-type]
        organization_id=org,
        user_id=user,
        account_id=account,
        net_pnl=loss,
        now=NOW,
        kind=PolicyKind.DEMO,
    )
    assert paused == 4
    # A positive venue fillPnl must not hide a price result that loses after fees.
    misleading = signed_verified_demo_pnl(
        is_long=True,
        entry_price=Decimal("100"),
        exit_price=Decimal("100.01"),
        quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        reported_pnl=Decimal("10"),
        fees=Decimal("0.05"),
        funding=Decimal("0"),
    )
    assert misleading < 0
    counted = _note_profile_close(
        object(),  # type: ignore[arg-type]
        organization_id=org,
        user_id=user,
        account_id=account,
        net_pnl=misleading,
        now=NOW,
        kind=PolicyKind.DEMO,
    )
    assert counted == 5
    fee_only_loss = signed_verified_demo_pnl(
        is_long=True,
        entry_price=Decimal("100"),
        exit_price=Decimal("100"),
        quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        reported_pnl=Decimal("0.01"),
        fees=Decimal("0.02"),
        funding=Decimal("0"),
    )
    assert fee_only_loss < 0
    priced = _note_profile_close(
        object(),  # type: ignore[arg-type]
        organization_id=org,
        user_id=user,
        account_id=account,
        net_pnl=fee_only_loss,
        now=NOW,
        kind=PolicyKind.DEMO,
    )
    assert priced == 6
    unknown_fees = signed_verified_demo_pnl(
        is_long=True,
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        reported_pnl=None,
        fees=None,
    )
    assert unknown_fees < 0
    unverified_funding = signed_verified_demo_pnl(
        is_long=True,
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        reported_pnl=Decimal("20"),
        fees=Decimal("0.01"),
        funding=None,
        notional=Decimal("1000000"),
    )
    assert unverified_funding < 0
    held = _note_profile_close(
        object(),  # type: ignore[arg-type]
        organization_id=org,
        user_id=user,
        account_id=account,
        net_pnl=unverified_funding,
        now=NOW,
        kind=PolicyKind.DEMO,
    )
    assert held == 7
    non_finite_funding = signed_verified_demo_pnl(
        is_long=True,
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        reported_pnl=None,
        fees=Decimal("0"),
        funding=Decimal("NaN"),
        notional=Decimal("100"),
    )
    assert non_finite_funding < 0
    price_win = signed_verified_demo_pnl(
        is_long=False,
        entry_price=Decimal("100"),
        exit_price=Decimal("90"),
        quantity=Decimal("2"),
        contract_multiplier=Decimal("1"),
        reported_pnl=None,
        fees=Decimal("0"),
        funding=Decimal("0"),
    )
    assert price_win > 0
    reset = _note_profile_close(
        object(),  # type: ignore[arg-type]
        organization_id=org,
        user_id=user,
        account_id=account,
        net_pnl=price_win,
        now=NOW,
        kind=PolicyKind.DEMO,
    )
    assert reset == 0


def test_next_demo_persist_keeps_the_stored_streak_and_refuses(monkeypatch) -> None:
    from app.services.risk_policy.breaker_trip import persist_profile_breakers
    from app.services.risk_policy.profile import PolicyKind, sofien_aggressive_profile

    profile = sofien_aggressive_profile(PolicyKind.DEMO)
    written: dict[str, object] = {}

    class _Daily:
        account_equity = Decimal("10000")
        realized_pnl = Decimal("0")
        unrealized_pnl = Decimal("0")

    class _Accounting:
        def __init__(self, _session: object, _settings: object, clock: object) -> None:
            return None

        def sync_from_portfolio(self, **_kwargs: object) -> _Daily:
            return _Daily()

    class _Row:
        equity_peak = Decimal("10000")
        consecutive_losses = 4
        drawdown_latched = False

    class _Service:
        def __init__(self, _session: object, _audit: object) -> None:
            return None

        def load_breaker(self, **_kwargs: object) -> _Row:
            return _Row()

        def record_breaker(self, **kwargs: object) -> object:
            written.update(kwargs)
            return kwargs

    monkeypatch.setattr("app.services.risk.daily_risk_accounting.DailyRiskAccounting", _Accounting)
    monkeypatch.setattr(
        "app.services.risk.settings_service.RiskSettingsService", lambda _session, _audit: None
    )
    monkeypatch.setattr("app.services.audit_service.AuditService", lambda _session: None)
    monkeypatch.setattr("app.services.risk_policy.persistence.RiskPolicyService", _Service)
    refused = persist_profile_breakers(
        object(),  # type: ignore[arg-type]
        organization_id=ORG,
        user_id=uuid4(),
        account_id=ACCOUNT,
        profile=profile,
        now=NOW,
    )
    assert refused is True
    assert written["consecutive_losses"] == 4
    assert written["consecutive_paused_until"] is not None
    assert written["drawdown_latched"] is False


def test_expired_pause_clears_the_streak_and_a_future_pause_is_kept(monkeypatch) -> None:
    from datetime import timedelta

    from app.services.risk_policy.breaker_trip import persist_profile_breakers
    from app.services.risk_policy.profile import PolicyKind, sofien_aggressive_profile

    profile = sofien_aggressive_profile(PolicyKind.DEMO)
    state = {
        "losses": 4,
        "paused": NOW - timedelta(hours=1),
    }
    written: dict[str, object] = {}

    class _Daily:
        account_equity = Decimal("10000")
        realized_pnl = Decimal("0")
        unrealized_pnl = Decimal("0")

    class _Accounting:
        def __init__(self, _session: object, _settings: object, clock: object) -> None:
            return None

        def sync_from_portfolio(self, **_kwargs: object) -> _Daily:
            return _Daily()

    class _Row:
        equity_peak = Decimal("10000")
        drawdown_latched = False

        @property
        def consecutive_losses(self) -> int:
            losses = state["losses"]
            assert isinstance(losses, int)
            return losses

        @property
        def consecutive_paused_until(self) -> datetime:
            paused = state["paused"]
            assert isinstance(paused, datetime)
            return paused

    class _Service:
        def __init__(self, _session: object, _audit: object) -> None:
            return None

        def load_breaker(self, **_kwargs: object) -> _Row:
            return _Row()

        def record_breaker(self, **kwargs: object) -> object:
            written.update(kwargs)
            return kwargs

    monkeypatch.setattr("app.services.risk.daily_risk_accounting.DailyRiskAccounting", _Accounting)
    monkeypatch.setattr(
        "app.services.risk.settings_service.RiskSettingsService", lambda _session, _audit: None
    )
    monkeypatch.setattr("app.services.audit_service.AuditService", lambda _session: None)
    monkeypatch.setattr("app.services.risk_policy.persistence.RiskPolicyService", _Service)
    refused = persist_profile_breakers(
        object(),  # type: ignore[arg-type]
        organization_id=ORG,
        user_id=uuid4(),
        account_id=ACCOUNT,
        profile=profile,
        now=NOW,
    )
    assert refused is False
    assert written["consecutive_losses"] == 0
    assert written["consecutive_paused_until"] is None
    state["losses"] = 4
    state["paused"] = NOW + timedelta(hours=10)
    kept = persist_profile_breakers(
        object(),  # type: ignore[arg-type]
        organization_id=ORG,
        user_id=uuid4(),
        account_id=ACCOUNT,
        profile=profile,
        now=NOW,
    )
    assert kept is True
    assert written["consecutive_losses"] == 4
    assert written["consecutive_paused_until"] == NOW + timedelta(hours=10)
    cleared_attempt = persist_profile_breakers(
        object(),  # type: ignore[arg-type]
        organization_id=ORG,
        user_id=uuid4(),
        account_id=ACCOUNT,
        profile=profile,
        now=NOW,
        consecutive_losses=0,
    )
    assert cleared_attempt is True
    assert written["consecutive_losses"] == 4
    assert written["consecutive_paused_until"] == NOW + timedelta(hours=10)


def test_non_negative_close_does_not_end_an_unexpired_pause(monkeypatch) -> None:
    from datetime import timedelta

    from app.services.canonical_paper_execution import _note_profile_close
    from app.services.risk_policy.plan_sizing import ActiveProfile
    from app.services.risk_policy.profile import PolicyKind, sofien_aggressive_profile

    profile = sofien_aggressive_profile(PolicyKind.DEMO)
    pause = NOW + timedelta(hours=10)
    state: dict[str, object] = {"losses": 4, "paused": pause}
    written: dict[str, object] = {}

    class _Daily:
        account_equity = Decimal("10000")
        realized_pnl = Decimal("0")
        unrealized_pnl = Decimal("0")

    class _Accounting:
        def __init__(self, _session: object, _settings: object, clock: object) -> None:
            return None

        def sync_from_portfolio(self, **_kwargs: object) -> _Daily:
            return _Daily()

    class _Row:
        equity_peak = Decimal("10000")
        drawdown_latched = False

        @property
        def consecutive_losses(self) -> int:
            losses = state["losses"]
            assert isinstance(losses, int)
            return losses

        @property
        def consecutive_paused_until(self) -> datetime:
            paused = state["paused"]
            assert isinstance(paused, datetime)
            return paused

    class _Service:
        def __init__(self, _session: object, _audit: object) -> None:
            return None

        def load_breaker(self, **_kwargs: object) -> _Row:
            return _Row()

        def record_breaker(self, **kwargs: object) -> object:
            written.update(kwargs)
            state["losses"] = kwargs["consecutive_losses"]
            state["paused"] = kwargs["consecutive_paused_until"]
            return kwargs

    monkeypatch.setattr(
        "app.services.automated_paper_loop._load_active_profile",
        lambda *_args, **_kwargs: ActiveProfile(profile=profile, version_id=uuid4()),
    )
    monkeypatch.setattr("app.services.risk.daily_risk_accounting.DailyRiskAccounting", _Accounting)
    monkeypatch.setattr(
        "app.services.risk.settings_service.RiskSettingsService", lambda _session, _audit: None
    )
    monkeypatch.setattr("app.services.audit_service.AuditService", lambda _session: None)
    monkeypatch.setattr("app.services.risk_policy.persistence.RiskPolicyService", _Service)
    org, user, account = uuid4(), uuid4(), uuid4()
    for kind in (PolicyKind.DEMO, PolicyKind.INTERNAL_PAPER):
        state["losses"] = 4
        state["paused"] = pause
        kept = _note_profile_close(
            object(),  # type: ignore[arg-type]
            organization_id=org,
            user_id=user,
            account_id=account,
            net_pnl=Decimal("25"),
            now=NOW,
            kind=kind,
        )
        assert kept == 4
        assert written["consecutive_losses"] == 4
        assert written["consecutive_paused_until"] == pause
        increased = _note_profile_close(
            object(),  # type: ignore[arg-type]
            organization_id=org,
            user_id=user,
            account_id=account,
            net_pnl=Decimal("-1"),
            now=NOW,
            kind=kind,
        )
        assert increased == 5
        assert written["consecutive_losses"] == 5
        assert written["consecutive_paused_until"] == pause


def test_recovered_pnl_same_utc_day_keeps_the_daily_pause() -> None:
    from app.services.risk_policy.breaker_trip import _as_utc
    from app.services.risk_policy.clock import next_utc_day

    recorder = _Recorder()
    tripped = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(
            realized_pnl_today=Decimal("-700"),
            unrealized_pnl_today=Decimal("-50"),
        ),
        now=NOW,
    )
    expiry = recorder.calls[0]["daily_paused_until"]
    assert tripped.daily_loss_active
    assert expiry == next_utc_day(NOW)
    later = datetime(2026, 10, 18, 18, tzinfo=UTC)
    held = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(
            realized_pnl_today=Decimal("-10"),
            unrealized_pnl_today=Decimal("-5"),
            now=later,
        ),
        now=later,
        existing_daily_paused_until=expiry,  # type: ignore[arg-type]
    )
    assert held.daily_loss_active is False
    assert held.refuse_new_trades is True
    assert recorder.calls[1]["daily_paused_until"] == expiry
    assert _as_utc(expiry) > later  # type: ignore[arg-type]
    expired = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(
            realized_pnl_today=Decimal("-10"),
            unrealized_pnl_today=Decimal("-5"),
            now=expiry,  # type: ignore[arg-type]
        ),
        now=expiry,  # type: ignore[arg-type]
        existing_daily_paused_until=expiry,  # type: ignore[arg-type]
    )
    assert expired.refuse_new_trades is False
    assert recorder.calls[2]["daily_paused_until"] is None


def test_same_day_scan_keeps_daily_pause_and_breaker_active(monkeypatch) -> None:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.models import Organization
    from app.db.risk_policy import RiskPolicyBreakerInterval
    from app.services.audit_service import AuditService
    from app.services.risk_policy.breaker_trip import _as_utc, persist_profile_breakers
    from app.services.risk_policy.clock import next_utc_day
    from app.services.risk_policy.persistence import RiskPolicyService
    from app.services.risk_policy.profile import PolicyKind, placeholder_profile

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(Organization(id=ORG, name="Daily pause org"))
    session.commit()
    figures = {
        "equity": Decimal("10000"),
        "realized": Decimal("-150"),
        "unrealized": Decimal("-50"),
    }

    class _Daily:
        @property
        def account_equity(self) -> Decimal:
            return figures["equity"]

        @property
        def realized_pnl(self) -> Decimal:
            return figures["realized"]

        @property
        def unrealized_pnl(self) -> Decimal:
            return figures["unrealized"]

    class _Accounting:
        def __init__(self, _session: object, _settings: object, clock: object) -> None:
            return None

        def sync_from_portfolio(self, **_kwargs: object) -> _Daily:
            return _Daily()

    monkeypatch.setattr("app.services.risk.daily_risk_accounting.DailyRiskAccounting", _Accounting)
    profile = placeholder_profile(PolicyKind.INTERNAL_PAPER)
    user_id = uuid4()
    tripped = persist_profile_breakers(
        session,
        organization_id=ORG,
        user_id=user_id,
        account_id=ACCOUNT,
        profile=profile,
        now=NOW,
    )
    service = RiskPolicyService(session, AuditService(session))
    stored = service.load_breaker(organization_id=ORG, account_id=ACCOUNT)
    assert tripped is True
    assert stored is not None
    assert _as_utc(stored.daily_paused_until) == next_utc_day(NOW)  # type: ignore[arg-type]
    assert stored.drawdown_latched is False
    assert stored.consecutive_paused_until is None
    assert service.breaker_active(organization_id=ORG, now=NOW) is True
    figures["realized"] = Decimal("-10")
    figures["unrealized"] = Decimal("-5")
    later = datetime(2026, 10, 18, 18, tzinfo=UTC)
    recovered = persist_profile_breakers(
        session,
        organization_id=ORG,
        user_id=user_id,
        account_id=ACCOUNT,
        profile=profile,
        now=later,
    )
    session.expire_all()
    held = service.load_breaker(organization_id=ORG, account_id=ACCOUNT)
    assert recovered is True
    assert held is not None
    assert held.daily_paused_until is not None
    assert _as_utc(held.daily_paused_until) == next_utc_day(NOW)
    assert held.drawdown_latched is False
    assert held.consecutive_paused_until is None
    assert service.breaker_active(organization_id=ORG, now=later) is True
    daily_closes = session.scalars(
        select(RiskPolicyBreakerInterval).where(
            RiskPolicyBreakerInterval.organization_id == ORG,
            RiskPolicyBreakerInterval.account_id == ACCOUNT,
            RiskPolicyBreakerInterval.kind == "daily",
            RiskPolicyBreakerInterval.phase == "close",
        )
    ).all()
    assert len(daily_closes) == 1
    expiry = next_utc_day(NOW)
    cleared = persist_profile_breakers(
        session,
        organization_id=ORG,
        user_id=user_id,
        account_id=ACCOUNT,
        profile=profile,
        now=expiry,
    )
    session.expire_all()
    ended = service.load_breaker(organization_id=ORG, account_id=ACCOUNT)
    assert cleared is False
    assert ended is not None
    assert ended.daily_paused_until is None
    assert service.breaker_active(organization_id=ORG, now=expiry) is False


def test_scan_cannot_clear_drawdown_latch_or_a_future_pause() -> None:
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.models import Organization
    from app.services.audit_service import AuditService
    from app.services.risk_policy.governance import PolicyActor, PolicyChannel
    from app.services.risk_policy.persistence import RiskPolicyService

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(Organization(id=ORG, name="Latch org"))
    session.commit()
    service = RiskPolicyService(session, AuditService(session))
    pause = NOW + timedelta(hours=10)
    service.record_breaker(
        organization_id=ORG,
        account_id=ACCOUNT,
        equity_peak=Decimal("10000"),
        consecutive_losses=4,
        realized_pnl=Decimal("-20"),
        unrealized_pnl=Decimal("-5"),
        daily_paused_until=pause,
        consecutive_paused_until=pause,
        drawdown_latched=True,
        now=NOW,
    )
    service.record_breaker(
        organization_id=ORG,
        account_id=ACCOUNT,
        equity_peak=Decimal("10000"),
        consecutive_losses=0,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        daily_paused_until=None,
        consecutive_paused_until=None,
        drawdown_latched=False,
        now=NOW,
    )
    session.expire_all()
    held = service.load_breaker(organization_id=ORG, account_id=ACCOUNT)
    assert held is not None
    assert held.daily_paused_until is not None
    assert held.consecutive_paused_until is not None
    assert held.consecutive_losses == 4
    assert held.drawdown_latched is True
    assert service.breaker_active(organization_id=ORG, now=NOW) is True
    service.record_breaker(
        organization_id=ORG,
        account_id=ACCOUNT,
        equity_peak=Decimal("10000"),
        consecutive_losses=0,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        daily_paused_until=None,
        consecutive_paused_until=None,
        drawdown_latched=False,
        now=pause,
    )
    session.expire_all()
    expired = service.load_breaker(organization_id=ORG, account_id=ACCOUNT)
    assert expired is not None
    assert expired.daily_paused_until is None
    assert expired.consecutive_paused_until is None
    assert expired.consecutive_losses == 0
    assert expired.drawdown_latched is True
    service.reset_drawdown_latch(
        organization_id=ORG,
        account_id=ACCOUNT,
        actor=PolicyActor("sofien", PolicyChannel.HUMAN, True),
        owner_user_id="sofien",
        equity=Decimal("9500"),
        drawdown_breaker_percent=Decimal("10"),
        now=pause,
    )
    session.expire_all()
    reset = service.load_breaker(organization_id=ORG, account_id=ACCOUNT)
    assert reset is not None
    assert reset.drawdown_latched is False
    assert service.breaker_active(organization_id=ORG, now=pause) is False
