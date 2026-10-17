"""Disposable PostgreSQL + MockTransport: native entry through sample statistics.

All venue mutations stop inside the in-process fixture. No runtime settings,
deployed databases or exchange endpoints are accessed.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError
from app.db.blofin_activity import BloFinActivityCursor, BloFinActivityFact
from app.db.models import AuditLog, ExecutionCommand, GovernedDemoLifecycleResolution
from app.experiments.execution_selection import select_execution_context
from app.experiments.models import ExperimentSampleRow
from app.experiments.native_closure import CLOSURE
from app.experiments.outcomes import BloFinExperimentReads
from app.providers.exchange.blofin_activity import parse_fact
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.repositories.blofin_activity import fact_hash
from app.security.tenant import TenantContext
from app.workers.watcher_paper_targets import PaperScanTarget
from tests.test_blofin_native_producer_postgres import producer_engine as producer_engine
from tests.test_governed_demo_lifecycle import ClosingVenue
from tests.test_governed_experiment_context_postgres import (
    execute,
    make_loop,
    pause,
    use_verified_persisted_symbol_cap,
)
from tests.test_governed_experiment_context_postgres import world as world


class OwnedVenue(ClosingVenue):
    terminal_unfilled = False

    def handle(self, request):
        if request.url.path.endswith("query-apikey"):
            return httpx.Response(200, json={"code": "0", "data": {"readOnly": 0, "uid": "uid-a"}})
        result = super().handle(request)
        content = result.json()
        for row in content.get("data", []) if isinstance(content.get("data"), list) else []:
            if row.get("orderId") == "demo-1":
                row.update(reduceOnly="false", tpslId="protect-1")
                if self.terminal_unfilled and request.url.path.endswith("order-detail"):
                    row.update(state="canceled", filledSize="0")
        return httpx.Response(result.status_code, json=content)


def open_owned(
    world, monkeypatch, *, partial=False, category="tp", parent="protect-1", behavior=None
):
    loop, _, _ = make_loop(world, monkeypatch)
    venue = OwnedVenue(
        Decimal("100000"),
        behavior or ("partial_cancel" if partial else "success"),
        category=category,
        child_tpsl_id=parent,
    )
    provider = GovernedBloFinDemoProvider(
        BloFinClient(
            base_url=loop._settings.blofin_demo_rest_base_url,
            api_key="mock-execution",
            api_secret="mock-secret",
            api_passphrase="mock-pass",
            transport=httpx.MockTransport(venue.handle),
            sleeper=lambda _: None,
            rate_limit_requests_per_second=100000,
        ),
        clock=lambda: world.w.now,
    )

    def submit(**kwargs):
        # Engine clocks are native milliseconds, after the committed pre-position.
        world.w.now = world.w.now.replace(microsecond=0) + timedelta(seconds=1)
        venue.quote_ts = str(int(world.w.now.timestamp() * 1000))
        return GovernedBloFinDemoProvider.submit(provider, **kwargs)

    monkeypatch.setattr(provider, "submit", submit)
    loop._provider = provider
    result = execute(world, loop)
    assert venue.post_count == 1, result
    assert result.reason_code in {
        "demo_filled_protected",
        "demo_cancelled_after_fill",
        "demo_acknowledged_unfilled",
    }, result
    command = world.session.get(ExecutionCommand, result.execution_command_id)
    return loop, venue, command


def mirror(world, venue, command, *, missing=None, unrelated=False):
    """Persist ordinary mirror facts parsed from the same mocked native receipts."""
    start = venue.quote_ts
    end = str(int(start) + 1000)
    parent = {
        **venue.order,
        "orderId": "demo-1",
        "state": "canceled" if venue.behavior == "partial_cancel" else "filled",
        "filledSize": venue.filled,
        "reduceOnly": "false",
        "createTime": start,
        "updateTime": start,
    }
    close = {
        **venue.order,
        "orderId": "exit-1",
        "clientOrderId": "venue-exit-1",
        "size": venue.filled,
        "side": venue.opposite,
        "state": "filled",
        "filledSize": venue.filled,
        "reduceOnly": "true",
        "createTime": end,
        "updateTime": end,
    }
    fills = [
        {
            "instId": venue.order["instId"],
            "orderId": "demo-1",
            "tradeId": "fill-1",
            "side": venue.order["side"],
            "positionSide": "net",
            "fillSize": venue.filled,
            "fillPrice": str(venue.price),
            "fee": "0.02",
            "feeCurrency": "USDT",
            "ts": start,
        },
        {
            "instId": venue.order["instId"],
            "orderId": "exit-1",
            "tradeId": "exit-fill-1",
            "side": venue.opposite,
            "positionSide": "net",
            "fillSize": venue.filled,
            "fillPrice": str(venue.price - 1),
            "fee": venue.exit_fee,
            "feeCurrency": venue.exit_fee_currency,
            "fillPnl": venue.gross,
            "ts": end,
        },
    ]
    facts = [
        parse_fact("order", parent),
        parse_fact("order", close),
        *[parse_fact("fill", f) for f in fills],
    ]
    if unrelated:
        facts.append(
            facts[-1].model_copy(
                update={
                    "native_id": "manual-fill",
                    "trade_id": "manual-fill",
                    "order_id": "manual-order",
                }
            )
        )
    for fact in facts:
        if fact.native_id == missing:
            continue
        world.session.add(
            BloFinActivityFact(
                organization_id=command.organization_id,
                environment="demo",
                account_uid="uid-a",
                kind=fact.kind,
                native_id=fact.native_id,
                order_id=fact.order_id,
                occurred_at_ms=int(fact.occurred_at_ms),
                payload=fact.model_dump(mode="json"),
                content_hash=fact_hash(fact),
                first_observed_at=world.w.now,
            )
        )
    world.session.add(
        BloFinActivityCursor(
            organization_id=command.organization_id,
            environment="demo",
            account_uid="uid-a",
            kind="fill",
            covered_begin_ms=int(start) - 10000,
            covered_end_ms=int(world.w.now.timestamp() * 1000),
            window_begin_ms=int(start) - 10000,
            window_end_ms=int(world.w.now.timestamp() * 1000),
            window_complete=True,
            gap_detected=False,
            last_successful_sync=world.w.now,
            seen_cursors=[],
        )
    )
    world.session.commit()


@pytest.mark.parametrize("partial", [False, True])
def test_restart_admits_once_and_exposes_verified_price_statistics(world, monkeypatch, partial):
    loop, venue, command = open_owned(world, monkeypatch, partial=partial)
    venue.exited = True
    world.w.now += timedelta(seconds=2)
    assert (
        loop.reconcile_command(world.session, command_id=command.id)
        == "demo_closed_reconciled_sample_unavailable"
    )
    assert world.session.scalar(select(func.count()).select_from(ExperimentSampleRow)) == 0
    assert (
        world.session.scalar(select(func.count()).select_from(GovernedDemoLifecycleResolution)) == 1
    )
    mirror(world, venue, command)
    # A fresh worker with no context discovers the durable, explicitly owned close.
    loop._experiment_context = None
    assert loop.reconcile_pending(world.session) == ("demo_closed_reconciled_sample_admitted",)
    requests = venue.post_count
    with world.factory() as fresh:
        assert (
            loop.reconcile_command(fresh, command_id=command.id)
            == "demo_closed_reconciled_sample_admitted"
        )
        reads = BloFinExperimentReads(
            fresh, loop._settings, world.w.tenant, clock=lambda: world.w.now
        )
        outcome = reads.outcome(
            world.version.experiment_id, world.version.id, "baseline", str(command.id)
        )
        report = reads.performance(world.version.experiment_id, world.version.id, "baseline")
        assert outcome.closure_verified and outcome.proof is not None
        assert outcome.status == "unavailable" and outcome.fee_cost is None
        assert report.sample_count == report.price_pnl_sample_count == 1
        assert report.price_pnl_excluding_fees_and_funding == Decimal(venue.filled) * Decimal(
            "0.001"
        )
        assert report.price_pnl_win_rate == 1 and report.win_rate is None
        assert report.realized_pnl_after_fees_excluding_funding is None
        assert fresh.scalar(select(func.count()).select_from(ExperimentSampleRow)) == 1
        assert (
            fresh.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.redacted_metadata["operation"].as_string() == CLOSURE)
            )
            == 1
        )
    assert venue.post_count == requests == 1


@pytest.mark.parametrize(
    "problem",
    ["missing_fill", "unrelated", "wrong_parent", "manual_close", "gap", "currency", "tamper"],
)
def test_incomplete_or_unowned_closures_do_not_admit(world, monkeypatch, problem):
    loop, venue, command = open_owned(
        world,
        monkeypatch,
        category="normal" if problem == "manual_close" else "tp",
        parent="foreign-parent" if problem == "wrong_parent" else "protect-1",
    )
    venue.exited = True
    world.w.now += timedelta(seconds=2)
    if problem == "currency":
        venue.exit_fee_currency = None
    loop.reconcile_command(world.session, command_id=command.id)
    mirror(
        world,
        venue,
        command,
        missing="exit-fill-1" if problem == "missing_fill" else None,
        unrelated=problem == "unrelated",
    )
    if problem == "gap":
        cursor = world.session.scalar(
            select(BloFinActivityCursor).where(BloFinActivityCursor.kind == "fill")
        )
        cursor.gap_detected = True
        world.session.commit()
    if problem == "tamper":
        fact = world.session.scalar(
            select(BloFinActivityFact).where(BloFinActivityFact.native_id == "exit-fill-1")
        )
        fact.payload = {**fact.payload, "price": "1"}
        world.session.commit()
    reason = loop.reconcile_command(world.session, command_id=command.id)
    count = world.session.scalar(select(func.count()).select_from(ExperimentSampleRow))
    # Missing currency cannot fabricate fee-adjusted money; owned price outcomes
    # remain independently admissible, with the raw native currency still absent.
    if problem == "currency":
        assert reason == "demo_closed_reconciled_sample_admitted" and count == 1
    else:
        assert reason == "demo_closed_reconciled_sample_unavailable" and count == 0
    assert venue.post_count == 1


def test_concurrent_admission_and_foreign_tenant_keep_one_sample(world, monkeypatch):
    loop, venue, command = open_owned(world, monkeypatch)
    venue.exited = True
    world.w.now += timedelta(seconds=2)
    loop.reconcile_command(world.session, command_id=command.id)
    mirror(world, venue, command)
    world.session.rollback()
    barrier = Barrier(2)

    def run():
        with world.factory() as fresh:
            barrier.wait(timeout=5)
            return loop.reconcile_command(fresh, command_id=command.id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        assert [f.result(timeout=20) for f in futures] == [
            "demo_closed_reconciled_sample_admitted"
        ] * 2
    assert world.session.scalar(select(func.count()).select_from(ExperimentSampleRow)) == 1
    from app.core.errors import NotFoundError

    foreign = TenantContext(
        organization_id=uuid4(),
        user_id=uuid4(),
        email="foreign@fixture.test",
        membership_role="owner",
    )
    reads = BloFinExperimentReads(world.session, loop._settings, foreign, clock=lambda: world.w.now)
    with pytest.raises(NotFoundError):
        reads.outcome(world.version.experiment_id, world.version.id, "baseline", str(command.id))


def test_exact_watcher_enrollment_pause_expiry_ambiguity_and_disarmed_gate(world, monkeypatch):
    loop, _, _ = make_loop(world, monkeypatch)
    candidate = world.plans.candidate
    target = PaperScanTarget(
        organization_id=world.w.tenant.organization_id,
        user_id=world.w.tenant.user_id,
        strategy_id=world.w.strategy.id,
        strategy_version_id=candidate.strategy_version_id,
        compiled_setup_definition_id=candidate.setup_definition_id,
        compiled_content_hash=candidate.executable_setup.content_hash,
        fusion_policy_version=candidate.fusion_policy_version,
        symbol="BTCUSDT",
        timeframe=world.envelope.plan.timeframe,
    )
    settings = loop._settings
    # The foundation intentionally retains two running versions. Exact matches
    # must refuse until the older enrollment is explicitly paused.
    from app.experiments.models import ExperimentVersionRow
    from app.schemas.experiments import ExperimentTransition

    assert select_execution_context(world.session, settings, target, world.w.now) == (
        None,
        "experiment_execution_ambiguous",
    )
    for row in world.session.scalars(
        select(ExperimentVersionRow).where(ExperimentVersionRow.id != world.version.id)
    ):
        world.w.service.transition(
            world.w.tenant,
            row.experiment_id,
            row.id,
            ExperimentTransition(action="pause", expected_revision=row.revision),
        )
    world.session.commit()
    selected, reason = select_execution_context(world.session, settings, target, world.w.now)
    assert selected.version_id == world.version.id and selected.variant_key == "baseline"
    assert reason is None
    from types import SimpleNamespace

    from app.services.automated_paper_loop import _proof
    from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
    from app.workers.watcher_paper import WatcherPaperRuntime, WatcherPaperScanReport

    calls = []

    def coordinator(self, session, **kwargs):
        calls.append(self._experiment_context)
        return _proof(kwargs["candidate"], "blocked", "fixture_coordinator")

    monkeypatch.setattr(GovernedBloFinDemoLoop, "continue_confirmed_setup", coordinator)
    runtime = SimpleNamespace(
        _canonical_runtime=loop._runtime,
        _settings=settings,
        _eval_clock=loop._clock,
        _clock=loop._clock,
    )
    report = WatcherPaperScanReport(
        organization_id=target.organization_id,
        scan_scope=target.scan_scope,
        symbol=target.symbol,
        status="succeeded",
        reason_code="fixture",
        replayed=False,
        published=True,
        candidate_ids=(candidate.candidate_id,),
        kill_switch_active=False,
        discussion=SimpleNamespace(candidate=candidate, assessment=None, window=None),
    )
    evidence = SimpleNamespace(last_assembly=lambda: (object(), object()))
    continued = WatcherPaperRuntime._continue_paper_loop(
        runtime, world.session, target, report, evidence
    )
    assert calls == [selected] and continued.paper_loop_reason == "fixture_coordinator"
    from dataclasses import replace

    assert select_execution_context(
        world.session, settings, replace(target, strategy_version_id=uuid4()), world.w.now
    ) == (None, None)
    assert select_execution_context(
        world.session,
        settings.model_copy(update={"governed_blofin_demo_enabled": False}),
        target,
        world.w.now,
    ) == (None, None)
    assert select_execution_context(
        world.session, settings, target, world.version.authorized_until
    ) == (None, "experiment_execution_inactive")
    world.session.commit()
    pause(world)
    continued = WatcherPaperRuntime._continue_paper_loop(
        runtime, world.session, target, report, evidence
    )
    assert calls == [selected] and continued.paper_loop_reason == "experiment_execution_inactive"
    assert select_execution_context(world.session, settings, target, world.w.now) == (
        None,
        "experiment_execution_inactive",
    )
    world.session.rollback()
    world.version = world.w.running(world.version.configuration)
    world.w.running(world.version.configuration)
    assert select_execution_context(world.session, settings, target, world.w.now) == (
        None,
        "experiment_execution_ambiguous",
    )


@pytest.mark.parametrize(
    "limit, expected",
    [
        ("max_trades_per_day", "experiment_reserved_trade_limit"),
        ("max_trades_total", "experiment_reserved_trade_limit"),
        ("max_daily_loss", "experiment_reserved_daily_loss"),
        ("max_weekly_loss", "experiment_reserved_weekly_loss"),
        ("max_drawdown", "experiment_reserved_drawdown"),
    ],
)
def test_claim_budgets_count_reserved_or_filled_trades_before_samples(
    world, monkeypatch, limit, expected
):
    from app.experiments.execution_risk import experiment_reservation_refusal
    from app.services.execution_claim import conservative_reservation

    values = world.version.configuration.risk_limits.model_dump(mode="json")
    values[limit] = 1 if "trades" in limit else "20"
    if limit == "max_weekly_loss":
        values["max_daily_loss"] = "20"
    world.version = world.w.running(
        world.w.configuration(
            account=world.version.configuration.account,
            risk_limits=values,
        )
    )
    world.session.commit()
    loop, _, command = open_owned(world, monkeypatch)
    assert world.session.scalar(select(func.count()).select_from(ExperimentSampleRow)) == 0
    loop._safety(world.session).lock_epoch(
        organization_id=command.organization_id, account_id=command.account_id
    )
    plan = world.envelope.plan
    if limit == "max_weekly_loss":
        world.w.now += timedelta(hours=8)
    assert (
        experiment_reservation_refusal(
            world.session, plan, conservative_reservation(plan), world.w.now
        )
        == expected
    )
    world.session.rollback()


def test_overfill_refuses_without_changing_exposure_or_fill_facts(world, monkeypatch):
    from app.db.models import AccountRiskAccountingState, ExecutionFillFact

    loop, _, command = open_owned(world, monkeypatch)
    before = world.session.scalar(select(AccountRiskAccountingState))
    notional, version = before.actual_notional, before.version
    with pytest.raises(ConflictError):
        loop._dispatcher(world.session).apply_unique_fill(
            command_id=command.id,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100000"),
            source_identity="excess-fill",
            occurred_at=world.w.now,
            venue_source="blofin_demo",
        )
    after = world.session.scalar(select(AccountRiskAccountingState))
    assert (after.actual_notional, after.version) == (notional, version)
    assert (
        world.session.scalar(
            select(func.count())
            .select_from(ExecutionFillFact)
            .where(ExecutionFillFact.command_id == command.id)
        )
        == 1
    )


def test_lost_native_reply_and_repeated_entry_keep_one_post_and_one_reservation(world, monkeypatch):
    from app.db.models import RiskReservation

    loop, venue, command = open_owned(world, monkeypatch, behavior="timeout")
    requests = list(world.requests)
    with world.factory() as fresh:
        use_verified_persisted_symbol_cap(fresh)
        result = execute(world, loop, fresh)
        assert result.execution_command_id == command.id
        assert (
            fresh.scalar(
                select(func.count())
                .select_from(RiskReservation)
                .where(RiskReservation.command_id == command.id)
            )
            == 1
        )
    assert venue.post_count == 1 and world.requests == requests


def test_verified_terminal_unfilled_releases_account_gate_once(world, monkeypatch):
    from app.db.models import AccountRiskAccountingState, RiskReservation
    from app.services.demo_account_history import has_demo_entry_history

    loop, venue, command = open_owned(world, monkeypatch, behavior="unfilled")
    assert has_demo_entry_history(
        world.session, organization_id=command.organization_id, account_id=command.account_id
    )
    venue.terminal_unfilled = True
    assert loop.reconcile_command(world.session, command_id=command.id) == "demo_cancelled_unfilled"
    assert not has_demo_entry_history(
        world.session, organization_id=command.organization_id, account_id=command.account_id
    )
    reservation = world.session.scalar(
        select(RiskReservation).where(RiskReservation.command_id == command.id)
    )
    assert reservation.remaining_reserved_notional == 0 and reservation.release_state == "RELEASED"
    accounting = world.session.scalar(select(AccountRiskAccountingState))
    before = (
        accounting.reserved_notional,
        accounting.reserved_daily_loss,
        accounting.reserved_trade_slots,
        accounting.version,
    )
    assert loop.reconcile_command(world.session, command_id=command.id) == "demo_cancelled_unfilled"
    world.session.refresh(accounting)
    assert (
        accounting.reserved_notional,
        accounting.reserved_daily_loss,
        accounting.reserved_trade_slots,
        accounting.version,
    ) == before
    assert venue.post_count == 1


def test_cached_session_refreshes_locked_capacity_and_fill_projection(world, monkeypatch):
    from app.db.models import (
        AccountRiskAccountingState,
        AccountSafetyEpoch,
        ExecutionProjection,
        RiskReservation,
    )

    loop, venue, command = open_owned(world, monkeypatch, behavior="unfilled")
    stale = world.session
    projection = stale.scalar(
        select(ExecutionProjection).where(
            ExecutionProjection.receipt_id
            == world.session.scalar(
                select(RiskReservation).where(RiskReservation.command_id == command.id)
            ).receipt_id
        )
    )
    reservation = stale.scalar(
        select(RiskReservation).where(RiskReservation.command_id == command.id)
    )
    accounting = stale.scalar(select(AccountRiskAccountingState))
    epoch = stale.scalar(select(AccountSafetyEpoch))
    stale.commit()  # expire_on_commit=False deliberately keeps all four objects cached.
    assert projection.filled_quantity == 0
    with world.factory() as fresh:
        loop._dispatcher(fresh).apply_unique_fill(
            command_id=command.id,
            fill_quantity=Decimal(1),
            fill_price=Decimal("100000"),
            source_identity="demo-1:first-concurrent",
            occurred_at=world.w.now,
            venue_source="blofin_demo",
        )
        fresh.commit()
    assert projection.filled_quantity == 0  # This worker still has a stale identity map.
    result = loop._dispatcher(stale).apply_unique_fill(
        command_id=command.id,
        fill_quantity=Decimal(1),
        fill_price=Decimal("99999"),
        source_identity="demo-1:second-concurrent",
        occurred_at=world.w.now,
        venue_source="blofin_demo",
    )
    stale.commit()
    assert result.filled_quantity == projection.filled_quantity == Decimal(2)
    assert result.weighted_price == Decimal("99999.5")
    assert accounting.actual_notional == Decimal("199.999")
    assert accounting.actual_trade_count == reservation.converted_trade_slots == 1
    assert epoch.account_id == command.account_id and venue.post_count == 1
