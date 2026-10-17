"""Actual shared Agent proposal/confirm to pinned watcher authority on disposable PG.

Synthetic approved versions and deterministic providers only. This selection does
not grant execution or send orders/Telegram; it uses the isolated assignment schema.
"""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.models import KillSwitchState
from app.db.watcher_assignments import WatcherAssignmentReceiptRow, WatcherStrategyAssignmentRow
from app.interactive_agent.actions import ActionRequest
from app.interactive_agent.contracts import (
    AgentTurnRequest,
    ProposalDecisionRequest,
    ProposalLifecycle,
)
from app.interactive_agent.service import InteractiveAgentService
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.services.watcher_assignment_service import (
    WatcherAssignmentService,
    assignment_configuration_exists,
    assignment_scan_targets,
)
from tests.test_watcher_assignments_postgres import PEER, body, save
from tests.test_watcher_assignments_postgres import factory as factory
from tests.test_watcher_paper_runtime import (
    ORG,
    ORG_B,
    USER,
    USER_B,
    _runtime,
    _seed_approved_compiled,
    _settings,
)


def propose(db, request):
    return InteractiveAgentService(db, settings=_settings()).handle_turn(
        AgentTurnRequest(
            message="Use this saved version to monitor this coin; keep trading unchanged.",
            action=ActionRequest(
                name="watcher.assignments.replace", arguments=request.model_dump(mode="json")
            ),
        ),
        organization_id=ORG,
        user_id=USER,
    )


def confirm(db, turn, *, digest=None, statement="I confirm", org=ORG, user=USER):
    proposal = turn.proposals[0]
    return InteractiveAgentService(db, settings=_settings()).confirm(
        proposal.proposal_id,
        ProposalDecisionRequest(
            conversation_id=turn.conversation_id,
            expected_content_hash=digest or proposal.content_hash,
            statement=statement,
        ),
        organization_id=org,
        user_id=user,
    )


def assert_unconfigured(db):
    assert not assignment_configuration_exists(db, ORG)
    assert WatcherWatchlistRepository(db).load(ORG).revision == 0
    assert db.scalar(select(func.count()).select_from(WatcherStrategyAssignmentRow)) == 0
    assert db.scalar(select(func.count()).select_from(WatcherAssignmentReceiptRow)) == 0


def test_actual_agent_confirmation_commits_receipt_then_fresh_worker_consumes_pinned_version(
    factory, monkeypatch
):
    with factory() as db:
        _, version_id = _seed_approved_compiled(db)
        request = body(db, [version_id])
        original_slots = WatcherWatchlistRepository(db).load(ORG).slots
        original_kill_rows = db.scalar(select(func.count()).select_from(KillSwitchState))
        turn = propose(db, request)
        db.commit()
        assert len(turn.proposals) == 1
        proposal = turn.proposals[0]
        assert proposal.status is ProposalLifecycle.PROPOSED
        assert not proposal.applied and not proposal.authority_mutated
        assert proposal.payload["assignment_replace_request"] == request.model_dump(mode="json")
        assert_unconfigured(db)
    with factory() as restarted:
        applied = confirm(restarted, turn)
        restarted.commit()
        assert applied.status is ProposalLifecycle.APPLIED and applied.authority_mutated
        assert applied.resulting_record_id == ORG
        assert applied.application_result["monitoring_only"] is True
        assert applied.application_result["trading_activated"] is False
        assert applied.application_result["execution_attempted"] is False
    with factory() as fresh:
        visible = WatcherAssignmentService(fresh).read(organization_id=ORG, user_id=USER)
        assert visible.revision == 1 and len(visible.assignments) == 1
        receipt = fresh.get(WatcherAssignmentReceiptRow, (ORG, request.request_key))
        assert receipt.response == visible.model_dump(mode="json")
        assert [
            (slot.symbol, slot.enabled)
            for slot in WatcherWatchlistRepository(fresh).load(ORG).slots
        ] == [(slot.symbol, slot.enabled) for slot in original_slots]
        assert fresh.scalar(select(func.count()).select_from(KillSwitchState)) == original_kill_rows
        (target,), _ = assignment_scan_targets(
            fresh, organization_id=ORG, symbols=("BTCUSDT",), limit=25
        )
        assert target.strategy_version_id == version_id
        assert target.assignment_id == request.assignments[0].id
        replay = confirm(fresh, turn)
        fresh.commit()
        assert replay.resulting_record_id == applied.resulting_record_id
        assert fresh.scalar(select(func.count()).select_from(WatcherAssignmentReceiptRow)) == 1
        assert WatcherWatchlistRepository(fresh).load(ORG).revision == 1
    runtime, _, side_effects, _ = _runtime(factory)
    runtime._watchlist_mode = True
    runtime._tenant_watchlists = True
    monkeypatch.setattr(
        runtime, "_continue_paper_loop", lambda *args: pytest.fail("Monitoring cannot execute")
    )
    result = runtime.run_cycle()
    assert len(result.scans) == 1 and result.scans[0].status == "succeeded"
    assert side_effects.unused


@pytest.mark.parametrize("seal", ["wrong_hash", "quoted_statement"])
def test_agent_monitoring_confirmation_requires_original_hash_and_explicit_statement(factory, seal):
    with factory() as db:
        _, version_id = _seed_approved_compiled(db)
        turn = propose(db, body(db, [version_id]))
        db.commit()
        with pytest.raises((ConflictError, ValidationAppError)):
            confirm(
                db,
                turn,
                digest="0" * 64 if seal == "wrong_hash" else None,
                statement="> I confirm" if seal == "quoted_statement" else "I confirm",
            )
        db.rollback()
        assert_unconfigured(db)


def test_changed_configuration_refuses_stale_agent_confirmation_without_second_mutation(factory):
    with factory() as db:
        _, version_id = _seed_approved_compiled(db)
        turn = propose(db, body(db, [version_id]))
        db.commit()
    with factory() as ui_writer:
        save(ui_writer, body(ui_writer))
    with factory() as restarted:
        with pytest.raises(ConflictError, match="assignments changed"):
            confirm(restarted, turn)
        restarted.rollback()
        assert WatcherWatchlistRepository(restarted).load(ORG).revision == 1
        assert restarted.scalar(select(func.count()).select_from(WatcherAssignmentReceiptRow)) == 1
        assert restarted.scalar(select(func.count()).select_from(WatcherStrategyAssignmentRow)) == 0


@pytest.mark.parametrize("org,user", [(ORG_B, USER_B), (ORG, PEER)])
def test_foreign_tenant_or_private_actor_cannot_confirm_monitoring_proposal(factory, org, user):
    with factory() as db:
        _, version_id = _seed_approved_compiled(db)
        turn = propose(db, body(db, [version_id]))
        db.commit()
    with factory() as foreign:
        with pytest.raises(NotFoundError):
            confirm(foreign, turn, org=org, user=user)
        foreign.rollback()
        assert_unconfigured(foreign)


def test_agent_proposal_rejects_foreign_saved_version_without_configuration_write(factory):
    with factory() as db:
        _, foreign_version_id = _seed_approved_compiled(db, org=ORG_B, user=USER_B)
        with pytest.raises(ValidationAppError, match="exact owned immutable"):
            propose(db, body(db, [foreign_version_id]))
        db.rollback()
        assert_unconfigured(db)


def test_existing_assignment_outside_recent_picker_can_be_paused_by_confirmed_agent(
    factory, monkeypatch
):
    with factory() as db:
        _, version_id = _seed_approved_compiled(db)
        original = body(db, [version_id])
        save(db, original)
    read = WatcherAssignmentService.read

    def bounded_picker(self, *, organization_id, user_id):
        result = read(self, organization_id=organization_id, user_id=user_id)
        # Represent an old saved version outside the bounded latest100 picker;
        # its real immutable version and existing owned assignment remain stored.
        return result.model_copy(
            update={
                "available_versions": [
                    version
                    for version in result.available_versions
                    if version.strategy_version_id != version_id
                ]
            }
        )

    monkeypatch.setattr(WatcherAssignmentService, "read", bounded_picker)
    pause = original.model_copy(
        update={
            "revision": 1,
            "request_key": str(uuid4()),
            "assignments": [
                original.assignments[0].model_copy(update={"monitoring_enabled": False})
            ],
        }
    )
    with factory() as proposer:
        current = WatcherAssignmentService(proposer).read(organization_id=ORG, user_id=USER)
        assert version_id not in {
            version.strategy_version_id for version in current.available_versions
        }
        assert len(current.assignments) == 1
        assert current.assignments[0].strategy_version_id == version_id
        turn = propose(proposer, pause)
        proposer.commit()
        assert turn.proposals[0].status is ProposalLifecycle.PROPOSED
        assert not turn.proposals[0].authority_mutated
        assert WatcherWatchlistRepository(proposer).load(ORG).revision == 1
        (target,), _ = assignment_scan_targets(
            proposer, organization_id=ORG, symbols=("BTCUSDT",), limit=25
        )
        assert target.strategy_version_id == version_id
    with factory() as confirmer:
        applied = confirm(confirmer, turn)
        confirmer.commit()
        assert applied.status is ProposalLifecycle.APPLIED
        assert applied.application_result["trading_activated"] is False
        assert applied.application_result["execution_attempted"] is False
    with factory() as restarted:
        result = read(WatcherAssignmentService(restarted), organization_id=ORG, user_id=USER)
        assert result.revision == 2 and len(result.assignments) == 1
        assert not result.assignments[0].monitoring_enabled
        assert result.assignments[0].strategy_version_id == version_id
        assert result.assignments[0].timeframes == original.assignments[0].timeframes
        assert result.assignments[0].parameters == original.assignments[0].parameters
        assert restarted.get(WatcherAssignmentReceiptRow, (ORG, pause.request_key)) is not None
        assert restarted.scalar(select(func.count()).select_from(WatcherAssignmentReceiptRow)) == 2
        targets, facts = assignment_scan_targets(
            restarted, organization_id=ORG, symbols=("BTCUSDT",), limit=25
        )
        assert targets == () and facts["BTCUSDT"][0]["reason_code"] == "monitoring_disabled"
