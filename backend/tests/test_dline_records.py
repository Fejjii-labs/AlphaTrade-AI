"""Local isolated SQLite only: draft template, restart, tenant boundaries and no authority."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Organization, User, UserStrategy, UserStrategyVersion
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.services.compiled_setup_service import CompiledSetupService
from app.strategy_brain.dline.records import record_dline_scan
from app.strategy_brain.service import create_template, details, overview
from tests.test_dline_detection import scan, spec


def test_dline_draft_template_restart_persistence_and_tenant_isolation(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'research.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    org, user = uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all(
            [
                Organization(id=org, name="Synthetic D-line"),
                User(id=user, email=f"{user}@example.com", hashed_password="unused"),
            ]
        )
        session.commit()
        config = spec()
        template = create_template(session, organization_id=org, user_id=user, spec=config)
        assert template["lifecycle"] == "draft" and template["execution_permission"] == "none"
        assert (
            create_template(session, organization_id=org, user_id=user, spec=config)[
                "execution_permission"
            ]
            == "none"
        )
        result = CompiledSetupService(session).compile_version(
            template["version_id"], organization_id=org, user_id=user
        )
        assert result.status == "non_executable"
        research = scan(config=config)
        ids = record_dline_scan(
            session,
            organization_id=org,
            user_id=user,
            version_id=template["version_id"],
            scan=research,
        )
        session.commit()
        assert (
            session.get(UserStrategyVersion, template["version_id"]).pattern_spec["kind"]
            == "d_line/v1"
        )
        with pytest.raises(ValueError):
            record_dline_scan(
                session,
                organization_id=uuid4(),
                user_id=user,
                version_id=template["version_id"],
                scan=research,
            )
        forged = research.model_copy(update={"spec_hash": "0" * 64})
        with pytest.raises(ValueError):
            record_dline_scan(
                session,
                organization_id=org,
                user_id=user,
                version_id=template["version_id"],
                scan=forged,
            )
    engine.dispose()
    with Session(create_engine(url)) as session:
        assert (
            record_dline_scan(
                session,
                organization_id=org,
                user_id=user,
                version_id=template["version_id"],
                scan=research,
            )
            == ids
        )
        session.commit()
        row = session.get(BrainSetupRow, ids[0])
        assert row.candidate_id is None and row.decision_id is None
        assert session.scalar(select(func.count()).select_from(BrainSetupEventRow)) == 1
        view = details(session, organization_id=org, setup_id=ids[0])
        assert view["family"] == "d_line" and view["execution_permission"] == "none"
        assert view["targets"] == [str(t.price) for t in research.candidates[0].targets]
        assert (
            overview(session, organization_id=org, user_id=user, family="d_line")["strategies"][0][
                "execution_permission"
            ]
            == "none"
        )
        from app.core.errors import NotFoundError

        with pytest.raises(NotFoundError):
            details(session, organization_id=uuid4(), setup_id=ids[0])
        assert session.get(UserStrategy, template["strategy_id"]).setup_type.value == "d_line"
