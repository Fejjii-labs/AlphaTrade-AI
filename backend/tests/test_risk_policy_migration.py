"""Alembic head, ceiling checks, and upgrade/downgrade of the risk-policy tables."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError, OperationalError, ProgrammingError
from sqlalchemy.pool import NullPool

from app.db.risk_policy import VERSION_CHECKS
from tests.support.alembic_heads import (
    alembic_ancestry,
    alembic_script_directory,
    assert_down_revision_chain,
    single_alembic_head,
)

_A16 = "a16riskpolicy001"
_A17 = "a17riskpolicy002"
_A18 = "a18riskpolicy003"
_A19 = "a19riskpolicy004"
_A20 = "a20autonomoussettings001"
_A21 = "a21killswitchkey001"
_PARENT = "a15reissueauth001"
_A16_MIGRATION = "a16riskpolicy001_grade_based_risk_policy.py"
_A17_MIGRATION = "a17riskpolicy002_risk_policy_review_guards.py"
_A18_MIGRATION = "a18riskpolicy003_breaker_intervals.py"
_A19_MIGRATION = "a19riskpolicy004_paper_demo_risk_ceiling.py"
_A16_RISK_CHECKS = (
    "risk_percent_a_plus > 0 AND risk_percent_a_plus <= 2",
    "risk_percent_a > 0 AND risk_percent_a <= 2",
    "risk_percent_b_plus > 0 AND risk_percent_b_plus <= 2",
    "risk_percent_b > 0 AND risk_percent_b <= 2",
)


def _load(filename: str):
    path = Path(__file__).resolve().parents[1] / "src/app/db/migrations/versions" / filename
    spec = importlib.util.spec_from_file_location(filename, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module():
    return _load(_A16_MIGRATION)


def test_risk_policy_revision_follows_its_parent_chain() -> None:
    script = alembic_script_directory()
    assert_down_revision_chain(
        script,
        (_A21, _A20, _A19, _A18, _A17, _A16, _PARENT),
    )
    head = single_alembic_head(script)
    assert _A21 in alembic_ancestry(head)
    versions = Path(__file__).resolve().parents[1] / "src/app/db/migrations/versions"
    source = (versions / _A16_MIGRATION).read_text(encoding="utf-8")
    for check in _A16_RISK_CHECKS:
        assert check in source
    ceiling = _load(_A19_MIGRATION)
    for column, check in zip(
        (
            "risk_percent_a_plus",
            "risk_percent_a",
            "risk_percent_b_plus",
            "risk_percent_b",
        ),
        VERSION_CHECKS[:4],
        strict=True,
    ):
        assert ceiling._kind_risk_check(column) == check
    revised = (versions / _A19_MIGRATION).read_text(encoding="utf-8")
    for check in (
        "default_leverage >= 1 AND default_leverage <= max_leverage AND default_leverage <= 20",
        "(paper_demo_data_collection = false OR kind IN ('internal_paper', 'demo'))",
    ):
        assert check in revised
        assert check in VERSION_CHECKS
    guard = (versions / _A17_MIGRATION).read_text(encoding="utf-8")
    assert "risk_policy_versions_immutable" in guard
    assert "risk_policy_version_immutable" in guard
    intervals = (versions / _A18_MIGRATION).read_text(encoding="utf-8")
    assert "risk_policy_breaker_intervals" in intervals


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
        assert engine.dialect.name == "sqlite"


def test_a16_downgrade_refuses_when_breaker_rows_exist() -> None:
    module = _module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    assert engine.dialect.name == "sqlite"
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
        org = str(uuid4())
        connection.execute(text("INSERT INTO organizations (id) VALUES (:id)"), {"id": org})
        connection.execute(
            text(
                "INSERT INTO risk_policy_breaker_states "
                "(id, organization_id, account_id, equity_peak, drawdown_latched, "
                "consecutive_losses, updated_at) VALUES "
                "(:id, :org, :account, 10000, 1, 4, '2026-10-18')"
            ),
            {"id": str(uuid4()), "org": org, "account": str(uuid4())},
        )
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context), pytest.raises(RuntimeError, match="breaker state exists"):
            module.downgrade()
        connection.execute(text("DELETE FROM risk_policy_breaker_states"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.downgrade()
        assert "risk_policy_breaker_states" not in set(inspect(connection).get_table_names())


def test_a17_upgrade_creates_review_tables_on_sqlite() -> None:
    a16 = _module()
    a17 = _load(_A17_MIGRATION)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    assert engine.dialect.name == "sqlite"
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            a16.upgrade()
            a17.upgrade()
        tables = set(inspect(connection).get_table_names())
        assert "risk_policy_version_cancellations" in tables
        assert "risk_policy_promotion_reports" in tables
        assert "risk_policy_breaker_resets" in tables
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("risk_policy_breaker_states")
        }
        assert "realized_pnl" in columns
        assert "unrealized_pnl" in columns
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            a17.downgrade()
        tables = set(inspect(connection).get_table_names())
        assert "risk_policy_version_cancellations" not in tables
        assert "risk_policy_breaker_states" in tables


def _postgres_url() -> str | None:
    return (
        os.environ.get("AT028_POSTGRES_URL")
        or os.environ.get("PHASE1_POSTGRES_URL")
        or os.environ.get("EXPERIMENT_TEST_POSTGRES_URL")
    )


def test_postgres_trigger_makes_profile_versions_immutable() -> None:
    raw = _postgres_url()
    if not raw:
        pytest.skip("Set AT028_POSTGRES_URL to disposable loopback PostgreSQL.")
    url = make_url(raw)
    if url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "localhost"}:
        pytest.skip("Risk-policy trigger test requires loopback PostgreSQL.")
    schema = "risk_policy_guard_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(isolated, poolclass=NullPool)
    a16 = _module()
    a17 = _load(_A17_MIGRATION)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        with engine.begin() as connection:
            assert connection.dialect.name == "postgresql"
            connection.execute(text("CREATE TABLE organizations (id UUID PRIMARY KEY)"))
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                a16.upgrade()
                a17.upgrade()
            org = str(uuid4())
            proposal = str(uuid4())
            version = str(uuid4())
            connection.execute(text("INSERT INTO organizations (id) VALUES (:id)"), {"id": org})
            connection.execute(
                text(
                    "INSERT INTO risk_policy_proposals "
                    "(id, organization_id, kind, payload, actor_id, actor_channel, reason, "
                    "content_hash, created_at) VALUES "
                    "(:id, :org, 'demo', '{}', 'sofien', 'human', 'placeholder', :hash, "
                    "TIMESTAMPTZ '2026-10-18 15:00:00+00')"
                ),
                {"id": proposal, "org": org, "hash": "a" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO risk_policy_versions ("
                    "id, organization_id, proposal_id, kind, version_number, "
                    "risk_percent_a_plus, risk_percent_a, risk_percent_b_plus, risk_percent_b, "
                    "risk_percent_c, max_leverage, daily_loss_limit_percent, "
                    "max_aggregate_open_risk_percent, max_open_positions, "
                    "max_notional_per_trade_percent, max_aggregate_notional_percent, "
                    "min_stop_atr_multiple, min_stop_absolute_floor, max_cost_share_of_r, "
                    "drawdown_breaker_percent, consecutive_loss_limit, "
                    "autonomous_internal_paper_enabled, autonomous_blofin_demo_enabled, "
                    "change_class, effective_at, actor_id, actor_channel, reason, diff, "
                    "content_hash, created_at"
                    ") VALUES ("
                    ":id, :org, :proposal, 'demo', 1, "
                    "1, 1, 1, 1, 0, 1, 1, 1, 1, 10, 25, 1, 0.001, 0.20, 10, 4, "
                    "false, false, 'initial', TIMESTAMPTZ '2026-10-18 15:00:00+00', "
                    "'sofien', 'human', 'placeholder', '{}', :hash, "
                    "TIMESTAMPTZ '2026-10-18 15:00:00+00')"
                ),
                {"id": version, "org": org, "proposal": proposal, "hash": "b" * 64},
            )
        with (
            engine.begin() as connection,
            pytest.raises(
                (ProgrammingError, OperationalError, DatabaseError),
                match="risk_policy_version_immutable",
            ),
        ):
            connection.execute(
                text("UPDATE risk_policy_versions SET reason = 'rewrite' WHERE id = :id"),
                {"id": version},
            )
        with (
            engine.begin() as connection,
            pytest.raises(
                (ProgrammingError, OperationalError, DatabaseError),
                match="risk_policy_version_immutable",
            ),
        ):
            connection.execute(
                text("DELETE FROM risk_policy_versions WHERE id = :id"),
                {"id": version},
            )
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
        admin.dispose()
