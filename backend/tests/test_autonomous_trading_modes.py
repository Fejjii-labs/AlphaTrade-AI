"""Guards for the two autonomous modes (AT-ADR-106).

Flags default off. Demo auto-approval stays inside configured risk limits.
The kill switch blocks every mode. Real-money settings never resolve.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.services.automated_paper_loop import AutomatedPaperLoop
from app.services.autonomous_trading import (
    AutonomousMode,
    RiskLimitSnapshot,
    configured_risk_refusal,
    continuation_refusal,
    kill_switch_refusal,
    resolve_autonomous_mode,
)
from app.services.governed_blofin_demo import DEMO_POLICY, GovernedBloFinDemoLoop
from app.workers.watcher_paper import WatcherPaperRuntime, WatcherPaperScanReport


def _view(**updates: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "execution_mode": "paper",
        "exchange_mode": "paper_internal",
        "enable_real_trading": False,
        "real_trading_enabled": False,
        "autonomous_internal_paper_enabled": False,
        "autonomous_blofin_demo_enabled": False,
        "global_kill_switch_active": False,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _limits(**updates: object) -> RiskLimitSnapshot:
    values: dict[str, object] = {
        "max_notional": Decimal("1000"),
        "max_daily_loss": Decimal("100"),
        "max_symbol_notional": Decimal("250"),
        "max_trade_slots": 5,
        "pending_notional": Decimal("100"),
        "proposed_loss": Decimal("10"),
        "instrument": "BTCUSDT",
    }
    values.update(updates)
    return RiskLimitSnapshot(**values)  # type: ignore[arg-type]


def test_autonomous_flags_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings.model_fields["autonomous_internal_paper_enabled"].default is False
    assert Settings.model_fields["autonomous_blofin_demo_enabled"].default is False
    monkeypatch.delenv("AUTONOMOUS_INTERNAL_PAPER_ENABLED", raising=False)
    monkeypatch.delenv("AUTONOMOUS_BLOFIN_DEMO_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.autonomous_internal_paper_enabled is False
    assert settings.autonomous_blofin_demo_enabled is False
    assert settings.real_trading_enabled is False


def test_real_money_settings_never_resolve_even_with_flags() -> None:
    with pytest.raises(ValueError, match="ENABLE_REAL_TRADING"):
        Settings(_env_file=None, enable_real_trading=True)
    with pytest.raises(ValueError, match="trade_live"):
        Settings(_env_file=None, exchange_mode="trade_live")
    with pytest.raises(ValueError, match="execution_mode=trade"):
        Settings(_env_file=None, execution_mode="trade")
    for updates in (
        {"enable_real_trading": True, "autonomous_internal_paper_enabled": True},
        {"real_trading_enabled": True, "autonomous_blofin_demo_enabled": True},
        {"execution_mode": "trade", "exchange_mode": "paper_exchange_demo"},
        {"exchange_mode": "trade_live", "autonomous_blofin_demo_enabled": True},
    ):
        resolution = resolve_autonomous_mode(_view(**updates))
        assert resolution.mode is AutonomousMode.OFF
        assert resolution.refusal == "real_trading_blocked"


def test_modes_are_separate_and_fail_closed_when_unverified() -> None:
    disarmed = resolve_autonomous_mode(_view())
    assert disarmed.mode is AutonomousMode.OFF
    assert disarmed.refusal is None
    assert (
        continuation_refusal(_view(), expected=AutonomousMode.INTERNAL_PAPER)
        == "autonomous_paper_disarmed"
    )
    assert (
        continuation_refusal(_view(), expected=AutonomousMode.BLOFIN_DEMO)
        == "autonomous_demo_disarmed"
    )

    paper = resolve_autonomous_mode(_view(autonomous_internal_paper_enabled=True))
    assert paper.mode is AutonomousMode.INTERNAL_PAPER
    assert paper.refusal is None
    armed_paper = _view(autonomous_internal_paper_enabled=True)
    assert continuation_refusal(armed_paper, expected=AutonomousMode.INTERNAL_PAPER) is None

    armed_demo = _view(
        autonomous_blofin_demo_enabled=True,
        exchange_mode="paper_exchange_demo",
    )
    demo = resolve_autonomous_mode(armed_demo)
    assert demo.mode is AutonomousMode.BLOFIN_DEMO
    assert continuation_refusal(armed_demo, expected=AutonomousMode.BLOFIN_DEMO) is None

    assert (
        resolve_autonomous_mode(
            _view(
                autonomous_internal_paper_enabled=True,
                autonomous_blofin_demo_enabled=True,
            )
        ).refusal
        == "autonomous_mode_ambiguous"
    )
    assert (
        resolve_autonomous_mode(
            _view(
                autonomous_internal_paper_enabled=True,
                exchange_mode="paper_exchange_demo",
            )
        ).refusal
        == "autonomous_paper_unverified"
    )
    assert (
        resolve_autonomous_mode(
            _view(
                autonomous_blofin_demo_enabled=True,
                exchange_mode="paper_internal",
            )
        ).refusal
        == "autonomous_demo_unverified"
    )
    assert resolve_autonomous_mode(None).refusal == "autonomous_mode_unverified"
    assert (
        resolve_autonomous_mode(_view(execution_mode=None)).refusal == "autonomous_mode_unverified"
    )
    broken = _view()
    del broken.autonomous_blofin_demo_enabled
    assert resolve_autonomous_mode(broken).refusal == "autonomous_mode_unverified"


def test_kill_switch_blocks_and_unreadable_state_fails_closed() -> None:
    org = uuid4()
    assert kill_switch_refusal(_view(), None, org, observed=True) == "kill_switch_active"
    assert (
        kill_switch_refusal(_view(global_kill_switch_active=True), None, org, observed=False)
        == "kill_switch_active"
    )
    assert kill_switch_refusal(None, None, org, observed=False) == "kill_switch_unverified"
    assert (
        kill_switch_refusal(_view(global_kill_switch_active=None), None, org, observed=False)
        == "kill_switch_unverified"
    )
    assert kill_switch_refusal(_view(), None, org, observed=False) == "kill_switch_unverified"
    assert kill_switch_refusal(_view(), None, None, observed=None) == "kill_switch_unverified"

    class _Down:
        def scalar(self, _statement: object) -> object:
            raise RuntimeError("kill switch store unavailable")

    assert (
        kill_switch_refusal(_view(), _Down(), org, observed=None) == "kill_switch_unverified"  # type: ignore[arg-type]
    )

    class _Active:
        def scalar(self, _statement: object) -> object:
            return SimpleNamespace(active=True)

    assert kill_switch_refusal(_view(), _Active(), org, observed=False) == "kill_switch_active"  # type: ignore[arg-type]
    assert kill_switch_refusal(_view(), _Active(), org, observed=None) == "kill_switch_active"  # type: ignore[arg-type]

    class _Idle:
        def scalar(self, _statement: object) -> object:
            return None

    assert kill_switch_refusal(_view(), _Idle(), org, observed=False) is None  # type: ignore[arg-type]


def test_demo_auto_approval_stays_inside_configured_risk_limits() -> None:
    assert configured_risk_refusal(_limits()) is None
    assert configured_risk_refusal(_limits(pending_notional=Decimal("251"))) == "max_position_size"
    assert (
        configured_risk_refusal(
            _limits(
                max_symbol_notional=Decimal("1000"),
                symbol_reserved={"BTCUSDT": "900"},
                pending_notional=Decimal("200"),
            )
        )
        == "max_position_size"
    )
    over_exposure = _limits(max_symbol_notional=Decimal("5000"), pending_notional=Decimal("1001"))
    assert configured_risk_refusal(over_exposure) == "max_exposure"
    assert (
        configured_risk_refusal(
            _limits(
                max_symbol_notional=Decimal("5000"),
                reserved_notional=Decimal("950"),
                pending_notional=Decimal("100"),
            )
        )
        == "max_exposure"
    )
    assert configured_risk_refusal(_limits(proposed_loss=Decimal("101"))) == "daily_loss_limit"
    over_daily_loss = _limits(actual_daily_loss=Decimal("95"), proposed_loss=Decimal("10"))
    assert configured_risk_refusal(over_daily_loss) == "daily_loss_limit"
    assert configured_risk_refusal(_limits(daily_locked=True)) == "daily_loss_limit"
    assert configured_risk_refusal(None) == "risk_limits_unverified"
    assert configured_risk_refusal(_limits(max_notional=Decimal("NaN"))) == "risk_limits_unverified"
    assert configured_risk_refusal(_limits(symbol_reserved={"BTCUSDT": "nope"})) == (
        "risk_limits_unverified"
    )


def test_disarmed_loops_do_not_auto_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)
    paper = AutomatedPaperLoop(None, settings, SimpleNamespace())  # type: ignore[arg-type]
    assert paper._posture_refusal() == "autonomous_paper_disarmed"

    revision_id = uuid4()
    candidate = SimpleNamespace(candidate_id=uuid4())
    target = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())
    envelope = SimpleNamespace(
        plan=SimpleNamespace(execution_venue="BLOFIN_DEMO", execution_policy_version=DEMO_POLICY)
    )
    monkeypatch.setattr(
        GovernedBloFinDemoLoop,
        "_plan_service",
        lambda _self: SimpleNamespace(get_scoped=lambda *_args, **_kwargs: envelope),
    )

    def _forbid_approval(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("disarmed demo mode must not create an approval")

    monkeypatch.setattr(
        "app.services.governed_blofin_demo.ApprovalService",
        _forbid_approval,
    )

    class _NoClosedLifecycle:
        def scalar(self, _statement: object) -> None:
            return None

    demo = GovernedBloFinDemoLoop(None, settings, SimpleNamespace(now=lambda: None))
    proof = demo._execute_existing(
        _NoClosedLifecycle(),  # type: ignore[arg-type]
        target=target,  # type: ignore[arg-type]
        candidate=candidate,  # type: ignore[arg-type]
        revision_id=revision_id,
        account_id=uuid4(),
    )
    assert proof.reason_code == "autonomous_demo_disarmed"
    assert proof.stage == "blocked"


def test_watcher_kill_switch_blocks_even_when_the_mode_is_disarmed() -> None:
    settings = Settings(_env_file=None)
    candidate_id = uuid4()
    candidate = SimpleNamespace(candidate_id=candidate_id)
    report = WatcherPaperScanReport(
        organization_id=uuid4(),
        scan_scope="scope",
        symbol="BTCUSDT",
        status="succeeded",
        reason_code="fixture",
        replayed=False,
        published=True,
        candidate_ids=(candidate_id,),
        kill_switch_active=True,
        discussion=SimpleNamespace(candidate=candidate, assessment=None, window=None),  # type: ignore[arg-type]
    )
    runtime = SimpleNamespace(
        _canonical_runtime=object(),
        _settings=settings,
        _eval_clock=SimpleNamespace(now=lambda: None),
        _clock=SimpleNamespace(now=lambda: None),
    )
    evidence = SimpleNamespace(last_assembly=lambda: (object(), object()))
    target = SimpleNamespace(organization_id=report.organization_id, scan_scope="scope")
    continued = WatcherPaperRuntime._continue_paper_loop(
        runtime,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        target,  # type: ignore[arg-type]
        report,
        evidence,  # type: ignore[arg-type]
    )
    assert continued.paper_loop_stage == "blocked"
    assert continued.paper_loop_reason == "kill_switch_active"

    report_off = WatcherPaperScanReport(
        organization_id=report.organization_id,
        scan_scope="scope",
        symbol="BTCUSDT",
        status="succeeded",
        reason_code="fixture",
        replayed=False,
        published=True,
        candidate_ids=(candidate_id,),
        kill_switch_active=False,
        discussion=report.discussion,
    )
    disarmed = WatcherPaperRuntime._continue_paper_loop(
        runtime,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        target,  # type: ignore[arg-type]
        report_off,
        evidence,  # type: ignore[arg-type]
    )
    assert disarmed.paper_loop_reason == "autonomous_mode_disarmed"
