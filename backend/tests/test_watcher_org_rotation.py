"""M9: the watcher's org rotation skips tenants that cannot produce a scan target."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import Organization, User, UserStrategy
from app.db.watcher_assignments import WatcherAssignmentReceiptRow
from app.workers.watcher_paper import _has_scan_authority


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    tables = [
        Organization.__table__,
        User.__table__,
        UserStrategy.__table__,
        WatcherAssignmentReceiptRow.__table__,
    ]
    for table in tables:
        table.create(engine)  # type: ignore[attr-defined]
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_rotation_predicate_selects_only_orgs_with_strategies_or_assignments(session) -> None:
    from datetime import UTC, datetime

    from app.schemas.common import StrategyId

    orgs = {
        name: Organization(id=uuid.uuid4(), name=name) for name in ("empty", "on", "off", "asg")
    }
    session.add_all(orgs.values())
    user = User(id=uuid.uuid4(), email="m9@example.test", hashed_password="x")
    session.add(user)
    session.flush()
    setup = next(iter(StrategyId))
    session.add_all(
        [
            UserStrategy(
                organization_id=orgs["on"].id, user_id=user.id, name="s", setup_type=setup
            ),
            UserStrategy(
                organization_id=orgs["off"].id,
                user_id=user.id,
                name="s",
                setup_type=setup,
                enabled=False,
            ),
            WatcherAssignmentReceiptRow(
                organization_id=orgs["asg"].id,
                request_key="k",
                user_id=user.id,
                payload_hash="0" * 64,
                configuration_revision=1,
                response={},
                created_at=datetime(2026, 11, 7, tzinfo=UTC),
            ),
        ]
    )
    session.flush()
    selected = set(
        session.scalars(select(Organization.id).where(_has_scan_authority(Organization.id)))
    )
    assert selected == {orgs["on"].id, orgs["asg"].id}
