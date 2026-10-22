"""Disposable PostgreSQL ledger evidence; no provider or trading requests.

Existing voice_database supplies committed owner/conversation/quota rows and a
NullPool. Every ledger operation opens its own transaction/physical connection.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid5

import pytest
from sqlalchemy import event, func, select, text

from app.agent_voice.config import VoiceUnavailableError
from app.agent_voice.live_accounting import LiveLedger
from app.db.models import Membership, OrganizationQuota, UsageEvent
from app.db.voice_realtime import VoiceSessionReservation
from app.schemas.common import CostSource, MembershipRole, UsageStatus
from app.services.quota_service import QuotaService
from tests.test_agent_live_integration import live_limits as _live_limits  # noqa: F401
from tests.test_agent_realtime_persistence import voice_database as _voice_database  # noqa: F401


@pytest.fixture
def voice_limits(request):
    return request.getfixturevalue("_live_limits")


@pytest.fixture
def live_database(request):
    database = request.getfixturevalue("_voice_database")
    with database.sessions() as session:
        quota = session.scalar(select(OrganizationQuota))
        quota.hard_block_threshold = Decimal(1)
        quota.monthly_cost_limit = Decimal(10)
        quota.monthly_token_limit = 10000000
        session.commit()
    return database


def ledger_for(database, *, sessions=None, limits=None):
    return LiveLedger(
        sessions or database.sessions,
        database.settings,
        limits or database.limits,
        database.token,
        database.conversation_id,
    )


def independent_handle(database, original, *, sessions=None):
    """Reconstitute the server-owned row identity in a separate ledger handle."""
    fresh = ledger_for(database, sessions=sessions, limits=original.limits)
    fresh.id = original.id
    fresh.organization_id, fresh.user_id = original.organization_id, original.user_id
    return fresh


def final_receipt(ledger, seconds=15, *, reason="close_requested"):
    return {
        "session": {"id": "live_" + ledger.id.hex},
        "usage": {"seconds": seconds},
        "reason": reason,
    }


def started_ledger(database):
    ledger = ledger_for(database)
    ledger.reserve()
    ledger.started("live_" + ledger.id.hex)
    return ledger


def concurrent_sessions(database, barrier, pids):
    @contextmanager
    def sessions():
        with database.sessions() as session:
            pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            # Both physical connections/transactions are open before either can
            # reach the production row lock. Futures and server locks are bounded.
            barrier.wait(timeout=5)
            yield session

    return sessions


@pytest.mark.parametrize("boundary", ["budget", "monthly_cost", "monthly_tokens", "both_fit"])
def test_competing_live_reservations_respect_committed_budget_and_quota(live_database, boundary):
    database = live_database
    ceiling = database.limits.reservation(database.settings)
    tokens = database.limits.reserved_tokens(database.settings)
    limits = database.limits.model_copy(
        update={"remaining_budget_usd": ceiling if boundary == "budget" else Decimal(5)}
    )
    with database.sessions() as session:
        quota = session.scalar(select(OrganizationQuota))
        if boundary == "monthly_cost":
            quota.monthly_cost_limit = ceiling
        if boundary == "monthly_tokens":
            quota.monthly_token_limit = tokens
        session.commit()
    pids, barrier = [], Barrier(2)
    factory = concurrent_sessions(database, barrier, pids)

    def reserve():
        ledger = ledger_for(database, sessions=factory, limits=limits)
        try:
            ledger.reserve()
            return ledger, "reserved"
        except VoiceUnavailableError as exc:
            return ledger, str(exc)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(reserve) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert len(pids) == len(set(pids)) == 2
    reason = {
        "budget": "voice_approved_budget_exhausted",
        "monthly_cost": "voice_monthly_cost_quota_exhausted",
        "monthly_tokens": "voice_monthly_token_quota_exhausted",
    }.get(boundary)
    accepted = 2 if boundary == "both_fit" else 1
    assert sorted(result for _, result in results) == (
        ["reserved", "reserved"] if reason is None else sorted(["reserved", reason])
    )
    with database.sessions() as fresh:
        assert fresh.scalar(select(func.count()).select_from(VoiceSessionReservation)) == accepted
        usage = QuotaService(fresh).get_status(database.organization_id).usage
        assert usage.monthly_cost_reserved == accepted * ceiling
        assert usage.monthly_tokens_reserved == accepted * tokens
    if boundary == "both_fit":
        # Separate sessions must retain separate duration receipts and final costs.
        for number, (original, _) in enumerate(results):
            original.sessions = database.sessions
            original.started("live_" + original.id.hex)
            handle = independent_handle(database, original)
            handle.usage(final_receipt(handle, 10 + number * 5), final=True)
            independent_handle(database, original).close()
        with database.sessions() as fresh:
            events = fresh.scalars(
                select(UsageEvent).where(UsageEvent.feature == "agent_voice_live")
            ).all()
            assert len(events) == 2 and len({row.request_id for row in events}) == 2
            assert sum(row.estimated_cost for row in events) == Decimal("0.020")
            assert (
                QuotaService(fresh).get_status(database.organization_id).usage.monthly_cost_reserved
                == 0
            )


def test_concurrent_usage_and_final_receipt_replays_update_one_duration_event(live_database):
    database = live_database
    original = started_ledger(database)
    independent_handle(database, original).usage({"usage": {"seconds": 12}})
    for final in (False, True):
        pids = []
        factory = concurrent_sessions(database, Barrier(2), pids)
        handles = [independent_handle(database, original, sessions=factory) for _ in range(2)]
        payload = final_receipt(original) if final else {"usage": {"seconds": 15}}
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(handle.usage, payload, final=final) for handle in handles]
            for future in futures:
                future.result(timeout=10)
        assert len(pids) == len(set(pids)) == 2
    independent_handle(database, original).close()
    with database.sessions() as fresh:
        row = fresh.get(VoiceSessionReservation, original.id)
        usage = fresh.scalars(
            select(UsageEvent).where(UsageEvent.request_id == str(original.id))
        ).all()
        assert len(usage) == 1 and usage[0].id == uuid5(original.id, "live-duration")
        assert usage[0].status == UsageStatus.SUCCESS
        assert usage[0].cost_source == CostSource.STATIC_ESTIMATED
        assert usage[0].total_tokens == 0 and usage[0].estimated_cost == Decimal("0.012")
        assert row.ledger["live"]["seconds"] == "15" and row.ledger["live"]["finalized"]
        assert row.status == "closed" and row.accounted_cost == Decimal("0.012")
        before = (usage[0].event_at, row.ledger)
    independent_handle(database, original).usage(final_receipt(original), final=True)
    with database.sessions() as fresh:
        row = fresh.get(VoiceSessionReservation, original.id)
        usage = fresh.get(UsageEvent, uuid5(original.id, "live-duration"))
        assert (usage.event_at, row.ledger) == before
        assert QuotaService(fresh).get_status(
            database.organization_id
        ).usage.monthly_cost_used == Decimal("0.012")


@pytest.mark.parametrize("conflict", ["session", "reason"])
def test_final_replay_requires_original_session_and_reason(live_database, conflict):
    original = started_ledger(live_database)
    original.usage(final_receipt(original), final=True)
    replay = final_receipt(original)
    if conflict == "session":
        replay["session"]["id"] = "live_another_session"
    else:
        replay["reason"] = "connection_lost"
    with pytest.raises(VoiceUnavailableError, match=r"mismatch|conflict"):
        independent_handle(live_database, original).usage(replay, final=True)
    with live_database.sessions() as fresh:
        row = fresh.get(VoiceSessionReservation, original.id)
        assert row.ledger["live"]["reason"] == "close_requested"
        assert fresh.scalar(select(func.count()).select_from(UsageEvent)) == 1


def test_unconfirmed_silent_close_never_commits_a_released_hold(live_database):
    database = live_database
    original = started_ledger(database)
    observed = []

    def inspect_committed_state(_session):
        # A fresh physical connection observes every committed state during
        # cleanup, just as an ordinary independent quota request could.
        with database.sessions() as fresh:
            row = fresh.get(VoiceSessionReservation, original.id)
            quota = QuotaService(fresh).get_status(database.organization_id).usage
            observed.append(
                (row.status, row.ledger["live"]["finalized"], quota.monthly_cost_reserved)
            )

    @contextmanager
    def observed_sessions():
        with database.sessions() as session:
            event.listen(session, "after_commit", inspect_committed_state)
            yield session

    independent_handle(database, original, sessions=observed_sessions).close()
    assert observed
    assert all(
        not finalized and cost == original.limits.reservation(original.settings)
        for _status, finalized, cost in observed
    )
    assert observed[-1][0] == "uncertain"


@pytest.mark.parametrize("receipt_order", ["before_cleanup", "after_cleanup"])
def test_finalization_after_expiry_and_revocation_reconciles_fresh_sessions(
    live_database, receipt_order
):
    database = live_database
    original = started_ledger(database)
    original.usage({"usage": {"seconds": 12}})
    with database.sessions() as session:
        row = session.get(VoiceSessionReservation, original.id)
        row.ledger = row.ledger | {
            "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        }
        session.scalar(select(Membership)).role = MembershipRole.VIEWER
        session.commit()
    if receipt_order == "after_cleanup":
        independent_handle(database, original).close()
        with database.sessions() as fresh:
            row = fresh.get(VoiceSessionReservation, original.id)
            assert row.status == "uncertain" and row.accounted_cost is None
            assert QuotaService(fresh).get_status(
                database.organization_id
            ).usage.monthly_cost_reserved == original.limits.reservation(original.settings)
    independent_handle(database, original).usage(final_receipt(original), final=True)
    independent_handle(database, original).close()
    independent_handle(database, original).close()
    with database.sessions() as fresh:
        row = fresh.get(VoiceSessionReservation, original.id)
        assert row.status == "closed" and row.accounted_cost == Decimal("0.012")
        quota = QuotaService(fresh).get_status(database.organization_id).usage
        assert quota.monthly_cost_reserved == 0 and quota.monthly_tokens_reserved == 0
        assert quota.monthly_cost_used == Decimal("0.012")
        assert fresh.scalar(select(func.count()).select_from(UsageEvent)) == 1


def test_final_voice_receipt_keeps_unresolved_secondary_hold_in_fresh_session(live_database):
    database = live_database
    original = started_ledger(database)
    original.tool(
        "delegation_fixture",
        {"message": "Unresolved request", "conversation_id": str(original.conversation_id)},
    )
    independent_handle(database, original).usage(final_receipt(original), final=True)
    independent_handle(database, original).close()
    with database.sessions() as fresh:
        row = fresh.get(VoiceSessionReservation, original.id)
        assert row.ledger["live"]["finalized"] and row.status == "uncertain"
        assert row.accounted_cost is None
        quota = QuotaService(fresh).get_status(database.organization_id).usage
        assert quota.monthly_cost_reserved == original.limits.reservation(original.settings)
        assert quota.monthly_cost_used == Decimal("0.012")
        assert quota.monthly_tokens_reserved == original.limits.reserved_tokens(original.settings)
