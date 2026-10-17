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
    assert (
        resolve_autonomous_mode(
            _view(autonomous_internal_paper_enabled=True, execution_mode="read_only")
        ).refusal
        == "autonomous_paper_unverified"
    )
    assert (
        resolve_autonomous_mode(
            _view(
                autonomous_blofin_demo_enabled=True,
                exchange_mode="paper_exchange_demo",
                execution_mode="read_only",
            )
        ).refusal
        == "autonomous_demo_unverified"
    )
    assert (
        resolve_autonomous_mode(
            _view(environment="production", autonomous_internal_paper_enabled=True)
        ).refusal
        == "autonomous_production_refused"
    )
    assert (
        resolve_autonomous_mode(
            _view(
                environment="production",
                autonomous_blofin_demo_enabled=True,
                exchange_mode="paper_exchange_demo",
            )
        ).refusal
        == "autonomous_production_refused"
    )


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
    assert (
        configured_risk_refusal(_limits(max_trade_slots=1, actual_trade_count=1))
        == "daily_trade_limit"
    )
    assert (
        configured_risk_refusal(_limits(max_trade_slots=2, reserved_trade_slots=2))
        == "daily_trade_limit"
    )
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


def test_trade_slot_refusal_is_distinct_from_exposure() -> None:
    assert configured_risk_refusal(_limits(max_trade_slots=0, trade_slots=1)) == "daily_trade_limit"


def test_missing_ledger_row_uses_stored_risk_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RiskSettings:
        def __init__(self, _session: object, _audit: object) -> None:
            pass

        def get(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                default_account_balance=Decimal("10000"),
                daily_loss_limit=Decimal("250"),
                max_trades_per_day=3,
            )

    monkeypatch.setattr("app.services.risk.settings_service.RiskSettingsService", _RiskSettings)

    class _NoLedger:
        def scalar(self, _statement: object) -> None:
            return None

    from app.services.governed_blofin_demo import _demo_risk_snapshot

    snapshot = _demo_risk_snapshot(
        _NoLedger(),  # type: ignore[arg-type]
        SimpleNamespace(organization_id=uuid4(), user_id=uuid4()),  # type: ignore[arg-type]
        SimpleNamespace(account_id=uuid4()),
        SimpleNamespace(
            pending_notional=Decimal("10"),
            daily_loss=Decimal("1"),
            instrument="BTCUSDT",
            trade_slots=1,
        ),
    )
    assert snapshot is not None
    assert snapshot.max_notional == Decimal("10000")
    assert snapshot.max_daily_loss == Decimal("250")
    assert snapshot.max_symbol_notional == Decimal("10000")
    assert snapshot.max_trade_slots == 3
    assert snapshot.pending_notional == Decimal("10")
    assert snapshot.reserved_notional == Decimal("0")
    assert snapshot.daily_locked is False


def _forbid_approval(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("this failure must not create an approval")


def _armed_demo_view() -> SimpleNamespace:
    return _view(
        autonomous_blofin_demo_enabled=True,
        exchange_mode="paper_exchange_demo",
        environment="staging",
    )


def test_limit_snapshot_error_blocks_the_demo_order(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("limit snapshot unavailable")

    monkeypatch.setattr("app.services.execution_claim.conservative_reservation", _raise)
    monkeypatch.setattr("app.services.governed_blofin_demo.ApprovalService", _forbid_approval)
    proof = _run_disarmed_style_demo(_armed_demo_view())
    assert proof.stage == "blocked"
    assert proof.reason_code == "risk_limits_unverified"


def test_experiment_budget_error_blocks_the_demo_order(monkeypatch: pytest.MonkeyPatch) -> None:
    intent = SimpleNamespace(
        pending_notional=Decimal("10"),
        daily_loss=Decimal("1"),
        instrument="BTCUSDT",
        trade_slots=1,
    )
    monkeypatch.setattr(
        "app.services.execution_claim.conservative_reservation",
        lambda *_args, **_kwargs: intent,
    )
    monkeypatch.setattr(
        "app.services.governed_blofin_demo._demo_risk_snapshot",
        lambda *_args, **_kwargs: _limits(),
    )

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("experiment budget unavailable")

    monkeypatch.setattr("app.experiments.execution_risk.experiment_reservation_refusal", _raise)
    monkeypatch.setattr("app.services.governed_blofin_demo.ApprovalService", _forbid_approval)
    proof = _run_disarmed_style_demo(_armed_demo_view())
    assert proof.stage == "blocked"
    assert proof.reason_code == "risk_limits_unverified"


def test_kill_switch_after_auto_approval_blocks_before_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, autonomous_internal_paper_enabled=True)
    captured: dict[str, object] = {}

    class _Kill:
        active = False

        def scalar(self, _statement: object) -> SimpleNamespace:
            return SimpleNamespace(active=self.active)

    kill = _Kill()

    authorization_id = uuid4()

    class _Approval:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def create_for_plan_revision(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(id=uuid4())

        def decide(self, _approval_id: object, _decision: object, **kwargs: object) -> object:
            captured.update(kwargs)
            kill.active = True
            return SimpleNamespace(authorization=SimpleNamespace(authorization_id=authorization_id))

        def revoke_authorization(self, revoked_id: object, **kwargs: object) -> None:
            captured["revoked_id"] = revoked_id
            captured["revoke_organization_id"] = kwargs.get("organization_id")
            captured["revoke_user_id"] = kwargs.get("user_id")
            captured["revoke_reason"] = kwargs.get("reason")

    monkeypatch.setattr("app.services.automated_paper_loop.ApprovalService", _Approval)
    monkeypatch.setattr(
        "app.services.automated_paper_loop.ExecutionService",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("claim must not run after the kill switch turns on")
        ),
    )
    loop = AutomatedPaperLoop(None, settings, SimpleNamespace(now=lambda: None))  # type: ignore[arg-type]
    loop._plan_service = lambda: SimpleNamespace(get_scoped=lambda *_a, **_k: SimpleNamespace())  # type: ignore[method-assign]
    proof = loop._execute_existing(
        kill,  # type: ignore[arg-type]
        target=SimpleNamespace(organization_id=uuid4(), user_id=uuid4()),  # type: ignore[arg-type]
        candidate=SimpleNamespace(candidate_id=uuid4()),  # type: ignore[arg-type]
        revision_id=uuid4(),
        account_id=uuid4(),
        eligibility_id=uuid4(),
        eligibility_state="eligible",
    )
    assert proof.stage == "blocked"
    assert proof.reason_code == "kill_switch_active"
    channel = captured["channel"]
    assert getattr(channel, "value", None) == "AUTONOMOUS"
    assert captured["actor_type"] == "SYSTEM"
    meta = captured["audit_metadata"]
    assert isinstance(meta, dict)
    assert meta["mode"] == "internal_paper"
    assert meta["policy_version"] == "at-adr-106/v1"
    assert meta["flags"]["autonomous_internal_paper_enabled"] is True
    assert meta["flags"]["autonomous_blofin_demo_enabled"] is False
    assert meta["limits"] is None
    assert captured["revoked_id"] == authorization_id
    assert captured["revoke_organization_id"] is not None
    assert captured["revoke_user_id"] is not None
    assert captured["revoke_reason"] == "post_approval_recheck"


def test_demo_auto_approval_records_system_actor_and_limit_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = _limits()
    captured: dict[str, object] = {}
    authorization_id = uuid4()
    calls = {"n": 0}

    def _refusal(
        *_args: object, **_kwargs: object
    ) -> tuple[None, RiskLimitSnapshot] | tuple[str, None]:
        calls["n"] += 1
        if calls["n"] == 1:
            return None, limits
        return "kill_switch_active", None

    class _Approval:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def create_for_plan_revision(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(id=uuid4())

        def decide(self, _approval_id: object, _decision: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace(authorization=SimpleNamespace(authorization_id=authorization_id))

        def revoke_authorization(self, revoked_id: object, **kwargs: object) -> None:
            captured["revoked_id"] = revoked_id
            captured["revoke_reason"] = kwargs.get("reason")

    monkeypatch.setattr(
        "app.services.governed_blofin_demo.GovernedBloFinDemoLoop._demo_auto_approval_refusal",
        _refusal,
    )
    monkeypatch.setattr("app.services.governed_blofin_demo.ApprovalService", _Approval)
    proof = _run_disarmed_style_demo(_armed_demo_view())
    assert proof.stage == "blocked"
    assert proof.reason_code == "kill_switch_active"
    assert getattr(captured["channel"], "value", None) == "AUTONOMOUS"
    assert captured["actor_type"] == "SYSTEM"
    meta = captured["audit_metadata"]
    assert isinstance(meta, dict)
    assert meta["actor"] == "system"
    assert meta["channel"] == "AUTONOMOUS"
    assert meta["mode"] == "blofin_demo"
    snapshot = meta["limits"]
    assert isinstance(snapshot, dict)
    assert snapshot["max_notional"] == "1000"
    assert snapshot["max_daily_loss"] == "100"
    assert snapshot["max_symbol_notional"] == "250"
    assert snapshot["instrument"] == "BTCUSDT"
    assert captured["revoked_id"] == authorization_id
    assert captured["revoke_reason"] == "post_approval_recheck"


def test_unset_daily_loss_limit_refuses_demo_auto_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RiskSettings:
        def __init__(self, _session: object, _audit: object) -> None:
            pass

        def get(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                default_account_balance=Decimal("10000"),
                daily_loss_limit=None,
                max_trades_per_day=3,
            )

    monkeypatch.setattr("app.services.risk.settings_service.RiskSettingsService", _RiskSettings)
    _patch_reservation(monkeypatch)
    monkeypatch.setattr("app.services.governed_blofin_demo.ApprovalService", _forbid_approval)
    proof = _run_disarmed_style_demo(_armed_demo_view())
    assert proof.stage == "blocked"
    assert proof.reason_code == "risk_limits_unverified"


def test_ledger_daily_loss_at_notional_refuses_demo_auto_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_reservation(monkeypatch)
    monkeypatch.setattr("app.services.governed_blofin_demo.ApprovalService", _forbid_approval)
    proof = _run_disarmed_style_demo(
        _armed_demo_view(),
        session=_ledger_session(_ledger_row(max_daily_loss=Decimal("1000"))),
    )
    assert proof.stage == "blocked"
    assert proof.reason_code == "risk_limits_unverified"


def test_ledger_symbol_cap_at_notional_refuses_demo_auto_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_reservation(monkeypatch)
    monkeypatch.setattr("app.services.governed_blofin_demo.ApprovalService", _forbid_approval)
    proof = _run_disarmed_style_demo(
        _armed_demo_view(),
        session=_ledger_session(_ledger_row(max_symbol_notional=Decimal("1000"))),
    )
    assert proof.stage == "blocked"
    assert proof.reason_code == "risk_limits_unverified"
    from app.services.governed_blofin_demo import _demo_risk_snapshot

    healthy = _demo_risk_snapshot(
        _ledger_session(_ledger_row(), always=True),  # type: ignore[arg-type]
        SimpleNamespace(organization_id=uuid4(), user_id=uuid4()),  # type: ignore[arg-type]
        SimpleNamespace(account_id=uuid4()),
        SimpleNamespace(
            pending_notional=Decimal("10"),
            daily_loss=Decimal("1"),
            instrument="BTCUSDT",
            trade_slots=1,
        ),
    )
    assert healthy is not None
    assert healthy.max_daily_loss == Decimal("100")
    assert healthy.max_symbol_notional == Decimal("250")


def test_journal_payload_copies_the_autonomous_approval_channel() -> None:
    from app.schemas.trade_plan import AuthorizationChannel
    from app.services.canonical_paper_execution import _attach_approval_channel

    authorization_id = uuid4()

    class _Session:
        def get(self, _model: object, key: object) -> SimpleNamespace | None:
            if key != authorization_id:
                return None
            return SimpleNamespace(channel=AuthorizationChannel.AUTONOMOUS)

    payload: dict[str, object] = {}
    _attach_approval_channel(_Session(), payload, authorization_id)  # type: ignore[arg-type]
    assert payload["approval_channel"] == "AUTONOMOUS"
    untouched: dict[str, object] = {}
    _attach_approval_channel(_Session(), untouched, uuid4())  # type: ignore[arg-type]
    assert "approval_channel" not in untouched


def _patch_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.execution_claim.conservative_reservation",
        lambda *_args, **_kwargs: SimpleNamespace(
            pending_notional=Decimal("10"),
            daily_loss=Decimal("1"),
            instrument="BTCUSDT",
            trade_slots=1,
        ),
    )


def _ledger_row(**updates: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "max_notional": Decimal("1000"),
        "max_daily_loss": Decimal("100"),
        "max_symbol_notional": Decimal("250"),
        "max_trade_slots": 5,
        "reserved_notional": Decimal("0"),
        "reserved_daily_loss": Decimal("0"),
        "reserved_trade_slots": 0,
        "actual_notional": Decimal("0"),
        "actual_daily_loss": Decimal("0"),
        "actual_trade_count": 0,
        "symbol_reserved": {},
        "symbol_actual": {},
        "daily_locked": False,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _ledger_session(row: SimpleNamespace, *, always: bool = False) -> object:
    class _Session:
        def __init__(self) -> None:
            self.calls = 0

        def scalar(self, _statement: object) -> SimpleNamespace | None:
            self.calls += 1
            if always or self.calls >= 3:
                return row
            return None

    return _Session()


def _run_disarmed_style_demo(settings: object, session: object | None = None):  # type: ignore[no-untyped-def]
    revision_id = uuid4()
    candidate = SimpleNamespace(candidate_id=uuid4())
    target = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())
    envelope = SimpleNamespace(
        plan=SimpleNamespace(execution_venue="BLOFIN_DEMO", execution_policy_version=DEMO_POLICY)
    )
    demo = GovernedBloFinDemoLoop(None, settings, SimpleNamespace(now=lambda: None))  # type: ignore[arg-type]
    demo._plan_service = lambda: SimpleNamespace(  # type: ignore[method-assign]
        get_scoped=lambda *_args, **_kwargs: envelope
    )

    class _Session:
        def __init__(self) -> None:
            self.calls = 0

        def scalar(self, _statement: object) -> None:
            self.calls += 1
            return None

    return demo._execute_existing(
        _Session() if session is None else session,  # type: ignore[arg-type]
        target=target,  # type: ignore[arg-type]
        candidate=candidate,  # type: ignore[arg-type]
        revision_id=revision_id,
        account_id=uuid4(),
    )
