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
    recorder = _Recorder()
    verdict = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(consecutive_losses=4),
        now=NOW,
    )
    assert verdict.consecutive_loss_active and verdict.refuse_new_trades
    written = recorder.calls[0]
    assert written["consecutive_paused_until"] == verdict.paused_until
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


def test_profile_close_consecutive_increments_a_loss_and_resets_a_win() -> None:
    from app.services.risk_policy.breaker_trip import profile_close_consecutive

    assert profile_close_consecutive(3, Decimal("-1")) == 4
    assert profile_close_consecutive(3, Decimal("0")) == 0
    assert profile_close_consecutive(4, Decimal("2")) == 0


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
    assert recorder.calls[1]["consecutive_paused_until"] == second.paused_until


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
    price_win = signed_verified_demo_pnl(
        is_long=False,
        entry_price=Decimal("100"),
        exit_price=Decimal("90"),
        quantity=Decimal("2"),
        contract_multiplier=Decimal("1"),
        reported_pnl=None,
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
