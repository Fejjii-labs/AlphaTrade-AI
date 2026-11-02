"""Griller #403 re-review: site-2 race, gate and rollback guards, pass cap, venue scope."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text

from app.core.errors import TradingPolicyError
from app.db.models import AuditLog, KillSwitchState, ManualDemoLifecycleResolution
from app.services import stale_demo_entry_reconciliation as module
from app.services.manual_demo_service import ManualDemoService
from tests.support.postgres_persistence import requires_postgres
from tests.test_manual_blofin_demo import confirm, preview
from tests.test_manual_blofin_demo import world as world
from tests.test_manual_demo_lifecycle_workflow import set_exit
from tests.test_manual_demo_reconciliation import _submit
from tests.test_manual_demo_reconciliation import native as native
from tests.test_stale_demo_entry_reconciliation import (
    _armed,
    _blocked,
    _no_kill_switch,
    _run,
    _snapshot,
)

pytestmark = requires_postgres


@pytest.fixture(autouse=True)
def _aged(monkeypatch):
    monkeypatch.setattr(module, "STALE_ENTRY_AGE", timedelta(seconds=-120))


def test_site2_out_of_zone_fill_race_refuses_without_kill_switch(world, monkeypatch):
    """N3: protected but out-of-zone fills, venue not flat, pre-check raced to None."""
    world[3].open_position = False
    plan = preview(world)
    world[3].price = Decimal("100050")
    original = world[4].reconcile

    def outside(**kwargs):
        evidence = original(**kwargs)
        return replace(
            evidence, fills=tuple(replace(f, price=Decimal("100500")) for f in evidence.fills)
        )

    world[4].reconcile = outside
    result = confirm(world, plan)
    assert result.status == "actual_fill_outside_plan_operator_hold"
    world[3].open_position = True
    monkeypatch.setattr(module, "venue_precheck_refusal", lambda provider: None)
    raised = []
    real = ManualDemoService.reconcile

    def spy(self, tenant, command_id):
        try:
            return real(self, tenant, command_id)
        except TradingPolicyError as exc:
            raised.append((exc.details or {}).get("reason"))
            raise

    monkeypatch.setattr(ManualDemoService, "reconcile", spy)
    clock = world[4]._clock
    with world[0]() as session:
        _no_kill_switch(session)
        _snapshot(session, world, clock())
        assert _run(world, session, _armed(world[2]), clock) == ("stale_entry_venue_mismatch",)
        assert raised == ["stale_entry_venue_mismatch"]
        _no_kill_switch(session)
        assert _blocked(session, world, result.command_id)


def test_credential_gate_closed_refuses_before_any_read(native):
    """R4."""
    world, changes = native
    _, first = _submit(native)
    set_exit(native, first)
    clock = world[4]._clock
    settings = _armed(world[2], blofin_demo_enabled=False)
    assert module.stale_entry_refusal(settings) == "stale_entry_credential_gate_closed"
    with world[0]() as session:
        _snapshot(session, world, clock())
        changes["reads"].clear()
        assert _run(world, session, settings, clock) == ()
        assert changes["reads"] == []
        assert _blocked(session, world, first.command_id)


def test_worker_recovery_scope_requires_worker_arm(native):
    """W1: worker_recovery does not bypass the governed worker arm."""
    world, _ = native
    disarmed = world[2].model_copy(update={"governed_blofin_demo_enabled": False})
    with world[0]() as session:
        svc = ManualDemoService(session, disarmed, provider=world[4], worker_recovery=True)
        with pytest.raises(TradingPolicyError):
            svc._scope(world[1])
        armed = ManualDemoService(
            session, _armed(world[2]), provider=world[4], worker_recovery=True
        )
        assert armed._scope(world[1]) is not None


def test_refused_resolve_is_rolled_back(native, monkeypatch):
    """E1: partial writes from a refused resolve are never committed with the audit row."""
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    clock = world[4]._clock

    def poisoned(self, tenant, command_id):
        self.session.add(
            KillSwitchState(organization_id=tenant.organization_id, active=True, reason="x")
        )
        self.session.flush()
        raise TradingPolicyError("refused")

    monkeypatch.setattr(ManualDemoService, "resolve", poisoned)
    with world[0]() as session:
        _snapshot(session, world, clock())
        assert _run(world, session, _armed(world[2]), clock) == ("stale_entry_evidence_incomplete",)
        _no_kill_switch(session)
        assert not session.scalar(select(func.count()).select_from(KillSwitchState))


def test_nonempty_items_with_zero_count_is_refused(native):
    """F7."""
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    clock = world[4]._clock
    with world[0]() as session:
        _snapshot(session, world, clock(), positions=({"instId": "BTC-USDT"},))
        snap = session.scalars(select(module.BloFinDemoSyncSnapshot)).one()
        snap.position_count = 0
        session.commit()
        assert _run(world, session, _armed(world[2]), clock) == (
            "stale_entry_snapshot_position_open",
        )


def test_per_pass_cap(monkeypatch):
    """N9: at most MAX_PER_PASS commands are attempted per pass."""
    ids = [uuid4() for _ in range(module.MAX_PER_PASS + 3)]
    monkeypatch.setattr(module, "stale_manual_demo_command_ids", lambda *a, **k: ids)
    monkeypatch.setattr(module, "attempt_history", lambda *a, **k: (0, None, False))
    settings = type("S", (), {"governed_blofin_demo_organization_id": str(uuid4())})()
    due = module.due_command_ids(None, settings, now=None)
    assert [c for c, _ in due] == ids[: module.MAX_PER_PASS]


def test_non_blofin_demo_venue_is_not_selected(native):
    """Q4 behavioural: the same command on another venue is never selected.

    ck_tpr_manual_origin pins manual plans to BLOFIN_DEMO and revisions are
    immutable, so this disposable clone drops the constraint and bypasses the
    immutability trigger for one raw UPDATE.
    """
    world, _ = native
    with world[0]() as session:
        if not session.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")):
            pytest.skip("needs a superuser to set session_replication_role")
    _, first = _submit(native)
    set_exit(native, first)
    clock = world[4]._clock
    settings = _armed(world[2])
    with world[0]() as session:
        assert module.stale_manual_demo_command_ids(session, settings, now=clock()) == [
            first.command_id
        ]
        dropped = session.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = "
                "'trade_plan_revisions'::regclass AND contype = 'c' "
                "AND pg_get_constraintdef(oid) LIKE '%execution_venue%'"
            )
        ).all()
        for name, _ in dropped:
            session.execute(text(f'ALTER TABLE trade_plan_revisions DROP CONSTRAINT "{name}"'))
        session.execute(text("SET LOCAL session_replication_role = replica"))
        session.execute(
            text(
                "UPDATE trade_plan_revisions SET execution_venue = 'INTERNAL_PAPER' "
                "WHERE id = (SELECT revision_id FROM execution_commands WHERE id = :c)"
            ),
            {"c": first.command_id},
        )
        session.commit()
        try:
            assert module.stale_manual_demo_command_ids(session, settings, now=clock()) == []
            _snapshot(session, world, clock())
            assert _run(world, session, settings, clock) == ()
            assert not session.scalar(
                select(func.count()).select_from(ManualDemoLifecycleResolution)
            )
        finally:
            session.rollback()
            # N1: leave the disposable clone with its original venue CHECK.
            session.execute(text("SET LOCAL session_replication_role = replica"))
            session.execute(
                text(
                    "UPDATE trade_plan_revisions SET execution_venue = 'BLOFIN_DEMO' "
                    "WHERE id = (SELECT revision_id FROM execution_commands WHERE id = :c)"
                ),
                {"c": first.command_id},
            )
            for name, definition in dropped:
                session.execute(
                    text(f'ALTER TABLE trade_plan_revisions ADD CONSTRAINT "{name}" {definition}')
                )
            session.commit()


def test_held_command_excluded_in_sql(native, monkeypatch):
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first, flat=False)
    clock = world[4]._clock
    monkeypatch.setattr(module, "MAX_ATTEMPTS", 1)
    with world[0]() as session:
        _snapshot(session, world, clock())
        assert _run(world, session, _armed(world[2]), clock) == (module.OPERATOR_HOLD,)
        assert module.stale_manual_demo_command_ids(session, _armed(world[2]), now=clock()) == []
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.redacted_metadata["outcome"].as_string() == module.OPERATOR_HOLD)
            )
            == 1
        )


def test_attempt_history_is_scoped_to_organization(native):
    """ORG1: attempt rows from one organization never count for another."""
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first, flat=False)
    clock = world[4]._clock
    settings = _armed(world[2])
    org = UUID(settings.governed_blofin_demo_organization_id)
    with world[0]() as session:
        _snapshot(session, world, clock())
        _run(world, session, settings, clock)
        attempts, last, held = module.attempt_history(
            session, first.command_id, organization_id=org
        )
        assert attempts == 1 and last is not None and not held
        assert module.attempt_history(session, first.command_id, organization_id=uuid4()) == (
            0,
            None,
            False,
        )


def test_venue_check_restored_after_q4(native):
    """N1: runs after Q4 in file order; the venue CHECK must still exist."""
    world, _ = native
    with world[0]() as session:
        assert session.scalar(
            text(
                "SELECT count(*) FROM pg_constraint WHERE conrelid = "
                "'trade_plan_revisions'::regclass AND contype = 'c' "
                "AND pg_get_constraintdef(oid) LIKE '%execution_venue%'"
            )
        )
