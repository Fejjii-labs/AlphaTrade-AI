"""Existing governed coordinator + explicit origin, actual PostgreSQL, mocked IO.

No exchange requests leave MockTransport. Native exit/performance qualification
is deliberately unavailable; the prior identity test order is terminal unfilled.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.db.models import AuditLog, ExecutionCommand, KillSwitchState, Membership, VenueSubmitEffect
from app.experiments.governed_context import (
    GovernedExperimentContext,
    require_registered_experiment_dispatch,
)
from app.experiments.native_producer import ENTRY, ORIGIN, POSITION
from app.experiments.service import ExperimentService
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.repositories.execution_protocol import VenueSubmitEffectRepository
from app.runtime.canonical import build_production_canonical_runtime
from app.schemas.common import MembershipRole
from app.schemas.execution_protocol import VenueSubmitEffectState
from app.schemas.experiments import ExperimentTransition
from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
from app.services.manual_demo_service import ManualDemoService
from tests.support.phase6_fusion import ACCOUNT_ID, ORG_ID, USER_ID
from tests.support.phase7_trade_plan import plan_command, plan_terms
from tests.test_blofin_native_producer_postgres import ProducerWorld
from tests.test_blofin_native_producer_postgres import producer_engine as producer_engine
from tests.test_governed_blofin_demo import Venue
from tests.test_manual_blofin_demo import ManualVenue


class TerminalIdentityVenue(ManualVenue):
    def handle(self, request):
        if request.url.path.endswith("orders-tpsl-pending"):
            return httpx.Response(200, json={"code": "0", "data": []})
        result = super().handle(request)
        if request.url.path.endswith("order-detail"):
            content = result.json()
            for row in content["data"]:
                row.update(state="canceled", filledSize="0")
            return httpx.Response(200, json=content)
        return result


@pytest.fixture
def world(producer_engine, monkeypatch, request):
    from tests import test_blofin_native_producer_postgres as foundation
    from tests import test_experiment_native_identity as identity

    assembled = None
    if getattr(request, "param", None) == "canonical_live":
        from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
        from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
        from tests.support import phase7_postgres
        from tests.support.closed_loop_acceptance import FixtureMarket
        from tests.support.phase5_market import EVALUATED_AT
        from tests.support.phase6_fusion import fusion_policy

        market = FixtureMarket()
        source = BinanceUsdmPerpetualSource(
            transport=market.transport, observation_clock=lambda: EVALUATED_AT
        )
        assembled = FirstSliceEvidenceAssembler(
            source, replay=False, clock=lambda: EVALUATED_AT
        ).assemble(organization_id=ORG_ID, evaluated_at=EVALUATED_AT, policy=fusion_policy())
        monkeypatch.setattr(
            phase7_postgres, "make_evidence_window", lambda: assembled.evidence_window
        )

    monkeypatch.setattr(
        foundation,
        "plan_command",
        lambda plans: plan_command(
            plans,
            terms=plan_terms(
                plans.candidate,
                execution_policy_version="governed-blofin-demo/v1",
                instrument_rules={
                    **plan_terms(plans.candidate).instrument_rules.model_dump(mode="json"),
                    "contract_multiplier": "0.001",
                },
            ),
        ),
    )
    monkeypatch.setattr(
        identity, "ManualVenue", lambda price: TerminalIdentityVenue(price, behavior="unfilled")
    )
    services = []

    def captured_service(*args, **kwargs):
        result = ManualDemoService(*args, **kwargs)
        services.append(result)
        return result

    monkeypatch.setattr(identity, "ManualDemoService", captured_service)
    world = ProducerWorld.__new__(ProducerWorld)
    try:
        world.__init__(producer_engine, monkeypatch)
        account = world.version.configuration.account
        audit = world.session.get(AuditLog, account.execution_identity_audit_id)
        from uuid import UUID

        services[0].resolve(world.w.tenant, UUID(audit.resource_id))
        world.version = world.w.running(
            world.w.configuration(
                account=account,
                risk_limits={
                    **world.version.configuration.risk_limits.model_dump(mode="json"),
                    "max_risk_per_trade": "20",
                    "max_position_notional": "500000",
                    "max_total_exposure": "1000000",
                    "cost_allowance": "3",
                },
            )
        )
        world.session.commit()
        world.metadata[0]["contractValue"] = "0.001"
        world.assembled = assembled
        yield world
    finally:
        if hasattr(world, "session"):
            world.session.close()


def context(world):
    return GovernedExperimentContext(
        world.w.tenant, world.version.experiment_id, world.version.id, "baseline"
    )


def make_loop(world, monkeypatch, *, before_fence=None):
    settings = world.w.settings.model_copy(
        update={
            "governed_blofin_demo_enabled": True,
            "watcher_paper_staging_activation": True,
            "watcher_orchestration_enabled": True,
        }
    )

    class Clock:
        def now(self):
            world.w.now += timedelta(microseconds=100)
            return world.w.now

    clock = Clock()
    runtime = build_production_canonical_runtime(world.factory, settings=settings, clock=clock)
    venue = Venue(Decimal("100000"))
    provider = GovernedBloFinDemoProvider(
        BloFinClient(
            base_url=settings.blofin_demo_rest_base_url,
            api_key="fixture-execution",
            api_secret="fixture",
            api_passphrase="fixture",
            transport=httpx.MockTransport(venue.handle),
            max_retries=0,
        ),
        clock=clock.now,
    )
    captures = []

    def submit(*, plan, client_order_id, before_post):
        with world.factory() as probe:
            command = probe.scalar(
                select(ExecutionCommand).where(ExecutionCommand.revision_id == plan.revision_id)
            )
            receipts = list(
                probe.scalars(
                    select(AuditLog).where(AuditLog.resource_type == "experiment_execution")
                )
            )
            assert {r.redacted_metadata["operation"] for r in receipts} == {ORIGIN, POSITION, ENTRY}
            origin = next(r for r in receipts if r.redacted_metadata["operation"] == ORIGIN)
            assert origin.created_at < command.created_at
        before_post()
        captures.append(client_order_id)
        raise httpx.ReadTimeout("Synthetic lost reply, no POST performed")

    monkeypatch.setattr(provider, "submit", submit)
    loop = GovernedBloFinDemoLoop(
        runtime,
        settings,
        clock,
        provider=provider,
        experiment_context=context(world),
        activity_provider=world.provider(),
    )
    if before_fence is not None:
        original = provider.submit

        def delayed_submit(**kwargs):
            before_fence()
            return original(**kwargs)

        monkeypatch.setattr(provider, "submit", delayed_submit)
    return loop, captures, venue


def execute(world, loop, session=None):
    target = SimpleNamespace(organization_id=ORG_ID, user_id=USER_ID)
    return loop._execute_existing(
        session or world.session,
        target=target,
        candidate=world.plans.candidate,
        revision_id=world.envelope.plan.revision_id,
        account_id=ACCOUNT_ID,
    )


def pause(world):
    with world.factory() as session:
        service = ExperimentService(session, world.w.settings, clock=lambda: world.w.now)
        current = service.detail(world.w.tenant, world.version.experiment_id).versions[-1]
        service.transition(
            world.w.tenant,
            world.version.experiment_id,
            world.version.id,
            ExperimentTransition(action="pause", expected_revision=current.revision),
        )
        session.commit()


def test_actual_coordinator_commits_origin_and_binding_before_any_dispatch(world, monkeypatch):
    loop, captures, venue = make_loop(world, monkeypatch)
    result = execute(world, loop)
    assert result.execution_command_id is not None
    assert len(captures) == 1
    assert venue.post_count == 0


@pytest.mark.parametrize("world", ["canonical_live"], indirect=True)
def test_explicit_internal_entry_reaches_real_governed_claim_and_committed_receipts(
    world, monkeypatch
):
    from app.signal_fusion.strategy_evaluation_policy import executable_policy_from_fusion_policy
    from app.workers.watcher_paper_targets import PaperScanTarget
    from tests.support.phase6_fusion import fusion_policy, make_assessment

    loop, captures, venue = make_loop(world, monkeypatch)
    policy = executable_policy_from_fusion_policy(
        fusion_policy(),
        strategy_id=world.w.strategy.id,
        strategy_version_content_hash=world.w.strategy_version.content_hash,
        authored_spec=world.w.spec,
    )
    target = PaperScanTarget(
        organization_id=ORG_ID,
        user_id=USER_ID,
        strategy_id=world.w.strategy.id,
        strategy_version_id=world.plans.candidate.strategy_version_id,
        compiled_setup_definition_id=world.plans.candidate.setup_definition_id,
        compiled_content_hash=world.plans.candidate.executable_setup.content_hash,
        fusion_policy_version=world.plans.candidate.fusion_policy_version,
        symbol="BTCUSDT",
    )
    result = loop.continue_experiment_setup(
        world.session,
        tenant=world.w.tenant,
        experiment_id=world.version.experiment_id,
        version_id=world.version.id,
        variant_key="baseline",
        target=target,
        candidate=world.plans.candidate,
        assessment=make_assessment(world.assembled.evidence_window),
        window=world.assembled.evidence_window,
        assembled=world.assembled,
        policy=policy,
        kill_switch_active=False,
    )
    assert result.execution_command_id is not None, result.reason_code
    assert len(captures) == 1 and venue.post_count == 0
    assert {r.redacted_metadata["operation"] for r in world.operations()} >= {
        ORIGIN,
        POSITION,
        ENTRY,
    }


def test_fresh_coordinator_replays_original_command_without_new_position_or_send(
    world, monkeypatch
):
    first, captures, _ = make_loop(world, monkeypatch)
    original = execute(world, first)
    requests = list(world.requests)
    fresh, second_captures, _ = make_loop(world, monkeypatch)
    world.session.rollback()
    with world.factory() as session:
        replay = execute(world, fresh, session)
    assert replay.execution_command_id == original.execution_command_id
    assert captures and second_captures == []
    assert world.requests == requests
    assert (
        world.session.scalar(
            select(func.count())
            .select_from(ExecutionCommand)
            .where(ExecutionCommand.revision_id == world.envelope.plan.revision_id)
        )
        == 1
    )
    assert (
        world.session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.resource_type == "experiment_execution",
                AuditLog.redacted_metadata["operation"].as_string().in_([ORIGIN, POSITION, ENTRY]),
            )
        )
        == 3
    )


@pytest.mark.parametrize("change", ["pause", "expiry", "fence"])
def test_final_dispatch_revalidates_after_receipt_commit_and_preflight(world, monkeypatch, change):
    def after_receipt():
        if change == "pause":
            pause(world)
        elif change == "expiry":
            world.w.now = world.version.authorized_until + timedelta(seconds=1)
        else:
            with world.factory() as session:
                effect = session.scalar(
                    select(VenueSubmitEffect)
                    .join(ExecutionCommand)
                    .where(ExecutionCommand.revision_id == world.envelope.plan.revision_id)
                )
                effect.dispatch_fencing_token += 1
                session.commit()

    loop, captures, venue = make_loop(world, monkeypatch, before_fence=after_receipt)
    result = execute(world, loop)
    assert result.execution_command_id is not None
    assert captures == [] and venue.post_count == 0
    effect = world.session.scalar(
        select(VenueSubmitEffect).where(VenueSubmitEffect.command_id == result.execution_command_id)
    )
    assert effect.uncertainty is True
    assert effect.attempt == 1


def test_dispatch_refuses_uncommitted_entry_receipt_in_independent_session(world):
    world.register()
    command = world.command()
    world.session.commit()
    world.bind()  # No commit: a flushed receipt must not grant dispatch authority.
    with pytest.raises(ConflictError) as error:
        require_registered_experiment_dispatch(
            world.session, world.w.settings, command, now=world.w.now
        )
    assert error.value.code == "experiment_entry_binding_not_committed"


def test_concurrent_coordinators_preserve_one_origin_command_and_send_identity(world, monkeypatch):
    loops = [make_loop(world, monkeypatch) for _ in range(2)]
    world.session.rollback()
    barrier = Barrier(2)

    def run(loop):
        with world.factory() as session:
            barrier.wait(timeout=5)
            try:
                return execute(world, loop, session).execution_command_id
            except ConflictError as error:
                # Existing fences can refuse a worker that lost the send race.
                assert str(error) in {
                    "Demo send already consumed; reconciliation required.",
                    "Effect is not eligible for lease.",
                }
                session.rollback()
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, item[0]) for item in loops]
        results = [future.result(timeout=20) for future in futures]
    ids = {identity for identity in results if identity is not None}
    assert len(ids) == 1
    assert sum(len(item[1]) for item in loops) == 1
    assert (
        world.session.scalar(
            select(func.count())
            .select_from(ExecutionCommand)
            .where(ExecutionCommand.revision_id == world.envelope.plan.revision_id)
        )
        == 1
    )
    assert (
        world.session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.resource_type == "experiment_execution",
                AuditLog.redacted_metadata["operation"].as_string().in_([ORIGIN, POSITION, ENTRY]),
            )
        )
        == 3
    )


def test_locked_effect_refreshes_cached_created_state_before_another_lease(world):
    world.register()
    command = world.command()
    world.bind()
    world.session.commit()
    with world.factory() as stale:
        cached = VenueSubmitEffectRepository(stale).get_by_command(command.id)
        assert cached.state is VenueSubmitEffectState.CREATED
        with world.factory() as writer:
            effect = VenueSubmitEffectRepository(writer).get_by_command_for_update(command.id)
            effect.state = VenueSubmitEffectState.SEND_AMBIGUOUS
            effect.uncertainty = True
            writer.commit()
        locked = VenueSubmitEffectRepository(stale).get_by_command_for_update(command.id)
        assert locked is cached
        assert cached.state is VenueSubmitEffectState.SEND_AMBIGUOUS
        assert cached.uncertainty is True
        stale.rollback()


def test_unowned_governed_dispatch_keeps_its_existing_authority(world):
    command = world.command()
    world.session.commit()
    # No origin was registered. This added guard must not reinterpret or claim
    # existing ordinary/manual activity as experiment activity.
    require_registered_experiment_dispatch(
        world.session, world.w.settings, command, now=world.w.now
    )
    assert world.operations() == []


def test_explicit_internal_entry_does_not_activate_a_disarmed_coordinator(world):
    settings = world.w.settings
    before = settings.model_dump()
    runtime = build_production_canonical_runtime(world.factory, settings=settings)
    loop = GovernedBloFinDemoLoop(runtime, settings, SimpleNamespace(now=lambda: world.w.now))
    result = loop.continue_experiment_setup(
        world.session,
        tenant=world.w.tenant,
        experiment_id=world.version.experiment_id,
        version_id=world.version.id,
        variant_key="baseline",
        target=SimpleNamespace(organization_id=ORG_ID, user_id=USER_ID),
        candidate=world.plans.candidate,
        assessment=None,
        window=None,
        assembled=None,
        policy=None,
        kill_switch_active=False,
    )
    assert result.reason_code == "governed_demo_disarmed"
    assert world.operations() == [] and world.requests == []
    assert settings.model_dump() == before


def test_explicit_plan_respects_frozen_experiment_risk_caps_before_origin(world):
    smaller = world.w.running(world.w.configuration(account=world.version.configuration.account))
    selection = replace(context(world), experiment_id=smaller.experiment_id, version_id=smaller.id)
    with pytest.raises(ConflictError) as error:
        selection.require_active(
            world.session,
            world.w.settings,
            world.w.now,
            revision_id=world.envelope.plan.revision_id,
        )
    assert error.value.code == "experiment_plan_risk_envelope"
    assert world.operations() == []


@pytest.mark.parametrize("state", ["pause", "expiry"])
def test_registered_dispatch_refuses_pause_and_expiry_without_changing_operator_state(world, state):
    world.register()
    command = world.command()
    world.bind()
    world.session.commit()
    before = world.session.scalar(select(func.count()).select_from(KillSwitchState))
    if state == "pause":
        world.w.transition(world.version, "pause")
    else:
        world.w.now = world.version.authorized_until + timedelta(seconds=1)
    world.session.commit()
    with pytest.raises(ConflictError) as error:
        require_registered_experiment_dispatch(
            world.session, world.w.settings, command, now=world.w.now
        )
    assert error.value.code == "experiment_sample_window"
    assert world.session.scalar(select(func.count()).select_from(KillSwitchState)) == before


def test_context_never_selects_similar_experiment_or_foreign_tenant(world):
    wrong = replace(context(world), version_id=uuid4())
    with pytest.raises(NotFoundError):
        wrong.require_active(world.session, world.w.settings, world.w.now)
    wrong = replace(context(world), tenant=replace(world.w.tenant, user_id=uuid4()))
    with pytest.raises(ForbiddenError):
        wrong.require_active(world.session, world.w.settings, world.w.now)
    assert (
        world.session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.resource_type == "experiment_execution")
        )
        == 0
    )


@pytest.mark.parametrize("boundary", ["entry", "dispatch"])
def test_cached_membership_downgrade_refuses_before_new_execution_evidence(
    world, monkeypatch, boundary
):
    command = None
    if boundary == "dispatch":
        world.register()
        command = world.command()
        world.bind()
        world.session.commit()
    cached = world.session.scalar(
        select(Membership).where(
            Membership.organization_id == ORG_ID, Membership.user_id == USER_ID
        )
    )
    assert cached.role is MembershipRole.OWNER
    before_receipts = [receipt.id for receipt in world.operations()]
    before_requests = list(world.requests)
    before_commands = world.session.scalar(select(func.count()).select_from(ExecutionCommand))
    loop, captures, venue = make_loop(world, monkeypatch)
    with world.factory() as writer:
        current = writer.get(Membership, cached.id)
        current.role = MembershipRole.VIEWER
        writer.commit()
    with pytest.raises(ForbiddenError, match="current Trader"):
        if boundary == "entry":
            loop.continue_experiment_setup(
                world.session,
                tenant=world.w.tenant,
                experiment_id=world.version.experiment_id,
                version_id=world.version.id,
                variant_key="baseline",
                target=SimpleNamespace(organization_id=ORG_ID, user_id=USER_ID),
                candidate=world.plans.candidate,
                assessment=None,
                window=None,
                assembled=None,
                policy=None,
                kill_switch_active=False,
            )
        else:
            require_registered_experiment_dispatch(
                world.session, loop._settings, command, now=world.w.now
            )
    assert cached.role is MembershipRole.VIEWER
    assert [receipt.id for receipt in world.operations()] == before_receipts
    assert world.requests == before_requests and captures == [] and venue.post_count == 0
    assert (
        world.session.scalar(select(func.count()).select_from(ExecutionCommand)) == before_commands
    )
