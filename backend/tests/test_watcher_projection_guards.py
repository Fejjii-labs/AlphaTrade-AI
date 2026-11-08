"""Opt-in watchlist projection stays observation-only and scoped to its own market.

Each test names the guard it protects. Synthetic evidence, SQLite, no network.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.candidate_alerts.nested import NestedAlertSummary
from app.candidate_alerts.sfp import SfpAlertSummary
from app.core.config import ExchangeMode
from app.db.canonical_eligibility import ActionEligibilityEvaluationRow
from app.db.models import ExchangeOrder, ExecutionCommand, Membership, Order
from app.db.strategy_brain import BrainSetupRow
from app.schemas.common import MembershipRole
from app.schemas.nested_continuation import EvidenceAvailability, NestedContinuationSpec
from app.schemas.telegram_policy import NotificationEventType
from app.services.automated_paper_loop import AutomatedPaperLoop
from app.services.canonical_strategy_evaluation import (
    project_executable_to_market,
    resolve_executable_strategy_policy,
)
from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
from app.services.watcher_assignment_service import assignment_target_reason
from app.signal_fusion.errors import StrategyEvaluationPolicyError
from app.strategy_brain.sfp_runtime.notifications import sfp_notification_summaries
from app.strategy_brain.sfp_runtime.records import record_sfp_availability
from app.workers import watcher_paper as watcher_paper_module
from app.workers.watcher_paper import WatcherPaperRuntime
from app.workers.watcher_paper_targets import list_paper_scan_targets, list_watchlist_scan_targets
from tests.support.phase6_evaluator import make_world
from tests.test_sfp_detector import evidence
from tests.test_sfp_strategy_brain_runtime import approve as approve_sfp
from tests.test_sfp_strategy_brain_runtime import persist
from tests.test_sfp_strategy_brain_runtime import store as sfp_store
from tests.test_strategy_brain_nested import approved
from tests.test_strategy_brain_nested import tenant_store as nested_store
from tests.test_watcher_paper_runtime import (
    ORG,
    _publish_runtime,
    _publish_scan,
    _resolve,
    _runtime,
    _seed_approved_compiled,
    _settings,
    _world_factory,
)
from tests.test_watcher_paper_runtime import session_factory as runtime_session_factory

session_factory = runtime_session_factory
tenant_store = nested_store
store = sfp_store
WATCHLIST = ["BTCUSDT", "ZECUSDT", "ETHUSDT", "TAOUSDT", "HYPEUSDT"]


def _proof(candidate_id):
    return SimpleNamespace(
        stage="blocked",
        reason_code="governed_human_path",
        replayed=False,
        candidate_id=candidate_id,
        eligibility_id=None,
        eligibility_state=None,
        trade_plan_revision_id=None,
        execution_command_id=None,
        paper_fill_id=None,
        journal_trade_id=None,
        journal_status=None,
    )


def _order_rows(factory) -> dict[str, int]:
    with factory() as db:
        return {
            model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (ActionEligibilityEvaluationRow, ExecutionCommand, Order, ExchangeOrder)
        }


# --- B2: a projected target never reaches an order path ------------------------


@pytest.mark.parametrize("projected", [False, True])
def test_projected_target_never_enters_the_paper_or_demo_order_path(
    session_factory, monkeypatch, projected
) -> None:
    """Fails if the ``if target.market_projection`` guard in _scan_one_isolated is removed."""

    with session_factory() as db:
        _seed_approved_compiled(db)
    world_factory = _world_factory(make_world())

    def evidence_factory(session, store, symbol):
        port = world_factory(session, store, symbol)
        # Lets the real _continue_paper_loop reach the governed demo branch.
        port.last_assembly = lambda: (object(), SimpleNamespace(authored_spec=None))
        return port

    def targets(session):
        listed = list_paper_scan_targets(session, symbols=("BTCUSDT",), limit=5)
        if not projected:
            return listed
        return tuple(
            replace(t, market_projection=True, projection_source="BTCUSDT 15m") for t in listed
        )

    calls: list[str] = []
    real_continue = WatcherPaperRuntime._continue_paper_loop

    def spy_continue(self, *args):
        calls.append("paper_loop")
        return real_continue(self, *args)

    def spy_demo(self, _session, **kwargs):
        calls.append("governed_demo")
        return _proof(kwargs["candidate"].candidate_id)

    def spy_internal(self, *_args, **kwargs):
        calls.append("internal_paper")
        return _proof(kwargs["candidate"].candidate_id)

    monkeypatch.setattr(WatcherPaperRuntime, "_continue_paper_loop", spy_continue)
    monkeypatch.setattr(GovernedBloFinDemoLoop, "continue_confirmed_setup", spy_demo)
    monkeypatch.setattr(AutomatedPaperLoop, "continue_confirmed_setup", spy_internal)
    runtime, _, probe, _ = _runtime(
        session_factory, target_loader=targets, evidence_factory=evidence_factory
    )
    # Demo exchange with autonomy off: a native confirmed setup enters the paper
    # loop, which refuses it (M1: OFF places no worker demo order).
    runtime._settings = runtime._settings.model_copy(
        update={"exchange_mode": ExchangeMode.PAPER_EXCHANGE_DEMO}
    )
    runtime._canonical_runtime = object()

    report = runtime.run_cycle()

    (scan,) = report.scans
    assert len(scan.candidate_ids) == 1, "the setup confirmed, so a Candidate is recorded"
    if not projected:
        # Control: the native scope reaches the paper loop (and OFF refuses it).
        assert calls == ["paper_loop"]
        assert scan.paper_loop_reason == "autonomous_mode_disarmed"
        return
    assert calls == []
    assert scan.market_projection is True
    assert scan.projection_source == "BTCUSDT 15m"
    assert scan.paper_loop_stage == "not_applicable"
    assert scan.eligibility_id is None and scan.execution_command_id is None
    assert probe.execution == []
    assert _order_rows(session_factory) == {
        "action_eligibility_evaluations": 0,
        "execution_commands": 0,
        "orders": 0,
        "exchange_orders": 0,
    }


def test_paper_loop_refuses_a_projected_target_on_its_own() -> None:
    """Second guard: even a direct call cannot continue a projected scope."""

    candidate = SimpleNamespace(candidate_id=uuid4())
    report = watcher_paper_module.WatcherPaperScanReport(
        organization_id=uuid4(),
        scan_scope="scope",
        symbol="ETHUSDT",
        status="succeeded",
        reason_code="fixture",
        replayed=False,
        published=True,
        candidate_ids=(candidate.candidate_id,),
        kill_switch_active=False,
        discussion=SimpleNamespace(candidate=candidate, assessment=None, window=None),
    )
    runtime = SimpleNamespace(
        _canonical_runtime=object(),
        _settings=_settings(),
        _clock=SimpleNamespace(now=lambda: None),
    )
    target = SimpleNamespace(organization_id=report.organization_id, market_projection=True)
    evidence_port = SimpleNamespace(last_assembly=pytest.fail)
    continued = WatcherPaperRuntime._continue_paper_loop(
        runtime, object(), target, report, evidence_port
    )
    assert continued.paper_loop_stage == "blocked"
    assert continued.paper_loop_reason == "market_projection_observation_only"


@pytest.mark.parametrize("flag", [False, True])
def test_runtime_passes_project_timeframes_only_when_the_flag_is_on(
    session_factory, monkeypatch, flag
) -> None:
    """Fails if WatcherPaperRuntime ignores WATCHER_APPLY_STRATEGIES_TO_WATCHLIST."""

    seen: list[tuple[str, ...]] = []

    def capture(_session, **kwargs):
        seen.append(tuple(kwargs["project_timeframes"]))
        return ()

    monkeypatch.setattr(watcher_paper_module, "list_watchlist_scan_targets", capture)
    runtime, _, _, _ = _runtime(session_factory)
    runtime._settings = _settings(
        watcher_apply_strategies_to_watchlist=flag, watcher_scan_timeframes="15m,4h"
    )
    runtime._watchlist_mode = True
    with session_factory() as db:
        runtime._load_targets(db)
    assert seen == [("15m", "4h") if flag else ()]


# --- C1: projected scopes are not monitor-polled -------------------------------


def test_projected_scope_is_not_monitor_polled(session_factory) -> None:
    """Fails if projected scopes poll the monitor (aggTrades weight 20 per symbol)."""

    runtime, _, polled = _publish_runtime(session_factory)
    projected = replace(_publish_scan("ETHUSDT", scope="p"), market_projection=True)
    runtime._publish_monitor_snapshots(_resolve(runtime, projected))
    assert polled == []
    # Control: a native scope on the same symbol is polled.
    runtime._publish_monitor_snapshots(_resolve(runtime, _publish_scan("ETHUSDT", scope="n")))
    assert polled == ["ETHUSDT"]


# --- C1: only SFP and Nested can be projected ----------------------------------


def test_first_slice_strategies_are_never_projected(session_factory) -> None:
    with session_factory() as db:
        _, version_id = _seed_approved_compiled(db)
        executable = resolve_executable_strategy_policy(
            db, organization_id=ORG, strategy_version_id=version_id
        )
        with pytest.raises(StrategyEvaluationPolicyError, match="Only SFP and Nested") as refused:
            project_executable_to_market(executable, symbol="ETHUSDT", timeframe="1h")
        assert refused.value.reason_code == "market_projection_unsupported"
        targets = list_watchlist_scan_targets(
            db, symbols=WATCHLIST, limit=20, project_timeframes=("15m", "1h")
        )
        assert [(t.symbol, t.market_projection) for t in targets] == [("BTCUSDT", False)]


def test_trendpulse_is_never_projected() -> None:
    """TrendPulse would validate on another symbol at 5m, so only the family check refuses it."""

    from app.strategy_brain.trendpulse_1r.contracts import TrendPulseSpec

    executable = SimpleNamespace(authored_spec=TrendPulseSpec(symbol="BTCUSDT", direction="long"))
    with pytest.raises(StrategyEvaluationPolicyError, match="Only SFP and Nested"):
        project_executable_to_market(executable, symbol="ETHUSDT", timeframe="5m")


def test_sfp_strategies_project_like_nested(store) -> None:
    session, org, user, _ = store
    policy = approve_sfp(session, org, user)
    projected = project_executable_to_market(policy, symbol="ETHUSDT", timeframe="1h")
    assert (projected.authored_spec.symbol, projected.authored_spec.trigger_timeframe.value) == (
        "ETHUSDT",
        "1h",
    )
    assert projected.compiled_content_hash == policy.compiled_content_hash
    assert projected.authored_spec.parameters == policy.authored_spec.parameters


# --- C1: assignment_target_reason re-validates projected targets ---------------


def test_projected_inferred_target_passes_the_fresh_authority_check(tenant_store) -> None:
    """Fails if assignment_target_reason drops its projection branch."""

    session, org, user = tenant_store
    session.add(Membership(organization_id=org, user_id=user, role=MembershipRole.OWNER))
    session.commit()
    approved(session, org, user, NestedContinuationSpec(symbol="BTCUSDT"))
    targets = list_watchlist_scan_targets(
        session, symbols=WATCHLIST, organization_id=org, limit=20, project_timeframes=("1h",)
    )
    targets = tuple(replace(t, configuration_revision=0) for t in targets)
    native = next(t for t in targets if not t.market_projection)
    projected = next(t for t in targets if t.symbol == "ETHUSDT" and t.timeframe == "1h")
    assert assignment_target_reason(session, native) is None
    assert assignment_target_reason(session, projected) is None
    # A projected target whose lineage no longer matches is still refused.
    forged = replace(projected, compiled_content_hash="0" * 64)
    assert assignment_target_reason(session, forged) == "strategy_binding_unavailable"


# --- C1/C3: SFP rows stay on their own symbol and timeframe --------------------


def _sfp_with_btc_setup(store):
    session, org, user, _ = store
    policy = approve_sfp(session, org, user)
    bars, obs = evidence()
    _, setup_id = persist(session, policy, bars, obs)
    row = session.get(BrainSetupRow, setup_id)
    assert (row.symbol, row.payload["timeframe"]) == ("BTCUSDT", "15m")
    return session, policy, row, obs[-1].observed_at


@pytest.mark.parametrize(("symbol", "timeframe"), [("ETHUSDT", "15m"), ("BTCUSDT", "1h")])
def test_projected_sfp_availability_does_not_touch_other_markets(store, symbol, timeframe) -> None:
    """Fails if record_sfp_availability drops its symbol (M13) or timeframe filter."""

    session, policy, row, at = _sfp_with_btc_setup(store)
    before = dict(row.payload)
    projected = project_executable_to_market(policy, symbol=symbol, timeframe=timeframe)
    record_sfp_availability(
        session,
        executable=projected,
        availability=EvidenceAvailability.MISSING,
        reason_codes=("required_sfp_evidence_unavailable",),
        evaluated_at=at + timedelta(seconds=1),
        evidence_hash="unavailable",
    )
    session.commit()
    session.refresh(row)
    assert row.payload == before
    # Control: the native market's own availability is still recorded.
    record_sfp_availability(
        session,
        executable=policy,
        availability=EvidenceAvailability.MISSING,
        reason_codes=("required_sfp_evidence_unavailable",),
        evaluated_at=at + timedelta(seconds=1),
        evidence_hash="unavailable",
    )
    session.commit()
    session.refresh(row)
    assert row.payload["required_evidence"] == "MISSING"


def test_sfp_summaries_are_filtered_by_symbol_and_timeframe(store) -> None:
    session, policy, row, at = _sfp_with_btc_setup(store)

    def summaries(**scope):
        return sfp_notification_summaries(
            session,
            organization_id=policy.organization_id,
            strategy_version_id=policy.strategy_version_id,
            now=at,
            **scope,
        )

    assert summaries() and {s.setup_id for s in summaries()} == {row.id}
    assert {s.setup_id for s in summaries(symbol="BTCUSDT", timeframe="15m")} == {row.id}
    assert summaries(symbol="ETHUSDT", timeframe="15m") == ()
    assert summaries(symbol="BTCUSDT", timeframe="1h") == ()


def test_runtime_scopes_sfp_summaries_and_labels_projected_alerts(
    session_factory, monkeypatch
) -> None:
    from app.strategy_brain.sfp.contracts import SFP_KIND
    from app.strategy_brain.sfp_runtime import notifications

    with session_factory() as db:
        _seed_approved_compiled(db)
    seen: list[dict[str, object]] = []

    def fake_summaries(_session, **kwargs):
        seen.append(kwargs)
        return (_sfp_summary(symbol=kwargs["symbol"], timeframe=kwargs["timeframe"]),)

    monkeypatch.setattr(notifications, "sfp_notification_summaries", fake_summaries)

    def targets(session):
        return tuple(
            replace(
                t,
                symbol="BTCUSDT",
                fusion_policy_version=SFP_KIND,
                market_projection=True,
                projection_source="BTCUSDT 1h",
            )
            for t in list_paper_scan_targets(session, symbols=("BTCUSDT",), limit=5)
        )

    runtime, _, _, _ = _runtime(session_factory, target_loader=targets)
    (scan,) = runtime.run_cycle().scans
    assert seen and (seen[0]["symbol"], seen[0]["timeframe"]) == ("BTCUSDT", "15m")
    assert [a.projection_source for a in scan.sfp_alerts] == ["BTCUSDT 1h"]
    assert "(watchlist scan, BTCUSDT 1h params)" in scan.sfp_alerts[0].text()


# --- C3/C4: alert text names the reused parameters -----------------------------


def _sfp_summary(**updates) -> SfpAlertSummary:
    now = datetime(2026, 11, 2, 10, tzinfo=UTC)
    values: dict[str, object] = {
        "organization_id": uuid4(),
        "strategy_version_id": uuid4(),
        "setup_id": uuid4(),
        "event_type": NotificationEventType.SFP_CONFIRMED,
        "occurred_at": now,
        "evidence_at": now,
        "symbol": "TAOUSDT",
        "venue": "binance",
        "timeframe": "1h",
        "direction": "long",
        "level_type": "swing_low",
        "structural_level": "300",
        "sweep_extreme": "295",
        "reclaim_state": "reclaimed",
        "quality_coverage": (),
        "missing_evidence": (),
        "risk_state": "not_evaluated",
    }
    values.update(updates)
    return SfpAlertSummary(**values)


@pytest.mark.parametrize(
    "event_type", [NotificationEventType.SFP_CONFIRMED, NotificationEventType.SFP_LEVEL_APPROACH]
)
def test_projected_sfp_alert_names_the_reused_params(event_type) -> None:
    native = _sfp_summary(event_type=event_type)
    projected = native.model_copy(update={"projection_source": "BTCUSDT 15m"})
    lines = projected.text().splitlines()
    assert lines[-2] == "Source: (watchlist scan, BTCUSDT 15m params)"
    assert "approved for BTCUSDT 15m" in lines[-1] and "TAOUSDT 1h" in lines[-1]
    assert "watchlist scan" not in native.text()
    # Same outbox identity: the label never forks the duplicate key.
    assert projected.notification_event().duplicate_key == native.notification_event().duplicate_key


def test_projected_nested_alert_names_the_reused_params() -> None:
    from app.candidate_alerts.content import format_candidate_alert_text

    nested = NestedAlertSummary(
        organization_id=uuid4(),
        strategy_version_id=uuid4(),
        setup_id=uuid4(),
        symbol="ETHUSDT",
        stage="confirmed",
        evidence_at=datetime(2026, 11, 2, 10, tzinfo=UTC),
        decision="not_evaluated",
        risk_state="not_evaluated",
        reasons=(),
    )
    content = SimpleNamespace(
        nested=nested.model_copy(update={"projection_source": "BTCUSDT 15m"}),
        evidence_provenance=SimpleNamespace(venue=SimpleNamespace(value="binance")),
        trigger_context=SimpleNamespace(timeframe=SimpleNamespace(value="1h")),
        direction=SimpleNamespace(value="long"),
    )
    text = format_candidate_alert_text(content)
    assert "Source: (watchlist scan, BTCUSDT 15m params)" in text
    assert "approved for BTCUSDT 15m" in text and "ETHUSDT 1h" in text
    native = format_candidate_alert_text(SimpleNamespace(**{**vars(content), "nested": nested}))
    assert "watchlist scan" not in native


def test_tenant_status_exposes_the_projection_source(session_factory) -> None:
    from app.schemas.watcher_paper import WatcherPaperScopeStatus

    runtime, _, _, _ = _runtime(session_factory)
    scan = replace(
        _publish_scan("ETHUSDT"), market_projection=True, projection_source="BTCUSDT 15m"
    )
    report = SimpleNamespace(enabled=True, reason_code="ok", scans=(scan,))
    runtime._organization_id = scan.organization_id
    (scope,) = runtime._tenant_runtime_summary(report)["scopes"]
    assert WatcherPaperScopeStatus(**scope).projection_source == "BTCUSDT 15m"


def test_runtime_labels_a_projected_nested_alert(session_factory, monkeypatch) -> None:
    from app.strategy_brain import assembly

    with session_factory() as db:
        _seed_approved_compiled(db)
    alert = NestedAlertSummary(
        organization_id=ORG,
        strategy_version_id=uuid4(),
        setup_id=uuid4(),
        symbol="ETHUSDT",
        stage="confirmed",
        evidence_at=datetime(2026, 11, 2, 10, tzinfo=UTC),
        decision="not_evaluated",
        risk_state="not_evaluated",
        reasons=(),
    )
    monkeypatch.setattr(assembly, "record_paper_link", lambda *_a, **_k: alert)

    def targets(session):
        return tuple(
            replace(t, market_projection=True, projection_source="BTCUSDT 15m")
            for t in list_paper_scan_targets(session, symbols=("BTCUSDT",), limit=5)
        )

    runtime, _, _, _ = _runtime(session_factory, target_loader=targets)
    (scan,) = runtime.run_cycle().scans
    assert scan.nested_alert is not None
    assert scan.nested_alert.projection_source == "BTCUSDT 15m"
