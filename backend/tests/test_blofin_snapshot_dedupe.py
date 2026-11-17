"""BloFin demo sync stores a new snapshot only when the account state changed."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.blofin_readonly_access import get_readonly_account_provider
from app.db.models import BloFinDemoSyncSnapshot, User
from app.services.blofin_sync_service import BloFinSyncService
from tests.test_blofin_activity_postgres import world
from tests.test_dashboard_demo_account import transport

__all__ = ["world"]


def _rows(session: Session, organization_id) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(BloFinDemoSyncSnapshot)
            .where(BloFinDemoSyncSnapshot.organization_id == organization_id)
        )
    )


def test_identical_sync_refreshes_the_latest_row_instead_of_adding_one(world, monkeypatch):
    engine, settings, config, scope, _venue = world
    user_id = uuid4()
    with Session(engine) as session:
        session.add(User(id=user_id, email="dedupe@example.test", hashed_password="fixture"))
        session.commit()
    monkeypatch.setattr("app.services.blofin_sync_service.get_activity_settings", lambda: config)
    positions: dict[str, object] = {}

    def bind(**kwargs: object) -> None:
        positions.clear()
        positions.update(kwargs)
        monkeypatch.setattr(
            "app.services.blofin_sync_service.get_readonly_account_provider",
            lambda cfg: get_readonly_account_provider(
                cfg,
                transport=transport(
                    [], identity={"uid": scope.account_uid, "readOnly": 1}, **positions
                ),
            ),
        )

    def sync():
        with Session(engine) as session:
            item = (
                BloFinSyncService(session, settings)
                .sync(organization_id=scope.organization_id, user_id=user_id)
                .snapshot
            )
            session.commit()
            return item

    bind()
    first = sync()
    second = sync()
    with Session(engine) as session:
        assert _rows(session, scope.organization_id) == 1
        row = session.get(BloFinDemoSyncSnapshot, first.id)
        assert row is not None
        # The kept row now carries the newest sync time, so it never reads stale.
        assert second.id == first.id
        assert second.synced_at >= first.synced_at
        assert row.provenance["first_synced_at"] == first.synced_at.isoformat()
        latest = BloFinSyncService(session, settings).latest(organization_id=scope.organization_id)
        assert latest.id == first.id
        assert latest.synced_at == second.synced_at
        assert latest.is_stale is False
        assert latest.account_snapshot == first.account_snapshot

    bind(positions=[])  # the open position closed: a real change
    changed = sync()
    with Session(engine) as session:
        assert changed.id != first.id
        assert changed.position_count == 0
        assert _rows(session, scope.organization_id) == 2


def test_identical_sync_on_a_new_utc_day_keeps_yesterdays_snapshot(world, monkeypatch):
    # Griller PR 466 round 2, B1: an unchanged account across midnight must not
    # move yesterday's only snapshot to today; the daily tier keeps it forever.
    engine, settings, config, scope, _venue = world
    user_id = uuid4()
    with Session(engine) as session:
        session.add(User(id=user_id, email="cross-day@example.test", hashed_password="fixture"))
        session.commit()
    monkeypatch.setattr("app.services.blofin_sync_service.get_activity_settings", lambda: config)
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_readonly_account_provider",
        lambda cfg: get_readonly_account_provider(
            cfg, transport=transport([], identity={"uid": scope.account_uid, "readOnly": 1})
        ),
    )

    def sync():
        with Session(engine) as session:
            item = (
                BloFinSyncService(session, settings)
                .sync(organization_id=scope.organization_id, user_id=user_id)
                .snapshot
            )
            session.commit()
            return item

    first = sync()
    yesterday = first.synced_at - timedelta(days=1)
    with Session(engine) as session:
        session.execute(
            update(BloFinDemoSyncSnapshot)
            .where(BloFinDemoSyncSnapshot.id == first.id)
            .values(synced_at=yesterday)
        )
        session.commit()

    today = sync()

    with Session(engine) as session:
        synced = session.scalars(
            select(BloFinDemoSyncSnapshot.synced_at).where(
                BloFinDemoSyncSnapshot.organization_id == scope.organization_id
            )
        ).all()
    assert today.id != first.id
    assert sorted(at.date() for at in synced) == [yesterday.date(), today.synced_at.date()]
