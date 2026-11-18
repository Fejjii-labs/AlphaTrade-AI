"""Postgres upgrade, downgrade, and re-upgrade of a29datapolicy001 (data retention policy)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.support.alembic_heads import (
    alembic_ancestry,
    alembic_script_directory,
    assert_down_revision_chain,
    single_alembic_head,
)
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres

_A28 = "a28promptsha001"
_A29 = "a29datapolicy001"
_SUMMARY = "trade_candidate_summaries"
_INDEXES = {
    ("audit_logs", "ix_audit_logs_event_at"),
    ("strategy_brain_setup_events", "ix_brain_event_kind_time"),
    ("canonical_candidates", "ix_canonical_candidates_created_at"),
    ("telegram_paper_notifications", "ix_tg_paper_notify_created_at"),
    ("public_market_observations", "ix_public_observation_bar_start"),
}


def _config() -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config()
    config.set_main_option("script_location", str(backend_root / "src/app/db/migrations"))
    return config


def _version(engine: Engine) -> str | None:
    with engine.connect() as connection:
        value = connection.scalar(text("SELECT version_num FROM alembic_version"))
    return value if isinstance(value, str) else None


def _indexes(engine: Engine) -> set[tuple[str, str]]:
    found = set()
    inspector = inspect(engine)
    for table, _name in _INDEXES:
        for index in inspector.get_indexes(table):
            found.add((table, index["name"]))
    return found & _INDEXES


def _seed_candidate(engine: Engine) -> str:
    candidate_id = str(uuid4())
    now = datetime(2026, 11, 1, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO canonical_candidates (candidate_id, organization_id, "
                "uniqueness_hash, schema_version, strategy_version_id, setup_definition_id, "
                "fusion_policy_version, direction, evidence_venue, evidence_market, "
                "evidence_instrument, timeframe, evidence_window_hash, assessment_id, "
                "executable_setup, evidence_identity, state, created_at, valid_until, "
                "transition_version, idempotency_key, correlation_id, content_hash) VALUES "
                "(:id, :org, :h1, 'v1', :sv, :sd, 'f1', 'long', 'binance', 'perp', 'BTCUSDT', "
                "'1h', :h2, :a, '{}', '{}', 'published', :at, :until, 1, :key, :corr, :h3)"
            ),
            {
                "id": candidate_id,
                "org": str(uuid4()),
                "h1": "a" * 64,
                "h2": "b" * 64,
                "h3": "c" * 64,
                "sv": str(uuid4()),
                "sd": str(uuid4()),
                "a": str(uuid4()),
                "at": now,
                "until": now + timedelta(hours=4),
                "key": "k-" + candidate_id,
                "corr": str(uuid4()),
            },
        )
        connection.execute(
            text(
                "INSERT INTO canonical_candidate_transitions (transition_id, organization_id, "
                "candidate_id, previous_state, new_state, transition_version, reason_codes, "
                "occurred_at, correlation_id, idempotency_key, content_hash) VALUES "
                "(:tid, :org, :id, 'none', 'published', 1, :codes, :at, :corr, 'k', :h)"
            ),
            {
                "tid": str(uuid4()),
                "org": str(uuid4()),
                "id": candidate_id,
                "codes": json.dumps([]),
                "at": now,
                "corr": str(uuid4()),
                "h": "d" * 64,
            },
        )
    return candidate_id


def _delete(engine: Engine, candidate_id: str, *, purge: bool) -> None:
    with engine.begin() as connection:
        if purge:
            connection.execute(
                text(
                    "SELECT set_config('alphatrade.retention_purge', 'canonical_candidates', true)"
                )
            )
        connection.execute(
            text("DELETE FROM canonical_candidate_transitions WHERE candidate_id = :id"),
            {"id": candidate_id},
        )
        connection.execute(
            text("DELETE FROM canonical_candidates WHERE candidate_id = :id"), {"id": candidate_id}
        )


def test_a29_revises_a28_on_a_single_head() -> None:
    script = alembic_script_directory()
    assert_down_revision_chain(script, (_A29, _A28))
    head = single_alembic_head(script)
    assert script.get_heads() == [head]
    assert _A29 in alembic_ancestry(head)


@requires_postgres
def test_a29_upgrade_downgrade_reupgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Head adds the summary table, indexes and the gated purge; a28 restores strict triggers."""

    admin = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    schema = "data_policy_" + uuid4().hex
    isolated = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", isolated.render_as_string(hide_password=False))
    config = _config()
    script = ScriptDirectory.from_config(config)
    assert_down_revision_chain(script, (_A29, _A28))
    engine = create_engine(isolated, poolclass=NullPool, future=True)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(config, "head")
        assert _version(engine) == single_alembic_head(script)
        assert _SUMMARY in inspect(engine).get_table_names()
        assert _indexes(engine) == _INDEXES

        # Append-only still holds for ordinary sessions.
        kept = _seed_candidate(engine)
        with pytest.raises(DBAPIError, match="append-only"):
            _delete(engine, kept, purge=False)
        # History rows still refuse UPDATE even inside the retention purge.
        with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config('alphatrade.retention_purge', 'canonical_candidates', true)"
                )
            )
            connection.execute(
                text(
                    "UPDATE canonical_candidate_transitions SET new_state = 'x' "
                    "WHERE candidate_id = :id"
                ),
                {"id": kept},
            )
        # The retention purge (transaction-local setting) may delete.
        _delete(engine, kept, purge=True)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM canonical_candidates")) == 0

        command.downgrade(config, _A28)
        assert _version(engine) == _A28
        assert _SUMMARY not in inspect(engine).get_table_names()
        assert not _indexes(engine)
        strict = _seed_candidate(engine)
        with pytest.raises(DBAPIError, match="append-only"):
            _delete(engine, strict, purge=True)

        command.upgrade(config, "head")
        assert _version(engine) == single_alembic_head(script)
        assert _SUMMARY in inspect(engine).get_table_names()
        assert _indexes(engine) == _INDEXES
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
