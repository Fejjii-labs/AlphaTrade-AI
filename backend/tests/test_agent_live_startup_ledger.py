"""GPT Live reservation lifecycle across startup failures (disposable PostgreSQL).

A hold is released at $0 only when the ledger proves the provider was never
contacted (failure before the committed ``provider_contact`` mark). From that
mark on, refusal, timeout or cancellation is not proof of zero spend:
the row stays uncertain until a final duration receipt, or the expiry sweep
charges the full reservation.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from app.agent_voice import live_socket
from app.agent_voice.config import VoiceUnavailableError
from app.agent_voice.live_accounting import LiveLedger
from app.agent_voice.live_config import CLEANUP_SECONDS, CLOSE_SECONDS
from app.agent_voice.reservations_sweep import (
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


@pytest.fixture
def voice_limits(request):
    return request.getfixturevalue("_voice_limits")


@pytest.fixture
def live_database(request):
    return request.getfixturevalue("_live_database")


CENTS = Decimal("0.00000001")  # NUMERIC scale of reservation money columns
CONNECT_REFUSED = {"stage": "provider_connect", "error_class": "InvalidStatus", "http_status": 429}
PRE_CONTACT = {"stage": "admission", "error_class": "CancelledError"}


def roomy(database):
    """Limits whose approved budget fits many reservations (budget debits are permanent)."""
    return database.limits.model_copy(update={"remaining_budget_usd": Decimal("1000")})


def tokens_per_hold(database) -> int:
    return database.limits.reserved_tokens(database.settings)


def allow_holds(database, count: int) -> None:
    with database.sessions() as session:
        quota = session.scalar(select(OrganizationQuota))
        quota.monthly_token_limit = tokens_per_hold(database) * count
        quota.monthly_cost_limit = Decimal(1000)
        session.commit()


def reserved_tokens(database) -> int:
    with database.sessions() as session:
        return (
            QuotaService(session).get_status(database.organization_id).usage.monthly_tokens_reserved
        )


def row(database, ledger) -> VoiceSessionReservation:
    with database.sessions() as session:
        return session.get(VoiceSessionReservation, ledger.id)


def after_expiry(database) -> datetime:
    return datetime.now(UTC) + timedelta(
        seconds=database.limits.maximum_seconds + CLOSE_SECONDS + CLEANUP_SECONDS + 5
    )


def sweep(database, moment: datetime) -> int:
    with database.sessions() as session:
        released = release_expired_voice_holds(session, database.organization_id, now=moment)
        session.commit()
        return released


def charges(database, ledger) -> UsageEvent | None:
    with database.sessions() as session:
        return session.get(UsageEvent, expired_hold_charge_id(ledger.id))


def test_failure_before_provider_contact_releases_tokens_and_keeps_audit(live_database):
    database = live_database
    ledger = ledger_for(database)
    ledger.reserve()
    assert reserved_tokens(database) == tokens_per_hold(database)

    ledger.close(PRE_CONTACT)

    released = row(database, ledger)
    assert (released.status, released.accounted_cost) == ("closed", Decimal(0))
    # Audit history is intact: reserved amounts, stage and sanitized failure.
    assert released.reserved_tokens == tokens_per_hold(database)
    assert released.reserved_cost == database.limits.reservation(database.settings).quantize(CENTS)
    assert released.ledger["live_transport"] == {"stage": "admitted", "failure": PRE_CONTACT}
    assert reserved_tokens(database) == 0
    assert charges(database, ledger) is None


def test_failure_after_provider_contact_keeps_conservative_hold(live_database):
    database = live_database
    ledger = ledger_for(database)
    ledger.reserve()
    ledger.provider_contact_may_start()

    ledger.close(CONNECT_REFUSED)

    held = row(database, ledger)
    assert (held.status, held.accounted_cost) == ("uncertain", None)
    assert held.ledger["live_transport"] == {
        "stage": "provider_contact",
        "failure": CONNECT_REFUSED,
    }
    assert reserved_tokens(database) == tokens_per_hold(database)


def test_released_hold_can_never_be_followed_by_provider_contact(live_database):
    database = live_database
    ledger = ledger_for(database)
    ledger.reserve()
    independent_handle(database, ledger).close(PRE_CONTACT)

    with pytest.raises(VoiceUnavailableError, match="voice_session_closed"):
        ledger.provider_contact_may_start()
    assert row(database, ledger).ledger["live_transport"]["stage"] == "admitted"


@pytest.mark.parametrize(
    ("resolved_model", "expected_status"),
    [("gpt-live-1", "closed"), ("gpt-live-1-preview", "uncertain"), ("", "uncertain")],
)
def test_bound_identity_records_final_receipt_even_when_config_refused(
    live_database, resolved_model, expected_status
):
    database = live_database
    ledger = ledger_for(database)
    ledger.reserve()
    ledger.provider_contact_may_start()
    ledger.started("live_" + ledger.id.hex, resolved_model=resolved_model)

    ledger.usage(final_receipt(ledger, seconds=12), final=True)
    ledger.close({"stage": "awaiting_session_started", "reason": "live_provider_event_unavailable"})

    settled = row(database, ledger)
    assert settled.ledger["live"]["finalized"] is True
    assert settled.status == expected_status
    if expected_status == "closed":
        # Measured seconds at the priced rate; an unpriced model is never confirmed.
        expected = Decimal(12) * database.limits.live.usd_per_second
        assert settled.accounted_cost == expected.quantize(CENTS)
    else:
        assert settled.accounted_cost is None


def test_successful_session_settles_measured_seconds(live_database):
    database = live_database
    ledger = ledger_for(database)
    ledger.reserve()
    ledger.provider_contact_may_start()
    ledger.started("live_" + ledger.id.hex, resolved_model="gpt-live-1")
    ledger.usage(final_receipt(ledger, seconds=40), final=True)

    ledger.close(None)

    settled = row(database, ledger)
    assert settled.status == "closed"
    expected = Decimal(40) * database.limits.live.usd_per_second
    assert settled.accounted_cost == expected.quantize(CENTS)
    assert "failure" not in settled.ledger["live_transport"]
    assert reserved_tokens(database) == 0


def test_repeated_pre_contact_failures_never_exhaust_the_token_quota(live_database):
    database = live_database
    allow_holds(database, 1)
    for _ in range(5):
        ledger = ledger_for(database, limits=roomy(database))
        ledger.reserve()
        ledger.close(PRE_CONTACT)

    assert reserved_tokens(database) == 0
    ledger_for(database, limits=roomy(database)).reserve()  # still admitted


def test_repeated_refusals_after_contact_block_until_swept_and_charged(live_database):
    database = live_database
    allow_holds(database, 1)
    first = ledger_for(database, limits=roomy(database))
    first.reserve()
    first.provider_contact_may_start()
    first.close(CONNECT_REFUSED)

    # Provider contact leaves an unaccounted hold until the expiry sweep charges it.
    held = row(database, first)
    assert (held.status, held.accounted_cost) == ("uncertain", None)
    assert charges(database, first) is None
    with pytest.raises(VoiceUnavailableError, match=r"^voice_budget_held_by_pending_sessions$"):
        ledger_for(database, limits=roomy(database)).reserve()

    assert sweep(database, datetime.now(UTC)) == 0  # inside the close margin
    assert sweep(database, after_expiry(database)) == 1

    charged = row(database, first)
    # A missing receipt is never $0: the full reservation is charged as spend.
    assert (charged.status, charged.accounted_cost) == ("closed", charged.reserved_cost)
    assert charges(database, first).estimated_cost == charged.reserved_cost
    assert reserved_tokens(database) == 0
    ledger_for(database, limits=roomy(database)).reserve()


def test_sweep_releases_abandoned_pre_contact_hold_only_after_expiry(live_database):
    database = live_database
    ledger = ledger_for(database)
    ledger.reserve()  # worker dies before provider contact; close never runs

    assert sweep(database, datetime.now(UTC)) == 0
    assert row(database, ledger).status == "pending"

    assert sweep(database, after_expiry(database)) == 1
    released = row(database, ledger)
    assert (released.status, released.accounted_cost) == ("closed", Decimal(0))
    assert charges(database, ledger) is None


def test_sweep_never_changes_legacy_rows_without_transport_stage(live_database):
    database = live_database
    ledger = ledger_for(database)
    ledger.reserve()
    with database.sessions() as session:
        legacy = session.get(VoiceSessionReservation, ledger.id)
        data = dict(legacy.ledger)
        data.pop("live_transport")
        legacy.ledger, legacy.status = data, "uncertain"
        session.commit()

    assert sweep(database, after_expiry(database)) == 0
    untouched = row(database, ledger)
    assert (untouched.status, untouched.accounted_cost) == ("uncertain", None)
    assert reserved_tokens(database) == tokens_per_hold(database)


def test_sweep_charges_abandoned_post_contact_hold_after_expiry(live_database):
    database = live_database
    ledger = ledger_for(database)
    ledger.reserve()
    ledger.provider_contact_may_start()  # worker dies during connect; close never runs

    assert sweep(database, datetime.now(UTC)) == 0
    assert sweep(database, after_expiry(database)) == 1
    charged = row(database, ledger)
    assert (charged.status, charged.accounted_cost) == ("closed", charged.reserved_cost)
    assert charges(database, ledger).estimated_cost == charged.reserved_cost


def test_concurrent_release_and_provider_contact_never_both_succeed(live_database):
    database = live_database
    allow_holds(database, 10)
    for _ in range(4):
        ledger = ledger_for(database, limits=roomy(database))
        ledger.reserve()
        other = independent_handle(database, ledger)

        def mark(ledger=ledger) -> bool:
            try:
                ledger.provider_contact_may_start()
            except VoiceUnavailableError:
                return False
            return True

        with ThreadPoolExecutor(2) as pool:
            marked = pool.submit(mark)
            pool.submit(other.close, PRE_CONTACT).result(timeout=10)
            sent = marked.result(timeout=10)

        settled = row(database, ledger)
        if sent:
            assert (settled.status, settled.accounted_cost) == ("uncertain", None)
        else:
            assert (settled.status, settled.accounted_cost) == ("closed", Decimal(0))
    assert reserved_tokens(database) == sum(
        r.reserved_tokens
        for r in _rows(database)
        if r.status != "closed" or r.accounted_cost is None
    )


def _rows(database) -> list[VoiceSessionReservation]:
    with database.sessions() as session:
        return list(session.scalars(select(VoiceSessionReservation)))


def test_concurrent_failed_startups_each_release_their_own_hold(live_database):
    database = live_database
    allow_holds(database, 2)
    ledgers = [ledger_for(database, limits=roomy(database)) for _ in range(2)]
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda ledger: ledger.reserve(), ledgers))
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda ledger: ledger.close(PRE_CONTACT), ledgers))

    assert [row(database, ledger).accounted_cost for ledger in ledgers] == [Decimal(0)] * 2
    assert reserved_tokens(database) == 0


class _Browser:
    def __init__(self, database) -> None:
        self.headers = {"origin": "https://staging.example"}
        self.sent: list[dict[str, Any]] = []
        self.database = database
        self._started = False

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if not self._started:
            self._started = True
            return json.dumps({})
        await asyncio.sleep(3600)
        return ""

    async def send_json(self, event: dict[str, Any]) -> None:
        self.sent.append(event)

    async def close(self, code: int | None = None) -> None:
        return None


class _Connect:
    def __init__(self, behaviour: Any) -> None:
        self.behaviour = behaviour

    async def __aenter__(self) -> Any:
        if isinstance(self.behaviour, BaseException):
            raise self.behaviour
        await asyncio.sleep(3600)  # "hang"

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def patch_route(database, monkeypatch, behaviour: Any, ledger_class=LiveLedger):
    created: list[LiveLedger] = []
    connects: list[str] = []

    def ledger_factory(*_args: Any) -> LiveLedger:
        ledger = ledger_class(
            database.sessions,
            database.settings,
            database.limits,
            database.token,
            database.conversation_id,
        )
        created.append(ledger)
        return ledger

    class Client:
        def __init__(self, **_kwargs: Any) -> None:
            self.live = SimpleNamespace(connect=self.connect)

        def connect(self, **_kwargs: Any) -> _Connect:
            connects.append("connect")
            return _Connect(behaviour)

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    settings = SimpleNamespace(
        cors_origins=["https://staging.example"],
        agent_live_enabled=True,
        openai_base_url="https://api.openai.com/v1",
        openai_api_key="fixture",
    )
    monkeypatch.setattr(live_socket, "get_settings", lambda: settings)
    monkeypatch.setattr(live_socket, "load_live_limits", lambda _s: database.limits)
    monkeypatch.setattr(
        live_socket,
        "VoiceStart",
        SimpleNamespace(
            model_validate=lambda _d: SimpleNamespace(
                access_token=database.token, conversation_id=database.conversation_id
            )
        ),
    )
    monkeypatch.setattr(live_socket, "get_session_factory", lambda: database.sessions)
    monkeypatch.setattr(live_socket, "LiveLedger", ledger_factory)
    monkeypatch.setattr(live_socket, "AsyncOpenAI", Client)
    monkeypatch.setattr(live_socket, "PROVIDER_CONNECT_SECONDS", 0.2)
    return created, connects


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("behaviour", "failure", "cancel"),
    [
        (
            InvalidStatus(Response(429, "Too Many Requests", Headers(), b"provider text")),
            CONNECT_REFUSED,
            False,
        ),
        (
            ConnectionRefusedError(111, "refused"),
            {"stage": "provider_connect", "error_class": "ConnectionRefusedError"},
            False,
        ),
        ("hang", {"stage": "provider_connect", "error_class": "TimeoutError"}, False),
        ("hang", {"stage": "provider_connect", "error_class": "CancelledError"}, True),
    ],
    ids=["refused_upgrade", "connection_refused", "timeout", "cancelled"],
)
async def test_route_failure_after_contact_keeps_hold_until_swept(
    live_database, monkeypatch, behaviour, failure, cancel
):
    database = live_database
    created, connects = patch_route(database, monkeypatch, behaviour)
    task = asyncio.create_task(live_socket.live_voice(_Browser(database)))
    if cancel:
        while not connects:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=30)
    else:
        await asyncio.wait_for(task, timeout=30)

    assert connects == ["connect"]
    held = row(database, created[0])
    # Contact was possible: conservative hold, monetary and token, with audit.
    assert (held.status, held.accounted_cost) == ("uncertain", None)
    assert held.ledger["live_transport"] == {"stage": "provider_contact", "failure": failure}
    assert reserved_tokens(database) == tokens_per_hold(database)

    assert sweep(database, after_expiry(database)) == 1
    charged = row(database, created[0])
    assert (charged.status, charged.accounted_cost) == ("closed", charged.reserved_cost)
    assert reserved_tokens(database) == 0


class _SlowContactLedger(LiveLedger):
    """The contact mark is still in flight when the route is cancelled."""

    def provider_contact_may_start(self) -> None:
        import time

        time.sleep(0.5)
        super().provider_contact_may_start()


@pytest.mark.asyncio
async def test_cancellation_racing_the_contact_mark_never_connects_and_settles_once(
    live_database, monkeypatch
):
    database = live_database
    created, connects = patch_route(database, monkeypatch, "hang", _SlowContactLedger)
    task = asyncio.create_task(live_socket.live_voice(_Browser(database)))
    while not created or row(database, created[0]) is None:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=30)

    # Cleanup took the row lock first and released it; the late mark then finds
    # the row closed and refuses, so the provider is never contacted.
    await asyncio.sleep(1)
    released = row(database, created[0])
    assert connects == []
    assert (released.status, released.accounted_cost) == ("closed", Decimal(0))
    assert released.ledger["live_transport"] == {
        "stage": "admitted",
        "failure": {"stage": "contact_boundary", "error_class": "CancelledError"},
    }
    assert reserved_tokens(database) == 0
