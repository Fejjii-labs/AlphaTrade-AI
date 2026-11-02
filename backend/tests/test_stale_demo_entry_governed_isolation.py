"""Q3/E2: governed commands are never touched by the stale manual pass, and a
failing stale pass cannot suppress governed reconciliation results."""

from datetime import timedelta

from sqlalchemy import func, select

from app.db.models import AuditLog, BloFinDemoSyncSnapshot, ExecutionCommand
from app.services import stale_demo_entry_reconciliation as module
from tests.support.phase5_market import EVALUATED_AT
from tests.support.postgres_persistence import requires_postgres
from tests.test_governed_demo_lifecycle import open_world

pytestmark = requires_postgres


def _armed(settings):
    return settings.model_copy(
        update={
            "governed_blofin_demo_enabled": True,
            "watcher_orchestration_enabled": True,
            "watcher_paper_staging_activation": True,
        }
    )


def test_governed_command_is_never_selected_or_resolved(monkeypatch):
    world = open_world(monkeypatch)
    monkeypatch.setattr(module, "STALE_ENTRY_AGE", timedelta(seconds=-3600))
    now = EVALUATED_AT + timedelta(seconds=2)
    with world.factory() as session:
        command = session.get(ExecutionCommand, world.command_id)
        session.add(
            BloFinDemoSyncSnapshot(
                organization_id=command.organization_id,
                user_id=command.user_id,
                synced_at=now,
                health_status="ok",
                provider="blofin_demo",
                exchange_mode="paper_exchange_demo",
                positions_snapshot={"items": [], "truncated": False},
                is_stale=False,
                position_count=0,
            )
        )
        session.commit()
        settings = _armed(world.settings)
        assert module.stale_manual_demo_command_ids(session, settings, now=now) == []
        assert (
            module.reconcile_stale_manual_demo_entries(
                session, settings, clock=lambda: now, provider=world.provider
            )
            == ()
        )
        assert not session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.redacted_metadata["operation"].as_string() == module.OPERATION)
        )


def test_reconcile_pending_survives_stale_pass_failure(monkeypatch):
    world = open_world(monkeypatch)
    world.venue.exited = True

    def boom(*args, **kwargs):
        raise RuntimeError("stale pass exploded")

    monkeypatch.setattr(module, "reconcile_stale_manual_demo_entries", boom)
    with world.factory() as session:
        assert world.loop.reconcile_pending(session) == ("demo_closed_reconciled",)
