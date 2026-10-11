"""Actual a11→a12 chain, real screening writes and historical preservation."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.strategy_brain.trendpulse_1r.contracts import TrendPulseStatus
from tests.support.experiment_fixtures import World
from tests.support.trendpulse_screening import after_close
from tests.test_trendpulse_1r_domain import configure
from tests.test_trendpulse_screening import request, service


def test_one_additive_revision_follows_reserved_a11():
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(cfg)
    assert scripts.get_heads() == ["a13voicemonitor001"]
    assert scripts.get_revision("a13voicemonitor001").down_revision == "a12trendpulsescreen001"
    assert len(scripts.get_bases()) == 1
    assert [r.revision for r in scripts.iterate_revisions("heads", "a11experiments001")] == [
        "a13voicemonitor001",
        "a12trendpulsescreen001",
    ]
    assert scripts.get_revision("a11experiments001").down_revision == "a10blofinactivity001"


def test_actual_migration_supports_screening_and_roundtrip_preserves_prior_domain_and_native_data(
    monkeypatch,
):
    url = os.getenv("EXPERIMENT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires explicitly supplied disposable EXPERIMENT_TEST_POSTGRES_URL")
    schema = "screening_migration_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = make_url(url).update_query_dict({"options": "-csearch_path=" + schema})
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", isolated.render_as_string(hide_password=False))
    cfg = Config("alembic.ini")
    engine = create_engine(isolated)
    try:
        command.upgrade(cfg, "a11experiments001")
        with Session(engine, expire_on_commit=False) as session:
            w = World(session)
            w.now = after_close()
            w.version = w.create(configure(w))
            w.settings = w.settings.model_copy(update={"trendpulse_screening_enabled": True})
            session.execute(
                text(
                    "INSERT INTO blofin_activity_accounts (organization_id,environment,account_uid,"
                    "credential_binding,identity_verified_at) "
                    "VALUES (:org,'demo','history-uid',:binding,now())"
                ),
                {"org": w.tenant.organization_id, "binding": "a" * 64},
            )
            session.commit()
            frozen_hash = w.version.configuration_hash
            command.upgrade(cfg, "head")
            result = service(w).screen(w.tenant, w.version.experiment_id, w.version.id, request())
            session.commit()
            assert result.status is TrendPulseStatus.QUALIFIED
            original = result.evidence_hash
            command.upgrade(cfg, "head")  # Existing pre-deploy/API repetition.
            assert service(w).detail(w.tenant, result.id).evidence_hash == original
            session.commit()  # Release the read transaction before separate-connection DDL.
            command.downgrade(cfg, "a11experiments001")
            assert "trendpulse_screening_runs" not in inspect(engine).get_table_names()
            assert (
                session.scalar(text("SELECT account_uid FROM blofin_activity_accounts"))
                == "history-uid"
            )
            assert (
                session.scalar(text("SELECT configuration_hash FROM experiment_versions"))
                == frozen_hash
            )
            session.rollback()
            command.upgrade(cfg, "head")
            assert session.scalar(text("SELECT count(*) FROM trendpulse_screening_runs")) == 0
            assert (
                session.scalar(text("SELECT configuration_hash FROM experiment_versions"))
                == frozen_hash
            )
            assert (
                session.scalar(text("SELECT account_uid FROM blofin_activity_accounts"))
                == "history-uid"
            )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
