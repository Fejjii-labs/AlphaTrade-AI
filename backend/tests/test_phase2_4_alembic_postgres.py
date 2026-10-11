"""Alembic cycle for PR 77 hardening migrations on PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

POSTGRES_URL = os.environ.get(
    "PHASE1_POSTGRES_URL",
    os.environ.get(
        "AT028_POSTGRES_URL",
        "postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade_test",
    ),
)

MAIN_HEAD = "6d4e9f0a12b3"
PHASE2 = "7e8f1a2b3c4d"
PHASE3 = "8a9b0c1d2e3f"
PHASE4 = "9b0c1d2e3f4a"
HARDENING = "a0c1d2e3f4b5"
WATCHER_TELEGRAM_PERSISTENCE = "3ec264f9aaa8"
CANONICAL_CANDIDATE_PERSISTENCE = "4fd8c1a90b27"
CANONICAL_TRADE_PLAN_ELIGIBILITY = "c9e2b4a1d078"
LEARNING_ATTRIBUTION_PERSISTENCE = "d4f7a2c8e901"
JOURNAL_TRADES_ACCOUNT_ID = "e8f1c4a9b702"
STRATEGY_CONVERSATION_PERSISTENCE = "b7c8d9e0f1a2"
SETUP_LIFETIME_PINS = "c8d9e0f1a2b3"
PAPER_EVALUATION_OBSERVATIONS = "e3f4a5b6c7d8"
TELEGRAM_PAPER_AGENT = "d9e0f1a2b3c4"
TELEGRAM_PAPER_ACTIVATION = "e0f1a2b3c4d5"
CONTROLLED_RUNTIME_STATUS = "f1a2b3c4d5e6"
WORKER_PROCESS_MEMORY = "a8c3e1b94d20"
WATCHER_TENANT_WATCHLIST = "b6f2d9a10e73"
STRATEGY_BRAIN = "a1brain001"
TELEGRAM_POLICY_V2 = "a2tgpolicy002"
RELEASE_WAVE002 = "a3release002"
CURRENT_HEAD = "a13voicemonitor001"

_NEW_TABLES = (
    "watcher_worker_leases",
    "watcher_scheduled_scans",
    "watcher_scan_lineages",
    "telegram_security_action_nonces",
    "telegram_security_action_receipts",
    "telegram_security_outbox",
    "telegram_security_enrollment_challenges",
    "telegram_security_bindings",
    "telegram_security_authorization_intents",
    "canonical_candidates",
    "canonical_candidate_creation_keys",
    "canonical_candidate_transitions",
    "canonical_candidate_transition_keys",
    "action_eligibility_evaluations",
    "action_eligibility_identity_bindings",
    "canonical_trade_plan_roots",
    "canonical_trade_plan_lineage",
    "canonical_trade_plan_idempotency_keys",
    "learning_attribution_records",
    "learning_attribution_events",
    "conversations",
    "conversation_messages",
    "strategy_conversation_proposals",
    "strategy_version_conversation_links",
    "setup_lifetime_pins",
    "paper_evaluation_observations",
    "telegram_paper_notifications",
    "telegram_paper_threads",
    "telegram_paper_messages",
    "telegram_paper_confirmations",
    "controlled_runtime_status",
)


def _postgres_available() -> bool:
    try:
        engine = create_engine(POSTGRES_URL, poolclass=NullPool)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason=f"PostgreSQL not reachable at {POSTGRES_URL}",
)


def _alembic_config() -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    ini = backend_root / "alembic.ini"
    config = Config(str(ini))
    config.set_main_option("sqlalchemy.url", POSTGRES_URL)
    config.set_main_option("script_location", str(backend_root / "src/app/db/migrations"))
    os.environ["ALEMBIC_DATABASE_URL"] = POSTGRES_URL
    return config


@requires_postgres
def test_pr77_alembic_upgrade_downgrade_reupgrade() -> None:
    engine = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = _alembic_config()
    command.upgrade(config, "head")
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == CURRENT_HEAD
        for table_name in _NEW_TABLES:
            present = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = :name"
                ),
                {"name": table_name},
            ).scalar()
            assert present == 1, table_name
        has_pattern = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'user_strategy_versions' AND column_name = 'pattern_spec'"
            )
        ).scalar()
        assert has_pattern == 1
        nullable = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'user_strategy_versions' AND column_name = 'content_hash'"
            )
        ).scalar()
        assert nullable == "NO"
        trigger = conn.execute(
            text(
                "SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'trg_user_strategy_versions_semantic_immutable'"
            )
        ).scalar()
        assert trigger == 1

    command.downgrade(config, HARDENING)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == HARDENING
        for table_name in _NEW_TABLES:
            missing = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = :name"
                ),
                {"name": table_name},
            ).scalar()
            assert missing is None, table_name

    command.downgrade(config, PHASE4)
    command.downgrade(config, PHASE3)
    command.downgrade(config, PHASE2)
    command.downgrade(config, MAIN_HEAD)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == MAIN_HEAD
        missing = conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = 'model_call_attempts'")
        ).scalar()
        assert missing is None

    command.upgrade(config, "head")
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == CURRENT_HEAD
        count = conn.execute(text("SELECT COUNT(*) FROM user_strategy_versions")).scalar()
        assert int(count or 0) == 0
        backfill_ok = conn.execute(
            text(
                "SELECT COUNT(*) FROM user_strategy_versions "
                "WHERE content_hash IS NULL OR length(content_hash) <> 64"
            )
        ).scalar()
        assert int(backfill_ok or 0) == 0
    engine.dispose()


def test_alembic_single_head() -> None:
    config = _alembic_config()
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [CURRENT_HEAD]
    head = script.get_revision(CURRENT_HEAD)
    assert head is not None
    assert head.down_revision == "a12trendpulsescreen001"
    assert script.get_revision("a12trendpulsescreen001").down_revision == "a11experiments001"
    assert script.get_revision("a11experiments001").down_revision == "a10blofinactivity001"
    assert script.get_revision("a10blofinactivity001").down_revision == "a9knowledgeoutbox001"
    assert script.get_revision("a9knowledgeoutbox001").down_revision == "a8agentcapture001"
    assert script.get_revision("a8agentcapture001").down_revision == "a7manualrecovery001"
    assert script.get_revision("a7manualrecovery001").down_revision == "a6manualdemo001"
    assert script.get_revision("a6manualdemo001").down_revision == "a5demolifecycle001"
    assert script.get_revision("a5demolifecycle001").down_revision == "a4knowledge001"
    assert script.get_revision("a4knowledge001").down_revision == RELEASE_WAVE002
    merge = script.get_revision(RELEASE_WAVE002)
    assert merge is not None
    assert set(merge.down_revision) == {TELEGRAM_POLICY_V2, "a2sfp002"}
    assert script.get_revision(TELEGRAM_POLICY_V2).down_revision == STRATEGY_BRAIN
    assert script.get_revision("a2sfp002").down_revision == STRATEGY_BRAIN
    assert script.get_revision(STRATEGY_BRAIN).down_revision == WATCHER_TENANT_WATCHLIST
    assert script.get_revision(WATCHER_TENANT_WATCHLIST).down_revision == WORKER_PROCESS_MEMORY
    assert script.get_revision(WORKER_PROCESS_MEMORY).down_revision == CONTROLLED_RUNTIME_STATUS
    revisions = {rev.revision for rev in script.walk_revisions(base="base", head=CURRENT_HEAD)}
    assert STRATEGY_BRAIN in revisions
    assert TELEGRAM_POLICY_V2 in revisions
    assert "a2sfp002" in revisions
    assert LEARNING_ATTRIBUTION_PERSISTENCE in revisions
    assert TELEGRAM_PAPER_ACTIVATION in revisions


@requires_postgres
def test_canonical_candidate_alembic_upgrade_downgrade() -> None:
    engine = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = _alembic_config()
    command.upgrade(config, "head")
    candidate_tables = (
        "canonical_candidates",
        "canonical_candidate_creation_keys",
        "canonical_candidate_transitions",
        "canonical_candidate_transition_keys",
    )
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == CURRENT_HEAD
        for table_name in candidate_tables:
            present = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = :name"
                ),
                {"name": table_name},
            ).scalar()
            assert present == 1, table_name
        unique_hash = conn.execute(
            text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conname = 'uq_canonical_candidates_uniqueness_hash'"
            )
        ).scalar()
        assert unique_hash == 1
        trigger = conn.execute(
            text(
                "SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'trg_canonical_candidate_transitions_append_only'"
            )
        ).scalar()
        assert trigger == 1

    command.downgrade(config, WATCHER_TELEGRAM_PERSISTENCE)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == WATCHER_TELEGRAM_PERSISTENCE
        for table_name in candidate_tables:
            missing = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = :name"
                ),
                {"name": table_name},
            ).scalar()
            assert missing is None, table_name
        watcher = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'watcher_worker_leases'"
            )
        ).scalar()
        assert watcher == 1

    command.upgrade(config, "head")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO canonical_candidates ("
                "candidate_id, organization_id, uniqueness_hash, schema_version, "
                "strategy_version_id, setup_definition_id, fusion_policy_version, "
                "direction, evidence_venue, evidence_market, evidence_instrument, "
                "timeframe, evidence_window_hash, assessment_id, executable_setup, "
                "evidence_identity, state, created_at, valid_until, transition_version, "
                "idempotency_key, correlation_id, content_hash"
                ") VALUES ("
                "gen_random_uuid(), gen_random_uuid(), :uhash, 'Candidate/v1', "
                "gen_random_uuid(), gen_random_uuid(), 'fusion/v1', "
                "'short', 'binance', 'perpetual', 'instrument-id-value', "
                "'15m', :uhash, gen_random_uuid(), '{}'::json, '{}'::json, 'active', "
                "now(), now() + interval '1 hour', 1, "
                "'idempotency-key', gen_random_uuid(), :uhash"
                ")"
            ),
            {"uhash": "a" * 64},
        )
    blocked = False
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM canonical_candidates"))
    except Exception:
        blocked = True
    assert blocked is True
    engine.dispose()


@requires_postgres
def test_canonical_trade_plan_alembic_upgrade_downgrade() -> None:
    engine = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = _alembic_config()
    command.upgrade(config, "head")
    plan_tables = (
        "action_eligibility_evaluations",
        "action_eligibility_identity_bindings",
        "canonical_trade_plan_roots",
        "canonical_trade_plan_lineage",
        "canonical_trade_plan_idempotency_keys",
    )
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == CURRENT_HEAD
        for table_name in plan_tables:
            present = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = :name"
                ),
                {"name": table_name},
            ).scalar()
            assert present == 1, table_name
        pvc_fk = conn.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = 'trade_plan_revisions' "
                "AND constraint_type = 'FOREIGN KEY' "
                "AND constraint_name LIKE '%paper_validation%'"
            )
        ).scalar()
        assert pvc_fk is None
        authority = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'trade_plan_revisions' AND column_name = 'plan_authority'"
            )
        ).scalar()
        assert authority == 1
        trigger = conn.execute(
            text(
                "SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'trg_canonical_trade_plan_lineage_append_only'"
            )
        ).scalar()
        assert trigger == 1

    command.downgrade(config, CANONICAL_CANDIDATE_PERSISTENCE)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == CANONICAL_CANDIDATE_PERSISTENCE
        for table_name in plan_tables:
            missing = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = :name"
                ),
                {"name": table_name},
            ).scalar()
            assert missing is None, table_name
        restored = conn.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = 'trade_plan_revisions' "
                "AND constraint_type = 'FOREIGN KEY' "
                "AND constraint_name LIKE '%paper_validation%'"
            )
        ).scalar()
        assert restored == 1
        candidate = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'canonical_candidates'"
            )
        ).scalar()
        assert candidate == 1

    command.upgrade(config, "head")
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == CURRENT_HEAD
        pvc_fk = conn.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = 'trade_plan_revisions' "
                "AND constraint_type = 'FOREIGN KEY' "
                "AND constraint_name LIKE '%paper_validation%'"
            )
        ).scalar()
        assert pvc_fk is None
    engine.dispose()


@requires_postgres
def test_setup_lifetime_alembic_upgrade_downgrade() -> None:
    engine = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = _alembic_config()
    command.upgrade(config, "head")
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == CURRENT_HEAD
        present = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'setup_lifetime_pins'"
            )
        ).scalar()
        assert present == 1
        unique = conn.execute(
            text("SELECT 1 FROM pg_constraint WHERE conname = 'uq_setup_lifetime_semantic_key'")
        ).scalar()
        assert unique == 1

    command.downgrade(config, STRATEGY_CONVERSATION_PERSISTENCE)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == STRATEGY_CONVERSATION_PERSISTENCE
        missing = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'setup_lifetime_pins'"
            )
        ).scalar()
        assert missing is None
        conversations = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'conversations'"
            )
        ).scalar()
        assert conversations == 1

    command.upgrade(config, "head")
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == CURRENT_HEAD
        present = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'setup_lifetime_pins'"
            )
        ).scalar()
        assert present == 1
    engine.dispose()


@requires_postgres
def test_telegram_paper_agent_alembic_upgrade_downgrade() -> None:
    engine = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = _alembic_config()
    command.upgrade(config, "head")
    tables = (
        "telegram_paper_notifications",
        "telegram_paper_threads",
        "telegram_paper_messages",
        "telegram_paper_confirmations",
    )
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == CURRENT_HEAD
        for table_name in tables:
            present = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    f"WHERE table_schema = 'public' AND table_name = '{table_name}'"
                )
            ).scalar()
            assert present == 1
        partial = conn.execute(
            text(
                "SELECT 1 FROM pg_indexes "
                "WHERE indexname = 'uq_tg_paper_thread_scope_null_resource'"
            )
        ).scalar()
        assert partial == 1

    command.downgrade(config, PAPER_EVALUATION_OBSERVATIONS)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == PAPER_EVALUATION_OBSERVATIONS
        missing = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'telegram_paper_notifications'"
            )
        ).scalar()
        assert missing is None
        observations = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'paper_evaluation_observations'"
            )
        ).scalar()
        assert observations == 1

    command.upgrade(config, "head")
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == CURRENT_HEAD
        present = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'telegram_paper_threads'"
            )
        ).scalar()
        assert present == 1
    engine.dispose()
