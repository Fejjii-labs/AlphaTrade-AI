"""Worker recovery of an aged manual BloFin demo entry that blocks governed entries.

Models staging command 80958141: a manual demo BUY 0.1 BTC-USDT filled on
2026-10-08, closed at the venue, never locally resolved, so
``has_demo_entry_history`` blocked every later governed demo entry.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import ExchangeMode
from app.db.models import (
    AuditLog,
    BloFinDemoSyncSnapshot,
    ExecutionCommand,
    KillSwitchState,
    ManualDemoLifecycleResolution,
    Membership,
    RiskReservation,
)
from app.schemas.common import AuditEventType, MembershipRole
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


def _no_kill_switch(session):
    assert not session.scalar(
        select(func.count()).select_from(KillSwitchState).where(KillSwitchState.active.is_(True))
    )
    assert not session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == AuditEventType.KILL_SWITCH_ACTIVATED.value)
    )


def test_refuses_when_venue_position_or_exit_unverified(native):
    """Griller P1/P6: snapshot flat, venue not flat. Never trips the org kill switch."""
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first, flat=False)
    clock = _later(native, first)
    with world[0]() as session:
        _snapshot(session, world, clock())
        assert _run(world, session, _armed(world[2]), clock) == ("stale_entry_venue_mismatch",)
        _no_kill_switch(session)
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
        assert _run(world, session, _armed(world[2]), clock) == ("stale_entry_venue_mismatch",)
        assert _blocked(session, world, first.command_id)
        _no_kill_switch(session)


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


def test_resolve_guard_refuses_instead_of_kill_switch_when_precheck_races(native, monkeypatch):
    """If the venue changes between pre-check and resolve, the worker path still never halts."""
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first, flat=False)
    clock = _later(native, first)
    monkeypatch.setattr(module, "venue_precheck_refusal", lambda provider: None)
    with world[0]() as session:
        _snapshot(session, world, clock())
        assert _run(world, session, _armed(world[2]), clock) == ("stale_entry_venue_mismatch",)
        _no_kill_switch(session)
        assert _blocked(session, world, first.command_id)


def test_exit_unverified_while_flat_is_incomplete_without_kill_switch(native):
    world, changes = native
    _, first = _submit(native)
    entry, _ = set_exit(native, first)
    changes["response"]["/api/v1/trade/orders-history"] = [entry]
    clock = _later(native, first)
    with world[0]() as session:
        _snapshot(session, world, clock())
        assert _run(world, session, _armed(world[2]), clock) == ("stale_entry_evidence_incomplete",)
        _no_kill_switch(session)
        assert _blocked(session, world, first.command_id)


def test_resolves_with_manual_api_arm_off_when_worker_arm_on(native):
    """W2: worker_recovery swaps the API manual-demo arm for the governed worker arm."""
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    clock = _later(native, first)
    settings = _armed(world[2], manual_blofin_demo_enabled=False)
    with world[0]() as session:
        _snapshot(session, world, clock())
        assert _run(world, session, settings, clock) == ("stale_entry_resolved",)
        assert not _blocked(session, world, first.command_id)


def test_non_owner_membership_is_refused(native):
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    clock = _later(native, first)
    with world[0]() as session:
        _snapshot(session, world, clock())
        membership = session.scalar(
            select(Membership).where(Membership.user_id == world[1].user_id)
        )
        membership.role = MembershipRole.VIEWER
        session.commit()
        assert _run(world, session, _armed(world[2]), clock) == ("stale_entry_owner_required",)
        assert _blocked(session, world, first.command_id)


def test_other_pinned_account_command_is_not_selected(native):
    """Q7: commands outside the pinned demo account are never touched."""
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    clock = _later(native, first)
    settings = _armed(world[2], governed_blofin_demo_account_id=str(uuid4()))
    with world[0]() as session:
        _snapshot(session, world, clock())
        assert module.stale_manual_demo_command_ids(session, settings, now=clock()) == []
        assert _blocked(session, world, first.command_id)


def test_command_query_is_scoped_to_manual_demo_allow_unresolved():
    """Q3/Q4/Q5/Q6 structural guard on the selection predicate."""
    from sqlalchemy.dialects import postgresql

    captured = {}

    class _Session:
        def scalars(self, statement):
            captured["sql"] = str(
                statement.compile(
                    dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
                )
            )
            return []

    settings = SimpleNamespace(
        governed_blofin_demo_organization_id=str(uuid4()),
        governed_blofin_demo_user_id=str(uuid4()),
        governed_blofin_demo_account_id=str(uuid4()),
    )
    module.stale_manual_demo_command_ids(_Session(), settings, now=datetime.now(UTC))
    sql = captured["sql"]
    assert "trade_plan_revisions.plan_authority = 'manual_demo_test'" in sql
    assert "trade_plan_revisions.execution_venue = 'BLOFIN_DEMO'" in sql
    assert "execution_commands.outcome = 'ALLOW'" in sql
    assert "manual_demo_lifecycle_resolutions.id IS NULL" in sql
    assert "execution_commands.account_id = " in sql


@pytest.mark.parametrize(
    "field,value",
    [
        ("health_status", "degraded"),
        ("exchange_mode", "paper_internal"),
        ("synced_at_future", None),
        ("truncated", True),
        ("count_only", None),
    ],
)
def test_snapshot_field_guards(native, field, value):
    """F2/F3/F5/F6/F8."""
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    clock = _later(native, first)
    now = clock()
    tenant = world[1]
    row = BloFinDemoSyncSnapshot(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        synced_at=now - timedelta(minutes=1),
        health_status="ok",
        provider="blofin_demo",
        exchange_mode="paper_exchange_demo",
        positions_snapshot={"items": [], "truncated": False},
        is_stale=False,
        position_count=0,
    )
    if field == "health_status":
        row.health_status = value
    elif field == "exchange_mode":
        row.exchange_mode = value
    elif field == "synced_at_future":
        row.synced_at = now + timedelta(minutes=5)
    elif field == "truncated":
        row.positions_snapshot = {"items": [], "truncated": True}
    else:
        row.position_count = 1
    with world[0]() as session:
        session.add(row)
        session.commit()
        outcome = _run(world, session, _armed(world[2]), clock)
        assert outcome in {
            ("stale_entry_snapshot_not_fresh",),
            ("stale_entry_snapshot_position_open",),
        }
        assert _blocked(session, world, first.command_id)


def test_retry_backoff_and_operator_hold_bound_audit_volume(native, monkeypatch):
    """Q2 + C2: exponential backoff on the injected clock, then one operator hold."""
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first, flat=False)
    base = _later(native, first)()
    settings = _armed(world[2])
    monkeypatch.setattr(module, "MAX_ATTEMPTS", 3)

    def at(minutes):
        moment = base + timedelta(minutes=minutes)
        return lambda: moment

    def rows(session):
        return session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.redacted_metadata["operation"].as_string() == OPERATION)
        )

    with world[0]() as session:
        for minutes, expected in [
            (0, ("stale_entry_venue_mismatch",)),  # attempt 1
            (10, ()),  # < 15m
            (16, ("stale_entry_venue_mismatch",)),  # attempt 2
            (40, ()),  # < 30m after attempt 2
            (47, (module.OPERATOR_HOLD,)),  # attempt 3 reaches MAX_ATTEMPTS
            (2000, ()),  # held: never retried
        ]:
            clock = at(minutes)
            _snapshot(session, world, clock())
            assert _run(world, session, settings, clock) == expected, minutes
        assert rows(session) == 4  # 3 attempts + 1 hold
        _no_kill_switch(session)
    assert module.retry_delay(1) == timedelta(minutes=15)
    assert module.retry_delay(3) == timedelta(hours=1)
    assert module.retry_delay(20) == timedelta(hours=6)
