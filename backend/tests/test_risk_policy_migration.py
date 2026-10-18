"""Alembic head, ceiling checks, and upgrade/downgrade of the risk-policy tables."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.db.risk_policy import VERSION_CHECKS
from tests.support.alembic_heads import alembic_script_directory, single_alembic_head

_REVISION = "a16riskpolicy001"
_PARENT = "a15reissueauth001"
_MIGRATION = "a16riskpolicy001_grade_based_risk_policy.py"


def _module():
    path = Path(__file__).resolve().parents[1] / "src/app/db/migrations/versions" / _MIGRATION
    spec = importlib.util.spec_from_file_location(_MIGRATION, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_risk_policy_revision_is_the_only_head() -> None:
    script = alembic_script_directory()
    revision = script.get_revision(_REVISION)
    assert revision is not None
    assert revision.down_revision == _PARENT
    assert single_alembic_head(script) == _REVISION
    source = (
        Path(__file__).resolve().parents[1] / "src/app/db/migrations/versions" / _MIGRATION
    ).read_text(encoding="utf-8")
    for check in VERSION_CHECKS:
        assert check in source


def test_upgrade_downgrade_roundtrip_on_sqlite() -> None:
    module = _module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
        tables = set(inspect(connection).get_table_names())
        assert "risk_policy_versions" in tables
        assert "risk_policy_trade_records" in tables
        org = str(uuid4())
        proposal = str(uuid4())
        version = str(uuid4())
        connection.execute(
            text("INSERT INTO organizations (id) VALUES (:id)"),
            {"id": org},
        )
        connection.execute(
            text(
                "INSERT INTO risk_policy_proposals "
                "(id, organization_id, kind, payload, actor_id, actor_channel, reason, "
                "content_hash, created_at) VALUES "
                "(:id, :org, 'demo', '{}', 'sofien', 'human', 'placeholder', :hash, '2026-10-18')"
            ),
            {"id": proposal, "org": org, "hash": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO risk_policy_trade_records "
                "(id, organization_id, trade_id, profile_version_id, grade, grader_version, "
                "adjusters, calculation, created_at) VALUES "
                "(:id, :org, 'trade-1', :version, 'A', 'deterministic-grade/v1', "
                "'{}', '{}', '2026-10-18')"
            ),
            {"id": str(uuid4()), "org": org, "version": version},
        )
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context), pytest.raises(RuntimeError, match="trade records exist"):
            module.downgrade()
        connection.execute(text("DELETE FROM risk_policy_trade_records"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.downgrade()
        assert "risk_policy_versions" not in set(inspect(connection).get_table_names())
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
        assert "risk_policy_versions" in set(inspect(connection).get_table_names())
