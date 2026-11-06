"""Breaker figures are persisted. No pause or latch refuses (AT-ADR-152)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

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


def _stop_reads(monkeypatch: pytest.MonkeyPatch, figures: dict[str, Decimal]) -> None:
    """The equity stop reads its own UTC equity; follow the stubbed figure."""

    from app.services.risk_policy.equity_stop import StopEquityReading

    monkeypatch.setattr(
        "app.services.risk_policy.equity_stop.read_stop_equity",
        lambda _session, **_kwargs: StopEquityReading(figures["equity"]),
    )


def _no_equity_stop(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Replace the account equity stop for tests whose session is a stub."""

    calls: list[dict[str, object]] = []

    def enforce(_session: object, **kwargs: object) -> bool:
        calls.append(kwargs)
        return False

    monkeypatch.setattr("app.services.risk_policy.breaker_trip._enforce_equity_stop", enforce)
    return calls


@pytest.mark.parametrize(
    ("overrides", "flag"),
    [
        ({"realized_pnl_today": Decimal("-700")}, "daily_loss_active"),
        ({"consecutive_losses": 4}, "consecutive_loss_active"),
        ({"equity": Decimal("8000"), "equity_peak": Decimal("10000")}, "drawdown_active"),
        ({"equity": Decimal("9500"), "drawdown_latched": True}, "drawdown_active"),
    ],
)
def test_trip_reports_the_figure_but_writes_no_pause_or_latch_and_does_not_refuse(
    overrides: dict[str, object], flag: str
) -> None:
    recorder = _Recorder()
    verdict = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(**overrides),
        now=NOW,
    )
    assert getattr(verdict, flag) is True
    assert verdict.refuse_new_trades is False and verdict.paused_until is None
    written = recorder.calls[0]
    assert written["daily_paused_until"] is None
    assert written["consecutive_paused_until"] is None
    assert written["drawdown_latched"] is False
    assert written["consecutive_losses"] == overrides.get("consecutive_losses", 0)


def test_profile_close_consecutive_increments_a_loss_and_resets_a_win() -> None:
    from app.services.risk_policy.breaker_trip import profile_close_consecutive

    assert profile_close_consecutive(3, Decimal("-1")) == 4
    assert profile_close_consecutive(3, Decimal("0")) == 0
    assert profile_close_consecutive(4, Decimal("2")) == 0
    assert profile_close_consecutive(4, Decimal("2"), pause_in_force=True) == 4
    assert profile_close_consecutive(4, Decimal("-1"), pause_in_force=True) == 5
    assert profile_close_consecutive(3, Decimal("NaN")) == 4


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
    paused, _refused = _note_profile_close(
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
    counted, _refused = _note_profile_close(
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
    priced, _refused = _note_profile_close(
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
    held, _refused = _note_profile_close(
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
    reset, _refused = _note_profile_close(
        object(),  # type: ignore[arg-type]
        organization_id=org,
        user_id=user,
        account_id=account,
        net_pnl=price_win,
        now=NOW,
        kind=PolicyKind.DEMO,
    )
    assert reset == 0


def test_next_demo_persist_keeps_the_stored_streak_and_does_not_refuse(monkeypatch) -> None:
    _no_equity_stop(monkeypatch)
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
    assert refused is False
    assert written["consecutive_losses"] == 4
    assert written["consecutive_paused_until"] is None
    assert written["drawdown_latched"] is False


@pytest.mark.parametrize("equity", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
def test_ruined_equity_records_the_streak_and_fails_closed_without_latching(
    monkeypatch: pytest.MonkeyPatch,
    equity: Decimal,
) -> None:
    from app.services.risk_policy.breaker_trip import persist_profile_breakers
    from app.services.risk_policy.profile import PolicyKind, sofien_aggressive_profile

    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    written: dict[str, object] = {}
    armed: list[bool] = []

    class _Daily:
        account_equity = equity
        realized_pnl = Decimal("-10000")
        unrealized_pnl = Decimal("0")

    class _Accounting:
        def __init__(self, _session: object, _settings: object, clock: object) -> None:
            return None

        def sync_from_portfolio(self, **_kwargs: object) -> _Daily:
            return _Daily()

    class _Row:
        equity_peak = Decimal("10000")
        consecutive_losses = 2
        drawdown_latched = False
        consecutive_paused_until = None
        daily_paused_until = None

    class _Service:
        def __init__(self, _session: object, _audit: object) -> None:
            return None

        def load_breaker(self, **_kwargs: object) -> _Row:
            return _Row()

        def record_breaker(self, **kwargs: object) -> object:
            written.update(kwargs)
            return kwargs

    class _Kill:
        def __init__(self, _session: object, _audit: object, _settings: object) -> None:
            return None

        def activate(self, **_kwargs: object) -> object:
            armed.append(True)
            return None

    monkeypatch.setattr("app.services.risk.daily_risk_accounting.DailyRiskAccounting", _Accounting)
    monkeypatch.setattr(
        "app.services.risk.settings_service.RiskSettingsService", lambda _session, _audit: None
    )
    monkeypatch.setattr("app.services.audit_service.AuditService", lambda _session: None)
    monkeypatch.setattr("app.services.risk_policy.persistence.RiskPolicyService", _Service)
    monkeypatch.setattr("app.services.risk.kill_switch.KillSwitchService", _Kill)
    monkeypatch.setattr("app.core.config.get_settings", lambda: None)
    stops = _no_equity_stop(monkeypatch)
    refused = persist_profile_breakers(
        object(),  # type: ignore[arg-type]
        organization_id=ORG,
        user_id=uuid4(),
        account_id=ACCOUNT,
        profile=profile,
        now=NOW,
        consecutive_losses=3,
    )
    assert refused is True  # unusable risk-day equity refuses this call
    assert written["drawdown_latched"] is False
    assert written["consecutive_paused_until"] is None
    assert written["consecutive_losses"] == 3
    assert written["equity_peak"] == Decimal("10000")
    # Nothing latches. The equity stop still runs, on its own UTC equity
    # reading (AT-ADR-152), and fails closed there if that is unusable.
    assert armed == []
    assert [call["account_id"] for call in stops] == [ACCOUNT]
    assert "equity" not in stops[0]


def test_a_legacy_consecutive_pause_is_cleared_and_never_refuses(monkeypatch) -> None:
    _no_equity_stop(monkeypatch)
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
    for paused in (NOW - timedelta(hours=1), NOW + timedelta(hours=10)):
        state["paused"] = paused
        refused = persist_profile_breakers(
            object(),  # type: ignore[arg-type]
            organization_id=ORG,
            user_id=uuid4(),
            account_id=ACCOUNT,
            profile=profile,
            now=NOW,
        )
        assert refused is False
        assert written["consecutive_losses"] == 4  # the streak stays a figure
        assert written["consecutive_paused_until"] is None


def test_profile_close_counts_the_streak_and_writes_no_pause(monkeypatch) -> None:
    _no_equity_stop(monkeypatch)
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
        def consecutive_paused_until(self) -> object:
            return state["paused"]

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
        kept, refused = _note_profile_close(
            object(),  # type: ignore[arg-type]
            organization_id=org,
            user_id=user,
            account_id=account,
            net_pnl=Decimal("25"),
            now=NOW,
            kind=kind,
        )
        assert kept == 4 and refused is False
        assert written["consecutive_losses"] == 4
        assert written["consecutive_paused_until"] is None
        increased, refused = _note_profile_close(
            object(),  # type: ignore[arg-type]
            organization_id=org,
            user_id=user,
            account_id=account,
            net_pnl=Decimal("-1"),
            now=NOW,
            kind=kind,
        )
        assert refused is False
        assert increased == 5
        assert written["consecutive_losses"] == 5
        assert written["consecutive_paused_until"] is None


def test_a_daily_loss_past_the_profile_limit_writes_no_pause_and_does_not_refuse(
    monkeypatch,
) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.models import Organization
    from app.services.audit_service import AuditService
    from app.services.risk_policy.breaker_trip import persist_profile_breakers
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
    _stop_reads(monkeypatch, figures)
    profile = placeholder_profile(PolicyKind.INTERNAL_PAPER)
    tripped = persist_profile_breakers(
        session,
        organization_id=ORG,
        user_id=uuid4(),
        account_id=ACCOUNT,
        profile=profile,
        now=NOW,
    )
    service = RiskPolicyService(session, AuditService(session))
    stored = service.load_breaker(organization_id=ORG, account_id=ACCOUNT)
    # -200 is past the placeholder's daily limit; it is a figure, not a pause.
    assert tripped is False
    assert stored is not None
    assert stored.daily_paused_until is None
    assert stored.drawdown_latched is False
    assert stored.consecutive_paused_until is None
    assert service.breaker_active(organization_id=ORG, now=NOW) is False


def test_the_next_scan_clears_a_legacy_latch_and_pauses() -> None:
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.models import ExecutionAccount, Organization, User
    from app.schemas.trade_plan import AccountMode, ExecutionMode
    from app.services.audit_service import AuditService
    from app.services.risk_policy.persistence import RiskPolicyService

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(Organization(id=ORG, name="Latch org"))
    owner = uuid4()
    session.add(User(id=owner, email="latch-reset@test.example", hashed_password="not-a-real-hash"))
    session.add(
        ExecutionAccount(
            id=ACCOUNT,
            organization_id=ORG,
            user_id=owner,
            name="Latch account",
            execution_mode=ExecutionMode.PAPER,
            account_mode=AccountMode.NET,
            enabled=True,
        )
    )
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
    cleared = service.load_breaker(organization_id=ORG, account_id=ACCOUNT)
    assert cleared is not None
    assert cleared.daily_paused_until is None
    assert cleared.consecutive_paused_until is None
    assert cleared.drawdown_latched is False
    assert service.breaker_active(organization_id=ORG, now=NOW) is False


def test_daily_equity_stop_arms_kill_switch_and_a_later_scan_does_not_clear_it(monkeypatch) -> None:
    """A 20% same-day loss arms through ACCOUNT_DAILY_DRAWDOWN, not the peak latch."""

    from datetime import timedelta

    import pytest
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.core.config import Settings
    from app.core.errors import TradingPolicyError
    from app.db.base import Base
    from app.db.models import AuditLog, Organization
    from app.schemas.common import AuditEventType
    from app.schemas.risk import KillSwitchMutationRequest
    from app.services.audit_service import AuditService
    from app.services.risk.kill_switch import KillSwitchService
    from app.services.risk_policy.breaker_trip import persist_profile_breakers
    from app.services.risk_policy.profile import PolicyKind, sofien_aggressive_profile

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    org = uuid4()
    user = uuid4()
    account = uuid4()
    session.add(Organization(id=org, name="Drawdown kill org"))
    session.commit()
    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_internal",
        provider_mode="mock",
        market_data_provider="mock",
        global_kill_switch_active=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="drawdown-kill-switch-secret-32bytes",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
    )
    figures = {"equity": Decimal("10000"), "realized": Decimal("0"), "unrealized": Decimal("0")}

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
    _stop_reads(monkeypatch, figures)
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    now = datetime(2026, 10, 19, 12, tzinfo=UTC)
    opened = persist_profile_breakers(
        session,
        organization_id=org,
        user_id=user,
        account_id=account,
        profile=profile,
        now=now,
        settings=settings,
    )
    kill = KillSwitchService(session, AuditService(session), settings)
    assert opened is False
    assert kill.is_execution_blocked(organization_id=org) is False

    figures["equity"] = Decimal("8000")
    figures["realized"] = Decimal("-2000")
    tripped = persist_profile_breakers(
        session,
        organization_id=org,
        user_id=user,
        account_id=account,
        profile=profile,
        now=now + timedelta(hours=1),
        settings=settings,
    )
    assert tripped is True
    with pytest.raises(TradingPolicyError):
        kill.assert_execution_allowed(organization_id=org, user_id=user)
    activated = list(
        session.scalars(
            select(AuditLog).where(AuditLog.action == AuditEventType.KILL_SWITCH_ACTIVATED)
        ).all()
    )
    assert len(activated) == 1
    triggered = list(
        session.scalars(
            select(AuditLog).where(
                AuditLog.action == AuditEventType.KILL_SWITCH_TRIGGERED,
                AuditLog.resource_type == "account_equity_stop",
            )
        ).all()
    )
    assert [row.redacted_metadata["reason"] for row in triggered] == ["ACCOUNT_DAILY_DRAWDOWN"]

    figures["equity"] = Decimal("9700")
    figures["realized"] = Decimal("-300")
    held = persist_profile_breakers(
        session,
        organization_id=org,
        user_id=user,
        account_id=account,
        profile=profile,
        now=now + timedelta(hours=2),
        settings=settings,
    )
    # No second arm and no soft pause; the armed switch is what blocks.
    assert held is False
    assert kill.get_status(organization_id=org).active is True
    repeat = list(
        session.scalars(
            select(AuditLog).where(AuditLog.action == AuditEventType.KILL_SWITCH_ACTIVATED)
        ).all()
    )
    assert len(repeat) == 1
    kill.deactivate(
        organization_id=org,
        actor_user_id=user,
        payload=KillSwitchMutationRequest(confirm=True, reason="human reset after review"),
    )
    assert kill.is_execution_blocked(organization_id=org) is False
    after_reset = persist_profile_breakers(
        session,
        organization_id=org,
        user_id=user,
        account_id=account,
        profile=profile,
        now=now + timedelta(hours=3),
        settings=settings,
    )
    assert after_reset is False
    assert kill.is_execution_blocked(organization_id=org) is False
    still_one = list(
        session.scalars(
            select(AuditLog).where(AuditLog.action == AuditEventType.KILL_SWITCH_ACTIVATED)
        ).all()
    )
    assert len(still_one) == 1
    engine.dispose()
