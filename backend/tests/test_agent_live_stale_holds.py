"""Finished GPT Live sessions must not hold the monthly quota forever.

Staging 2026-11-20: two finished sessions with one Agent lookup each stayed
``uncertain`` with 664k reserved tokens apiece. Real November usage was 8.5k
tokens, yet every new start was refused with
``voice_monthly_token_quota_exhausted``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.agent_voice.config import VoiceUnavailableError
from app.agent_voice.live_config import CLEANUP_SECONDS, CLOSE_SECONDS
from app.agent_voice.reservations_sweep import (
    SECONDARY_GRACE,
    expired_hold_charge_id,
    release_expired_voice_holds,
)
from app.db.models import OrganizationQuota, UsageEvent
from app.db.voice_realtime import VoiceSessionReservation
from app.services.quota_service import QuotaService
from tests.test_agent_live_integration import live_limits as _live_limits  # noqa: F401
from tests.test_agent_live_postgres import final_receipt, independent_handle, ledger_for
from tests.test_agent_live_postgres import live_database as _live_database  # noqa: F401
from tests.test_agent_live_postgres import voice_limits as _voice_limits  # noqa: F401
from tests.test_agent_realtime_persistence import voice_database as _voice_database  # noqa: F401

pytestmark = pytest.mark.usefixtures("fixture_realtime_model_is_priced")

CENTS = Decimal("0.00000001")
MARGIN = timedelta(seconds=CLOSE_SECONDS + CLEANUP_SECONDS)


@pytest.fixture
def voice_limits(request):
    return request.getfixturevalue("_voice_limits")


@pytest.fixture
def live_database(request):
    return request.getfixturevalue("_live_database")


def roomy(database):
    return database.limits.model_copy(update={"remaining_budget_usd": Decimal("1000")})


def allow_holds(database, count: int) -> None:
    with database.sessions() as session:
        quota = session.scalar(select(OrganizationQuota))
        quota.monthly_token_limit = database.limits.reserved_tokens(database.settings) * count
        quota.monthly_cost_limit = Decimal(1000)
        session.commit()


def row(database, ledger) -> VoiceSessionReservation:
    with database.sessions() as session:
        return session.get(VoiceSessionReservation, ledger.id)


def expire(database, ledger, ago: timedelta) -> None:
    with database.sessions() as session:
        held = session.get(VoiceSessionReservation, ledger.id)
        held.ledger = held.ledger | {"expires_at": (datetime.now(UTC) - ago).isoformat()}
        session.commit()


def finished_session(database, *, acknowledged: bool, seconds: int = 30):
    """A session that ran, delegated one Agent lookup and received its final receipt."""
    ledger = ledger_for(database, limits=roomy(database))
    ledger.reserve()
    ledger.provider_contact_may_start()
    ledger.started("live_" + ledger.id.hex, resolved_model="gpt-live-1")
    body = {"message": "What is BTC doing?", "conversation_id": str(ledger.conversation_id)}
    ledger.tool("lookup", body)
    if acknowledged:
        ledger.tool_done("lookup", {"reply": "BTC is flat."})
    ledger.usage(final_receipt(ledger, seconds=seconds), final=True)
    independent_handle(database, ledger).close()
    held = row(database, ledger)
    assert (held.status, held.accounted_cost) == ("uncertain", None)
    return ledger


def reserved_tokens(database) -> int:
    with database.sessions() as session:
        usage = QuotaService(session).get_status(database.organization_id).usage
        session.commit()
        return usage.monthly_tokens_reserved


def test_finished_session_with_answered_lookup_frees_quota_on_next_start(live_database):
    database = live_database
    allow_holds(database, 1)
    finished = finished_session(database, acknowledged=True, seconds=30)

    with pytest.raises(VoiceUnavailableError, match="voice_monthly_token_quota_exhausted"):
        ledger_for(database, limits=roomy(database)).reserve()

    expire(database, finished, MARGIN + SECONDARY_GRACE + timedelta(seconds=5))
    # The next start settles the stale hold itself; no quota read is needed first.
    ledger_for(database, limits=roomy(database)).reserve()

    settled = row(database, finished)
    expected = (Decimal(30) * database.limits.live.usd_per_second).quantize(CENTS)
    assert (settled.status, settled.accounted_cost) == ("closed", expected)
    with database.sessions() as session:
        # Measured seconds stay the only voice charge; the lookup bills via its own events.
        assert session.get(UsageEvent, expired_hold_charge_id(finished.id)) is None


def test_unanswered_lookup_is_charged_in_full_but_releases_tokens(live_database):
    database = live_database
    finished = finished_session(database, acknowledged=False)
    expire(database, finished, MARGIN + SECONDARY_GRACE + timedelta(seconds=5))

    with database.sessions() as session:
        assert release_expired_voice_holds(session, database.organization_id) == 1
        session.commit()

    charged = row(database, finished)
    assert (charged.status, charged.accounted_cost) == ("closed", charged.reserved_cost)
    with database.sessions() as session:
        event = session.get(UsageEvent, expired_hold_charge_id(finished.id))
        assert event.estimated_cost == charged.reserved_cost and event.total_tokens == 0
    assert reserved_tokens(database) == 0


def test_lookup_hold_survives_until_the_secondary_grace_ends(live_database):
    database = live_database
    finished = finished_session(database, acknowledged=True)
    expire(database, finished, MARGIN + timedelta(seconds=5))

    with database.sessions() as session:
        assert release_expired_voice_holds(session, database.organization_id) == 0
        session.commit()
    assert row(database, finished).status == "uncertain"
    assert reserved_tokens(database) == row(database, finished).reserved_tokens


def test_settled_sessions_do_not_also_debit_the_approved_budget(live_database):
    database = live_database
    hold = database.limits.reservation(database.settings)
    # Room for two concurrent holds. Settled ones only count through their usage.
    limits = database.limits.model_copy(
        update={"remaining_budget_usd": hold * 2 + Decimal("0.0001")}
    )
    for _ in range(4):
        ledger = ledger_for(database, limits=limits)
        ledger.reserve()
        ledger.close({"stage": "admission", "error_class": "CancelledError"})

    open_hold = ledger_for(database, limits=limits)
    open_hold.reserve()
    open_hold.provider_contact_may_start()
    open_hold.close({"stage": "provider_connect", "error_class": "InvalidStatus"})
    ledger_for(database, limits=limits).reserve()  # second concurrent hold fits
    with pytest.raises(VoiceUnavailableError, match="voice_approved_budget_exhausted"):
        ledger_for(database, limits=limits).reserve()
