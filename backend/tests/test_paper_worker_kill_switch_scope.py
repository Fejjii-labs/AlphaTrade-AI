"""M3: /health/paper-worker reports only the pinned tenant's kill switch."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import KillSwitchState, Organization, User
from app.db.runtime_status import ControlledRuntimeStatusRow
from app.services.risk.kill_switch import _project_kill_switch


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    for model in (Organization, User, KillSwitchState, ControlledRuntimeStatusRow):
        model.__table__.create(engine)  # type: ignore[attr-defined]
    with Session(engine) as db:
        yield db
    engine.dispose()


def _orgs(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    armed, quiet = uuid.uuid4(), uuid.uuid4()
    session.add_all([Organization(id=armed, name="armed"), Organization(id=quiet, name="quiet")])
    session.flush()
    session.add_all(
        [
            KillSwitchState(
                organization_id=armed, active=True, activated_at=datetime(2026, 11, 7, tzinfo=UTC)
            ),
            KillSwitchState(organization_id=quiet, active=False),
        ]
    )
    session.flush()
    return armed, quiet


def test_unpinned_projection_does_not_report_other_tenants(session) -> None:
    _orgs(session)
    projected = _project_kill_switch(session, Settings(_env_file=None))
    assert projected.active is None
    assert projected.source == "unscoped"
    assert projected.since is None


def test_pinned_projection_reads_only_its_own_tenant(session) -> None:
    armed, quiet = _orgs(session)
    quiet_view = _project_kill_switch(
        session, Settings(_env_file=None, watcher_paper_organization_id=str(quiet))
    )
    assert quiet_view.active is False
    armed_view = _project_kill_switch(
        session, Settings(_env_file=None, watcher_paper_organization_id=str(armed))
    )
    assert armed_view.active is True
    assert armed_view.since is not None
