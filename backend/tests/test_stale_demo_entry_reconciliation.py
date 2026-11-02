"""Worker recovery of an aged manual BloFin demo entry that blocks governed entries.

Models staging command 80958141: a manual demo BUY 0.1 BTC-USDT filled on
2026-10-08, closed at the venue, never locally resolved, so
``has_demo_entry_history`` blocked every later governed demo entry.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import ExchangeMode
from app.db.models import (
    AuditLog,
    BloFinDemoSyncSnapshot,
    ExecutionCommand,
    ManualDemoLifecycleResolution,
    RiskReservation,
)
from app.schemas.execution_protocol import RiskReservationReleaseState
from app.services import stale_demo_entry_reconciliation as module
from app.services.demo_account_history import has_demo_entry_history
from app.services.stale_demo_entry_reconciliation import (
    OPERATION,
    reconcile_stale_manual_demo_entries,
    stale_entry_refusal,
)
from tests.support.postgres_persistence import requires_postgres
from tests.test_manual_blofin_demo import world as world
from tests.test_manual_demo_lifecycle_workflow import set_exit
from tests.test_manual_demo_reconciliation import _submit
from tests.test_manual_demo_reconciliation import native as native

pytestmark = requires_postgres


def _armed(settings, **extra):
    return settings.model_copy(
        update={
            "governed_blofin_demo_enabled": True,
            "watcher_orchestration_enabled": True,
            "watcher_paper_staging_activation": True,
            **extra,
        }
    )


@pytest.fixture(autouse=True)
def _aged(monkeypatch):
    """Treat the fixture entry as aged; the venue fixture clock is pinned to 2026-10-08."""
    monkeypatch.setattr(module, "STALE_ENTRY_AGE", timedelta(seconds=-120))


def _later(native, first, hours=2):
    del first, hours
    return native[0][4]._clock


def _snapshot(session, world, now, *, stale=False, positions=(), age=timedelta(minutes=1)):
    tenant = world[1]
    session.add(
        BloFinDemoSyncSnapshot(
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            synced_at=now - age,
            health_status="ok",
            provider="blofin_demo",
            exchange_mode="paper_exchange_demo",
            positions_snapshot={"items": list(positions), "truncated": False},
            is_stale=stale,
            position_count=len(positions),
        )
    )
    session.commit()


def _blocked(session, world, command_id):
    command = session.get(ExecutionCommand, command_id)
    return has_demo_entry_history(
        session, organization_id=command.organization_id, account_id=command.account_id
    )


def _run(world, session, settings, clock):
    return reconcile_stale_manual_demo_entries(session, settings, clock=clock, provider=world[4])


def test_resolves_aged_closed_entry_and_unblocks_demo_history(native):
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    clock = _later(native, first)
    settings = _armed(world[2])
    with world[0]() as session:
        assert _blocked(session, world, first.command_id)
        _snapshot(session, world, clock())
        posts = world[3].post_count
        assert _run(world, session, settings, clock) == ("stale_entry_resolved",)
        assert world[3].post_count == posts  # never sends venue orders
        assert session.scalar(select(func.count()).select_from(ManualDemoLifecycleResolution)) == 1
        reservation = session.scalar(select(RiskReservation))
        assert reservation.release_state is RiskReservationReleaseState.RELEASED
        assert reservation.remaining_reserved_notional == 0
        assert not _blocked(session, world, first.command_id)
        audit = session.scalars(
            select(AuditLog).where(AuditLog.resource_id == str(first.command_id))
        ).all()
        assert any(
            a.redacted_metadata.get("operation") == OPERATION
            and a.redacted_metadata.get("outcome") == "stale_entry_resolved"
            for a in audit
        )
        # Idempotent: resolved commands are not revisited.
        assert _run(world, session, settings, clock) == ()


@pytest.mark.parametrize(
    "snapshot_kwargs,reason",
    [
        ({"stale": True}, "stale_entry_snapshot_not_fresh"),
        ({"age": timedelta(minutes=30)}, "stale_entry_snapshot_not_fresh"),
        (
            {"positions": ({"instId": "BTC-USDT", "positions": "0.1"},)},
            "stale_entry_snapshot_position_open",
        ),
        (None, "stale_entry_snapshot_missing"),
    ],
)
def test_refuses_without_fresh_flat_snapshot(native, snapshot_kwargs, reason):
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    clock = _later(native, first)
    with world[0]() as session:
        if snapshot_kwargs is not None:
            _snapshot(session, world, clock(), **snapshot_kwargs)
        assert _run(world, session, _armed(world[2]), clock) == (reason,)
        assert session.scalar(select(func.count()).select_from(ManualDemoLifecycleResolution)) == 0
        assert _blocked(session, world, first.command_id)


def test_refuses_when_venue_position_or_exit_unverified(native):
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first, flat=False)
    clock = _later(native, first)
    with world[0]() as session:
        _snapshot(session, world, clock())
        assert _run(world, session, _armed(world[2]), clock) == ("stale_entry_evidence_incomplete",)
        assert session.scalar(select(func.count()).select_from(ManualDemoLifecycleResolution)) == 0
        assert session.scalar(select(RiskReservation)).remaining_reserved_notional > 0
        assert _blocked(session, world, first.command_id)


def test_refuses_open_orders(native):
    world, changes = native
    _, first = _submit(native)
    set_exit(native, first)
    changes["response"]["/api/v1/trade/orders-pending"] = [
        {"orderId": "777", "instId": "BTC-USDT", "side": "buy", "state": "live", "size": "0.1"}
    ]
    clock = _later(native, first)
    with world[0]() as session:
        _snapshot(session, world, clock())
        assert _run(world, session, _armed(world[2]), clock) == ("stale_entry_evidence_incomplete",)
        assert _blocked(session, world, first.command_id)


def test_young_entry_is_not_touched(native, monkeypatch):
    monkeypatch.setattr(module, "STALE_ENTRY_AGE", timedelta(hours=1))
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    clock = _later(native, first, hours=0)
    with world[0]() as session:
        _snapshot(session, world, clock())
        assert _run(world, session, _armed(world[2]), clock) == ()
        assert _blocked(session, world, first.command_id)


@pytest.mark.parametrize(
    "update,reason",
    [
        ({"exchange_mode": ExchangeMode.PAPER_INTERNAL}, "stale_entry_not_demo_mode"),
        ({"enable_real_trading": True}, "stale_entry_live_refused"),
        ({"governed_blofin_demo_enabled": False}, "stale_entry_demo_worker_disarmed"),
    ],
)
def test_refuses_outside_demo(native, update, reason):
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    clock = _later(native, first)
    settings = _armed(world[2], **update)
    assert stale_entry_refusal(settings) == reason
    with world[0]() as session:
        _snapshot(session, world, clock())
        assert _run(world, session, settings, clock) == ()
        assert _blocked(session, world, first.command_id)
