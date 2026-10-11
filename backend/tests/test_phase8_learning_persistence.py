"""Phase 8 learning persistence: PostgreSQL attribution, stats, RAG fact boundaries."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import JournalProjectionConflictError
from app.db.learning_attribution import (
    LearningAttributionEventRow,
    LearningAttributionHistoryImmutabilityError,
    LearningAttributionIdentityMutationError,
    LearningAttributionRecordRow,
)
from app.db.models import HistoricalCandle, JournalTrade
from app.learning_attribution.adapters import (
    learning_evidence_document,
    lesson_suggestions,
    render_learning_facts_text,
)
from app.learning_attribution.contracts import (
    NARRATIVE_NOT_FACT_BANNER,
    LearningVenueMode,
    RiskAdherence,
)
from app.learning_attribution.errors import (
    CrossTenantAttributionError,
    ExecutedOutcomeForbiddenError,
    LearningAttributionConflictError,
)
from app.learning_attribution.query import LearningQueryService
from app.persistence.attribution_postgres import PostgresAttributionStore
from app.schemas.common import JournalLifecycleEventType, TradeResult
from tests.support.learning_attribution import (
    ACCOUNT_ID,
    ORG_ID,
    OTHER_ACCOUNT,
    OTHER_ORG,
    OTHER_USER,
    USER_ID,
    command_for,
    instrument_payload,
    learning_service,
    lifecycle_event,
    make_lineage,
)
from tests.support.phase6_fusion import CANDIDATE_ID
from tests.support.postgres_persistence import (
    POSTGRES_URL,
    phase7_plan_session_factory,
    requires_postgres,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src/app/learning_attribution"
PERSISTENCE_MODULE = (
    Path(__file__).resolve().parents[1] / "src/app/persistence/attribution_postgres.py"
)
HEAD = "a13voicemonitor001"
WATCHER_TENANT_WATCHLIST = "b6f2d9a10e73"
PHASE8_REVISION = "d4f7a2c8e901"
PREVIOUS_HEAD = "c9e2b4a1d078"
FORBIDDEN_SNIPPETS = (
    "app.services.execution",
    "ExecutionService",
    "place_paper_order",
    "WATCHER_ORCHESTRATION_ENABLED",
    "telegram_interaction_enabled",
    "RagService(",
    "from alembic",
    "import alembic",
)


def _alembic_config() -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", POSTGRES_URL)
    config.set_main_option("script_location", str(backend_root / "src/app/db/migrations"))
    os.environ["ALEMBIC_DATABASE_URL"] = POSTGRES_URL
    return config


def test_package_is_not_an_execution_or_rag_authority() -> None:
    for path in (*PACKAGE_ROOT.rglob("*.py"), PERSISTENCE_MODULE):
        text_body = path.read_text(encoding="utf-8")
        for snippet in FORBIDDEN_SNIPPETS:
            assert snippet not in text_body, f"{path} contains forbidden {snippet}"


def test_postgres_store_roundtrip_and_idempotency(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        store = PostgresAttributionStore(session)
        service = learning_service(session, store)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        event = lifecycle_event(
            JournalLifecycleEventType.APPROVED_PLAN,
            source_event_id="plan-pg",
            execution_lifecycle_id=lifecycle_id,
            payload=instrument_payload(),
        )
        first = service.apply(command_for(event, lineage))
        second = service.apply(command_for(event, lineage, narrative="Different wording."))
        session.commit()
        loaded = store.get(organization_id=ORG_ID, candidate_id=CANDIDATE_ID)
        assert loaded is not None
        assert loaded.attribution_id == first.record.attribution_id
        assert loaded.facts.content_hash == first.facts_hash == second.facts_hash
        assert second.replayed is True
        assert len(loaded.events) == 1
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.candidate_id == CANDIDATE_ID
        assert trade.assessment_id == lineage.assessment.assessment_id
        assert trade.evidence_window_hash == lineage.assessment.evidence_window_hash


def test_postgres_store_duplicate_fill_close_converges(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        store = PostgresAttributionStore(session)
        service = learning_service(session, store)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.FILL,
                    source_event_id="fill-pg",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(entry_price="64000", size="1"),
                ),
                lineage,
            )
        )
        close = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.CLOSE,
                    source_event_id="close-pg",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(
                        exit_price="63000",
                        net_pnl="900",
                        result=TradeResult.WIN.value,
                        realized_vs_available_pct="80",
                    ),
                ),
                lineage,
            )
        )
        replay = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.CLOSE,
                    source_event_id="close-pg",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(
                        exit_price="63000",
                        net_pnl="900",
                        result=TradeResult.WIN.value,
                        realized_vs_available_pct="80",
                    ),
                ),
                lineage,
            )
        )
        session.commit()
        assert replay.replayed is True
        assert close.executed_trade_outcome is True
        assert session.scalar(select(func.count()).select_from(JournalTrade)) == 1
        assert session.scalar(select(func.count()).select_from(LearningAttributionRecordRow)) == 1
        assert session.scalar(select(func.count()).select_from(LearningAttributionEventRow)) == 2


def test_conflicting_source_identity_fails_on_postgres_store(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        store = PostgresAttributionStore(session)
        service = learning_service(session, store)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.APPROVED_PLAN,
                    source_event_id="plan-conflict-pg",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(planned_entry_price="64000"),
                ),
                lineage,
            )
        )
        session.commit()
        with pytest.raises(JournalProjectionConflictError):
            service.apply(
                command_for(
                    lifecycle_event(
                        JournalLifecycleEventType.APPROVED_PLAN,
                        source_event_id="plan-conflict-pg",
                        execution_lifecycle_id=lifecycle_id,
                        payload=instrument_payload(planned_entry_price="1"),
                    ),
                    lineage,
                )
            )


def test_tenant_isolation_on_postgres_store(
    attribution_sessions: sessionmaker[Session],
) -> None:
    with attribution_sessions() as session:
        store = PostgresAttributionStore(session)
        service = learning_service(session, store)
        service.apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.REJECT, source_event_id="rej-tenant"),
                make_lineage(),
            )
        )
        session.commit()
        assert store.get(organization_id=OTHER_ORG, candidate_id=CANDIDATE_ID) is None
        assert store.list_for_organization(OTHER_ORG) == ()
        with pytest.raises(CrossTenantAttributionError):
            service.apply(
                command_for(
                    lifecycle_event(
                        JournalLifecycleEventType.SKIP,
                        source_event_id="skip-tenant",
                        account_id=OTHER_ACCOUNT,
                    ),
                    make_lineage(),
                    organization_id=OTHER_ORG,
                    user_id=OTHER_USER,
                )
            )


def test_strategy_stats_and_human_vs_system_query(
    attribution_sessions: sessionmaker[Session],
) -> None:
    paper_lifecycle = uuid4()
    demo_lifecycle = uuid4()
    with attribution_sessions() as session:
        store = PostgresAttributionStore(session)
        service = learning_service(session, store)
        service.apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.REJECT, source_event_id="rej-stats-pg"),
                make_lineage(candidate_id=uuid4()),
            )
        )
        service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.FILL,
                    source_event_id="fill-stats-pg",
                    execution_lifecycle_id=paper_lifecycle,
                    payload=instrument_payload(
                        entry_price="64000",
                        net_pnl="-10",
                        result=TradeResult.LOSS.value,
                    ),
                ),
                make_lineage(include_plan=True, execution_lifecycle_id=paper_lifecycle),
            )
        )
        demo_candidate = uuid4()
        service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.FILL,
                    source_event_id="fill-demo-pg",
                    execution_lifecycle_id=demo_lifecycle,
                    payload=instrument_payload(
                        entry_price="64000",
                        net_pnl="20",
                        result=TradeResult.WIN.value,
                    ),
                ),
                make_lineage(
                    candidate_id=demo_candidate,
                    include_plan=True,
                    execution_lifecycle_id=demo_lifecycle,
                ),
                learning_venue_mode=LearningVenueMode.PAPER_EXCHANGE_DEMO,
            )
        )
        session.commit()
        query = LearningQueryService(store)
        paper = query.strategy_pattern_stats(
            organization_id=ORG_ID,
            learning_venue_mode=LearningVenueMode.PAPER_INTERNAL,
        )
        demo = query.strategy_pattern_stats(
            organization_id=ORG_ID,
            learning_venue_mode=LearningVenueMode.PAPER_EXCHANGE_DEMO,
        )
        mixed = query.strategy_pattern_stats(organization_id=ORG_ID)
        assert paper.human_vs_system.executed_outcomes == 1
        assert paper.human_vs_system.human_reject_or_skip == 1
        assert sum(item.loss_count for item in paper.patterns) == 1
        assert sum(item.win_count for item in paper.patterns) == 0
        assert demo.human_vs_system.executed_outcomes == 1
        assert sum(item.win_count for item in demo.patterns) == 1
        assert demo.human_vs_system.human_reject_or_skip == 0
        assert mixed.human_vs_system.executed_outcomes == 2
        assert {item.learning_venue_mode for item in mixed.patterns} == {
            LearningVenueMode.PAPER_INTERNAL,
            LearningVenueMode.PAPER_EXCHANGE_DEMO,
        }
        other = query.strategy_pattern_stats(organization_id=OTHER_ORG)
        assert other.human_vs_system.executed_outcomes == 0
        assert other.patterns == ()


def test_rag_fact_boundaries_exclude_narrative_and_market_truth(
    attribution_sessions: sessionmaker[Session],
) -> None:
    with attribution_sessions() as session:
        store = PostgresAttributionStore(session)
        result = learning_service(session, store).apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.SKIP, source_event_id="skip-rag-pg"),
                make_lineage(),
                narrative="Trader skipped because of news. api_key=sk-not-a-real-secret",
            )
        )
        session.commit()
        document = learning_evidence_document(result.record)
        facts_text = render_learning_facts_text(result.record)
        assert document.is_market_truth is False
        assert "not market truth" in document.facts_text.lower()
        assert NARRATIVE_NOT_FACT_BANNER not in facts_text
        assert "Trader skipped because of news." not in facts_text
        assert NARRATIVE_NOT_FACT_BANNER in document.combined_text
        assert "sk-not-a-real-secret" not in document.combined_text
        assert "[REDACTED]" in (document.narrative_text or "")
        assert result.facts_hash not in (document.narrative_text or "")
        query = LearningQueryService(store)
        loaded = query.rag_evidence(organization_id=ORG_ID, candidate_id=CANDIDATE_ID)
        assert loaded is not None
        assert loaded.facts_hash == result.facts_hash
        assert query.rag_evidence(organization_id=OTHER_ORG, candidate_id=CANDIDATE_ID) is None


def test_stop_violation_is_risk_not_setup(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        result = learning_service(session).apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.CLOSE,
                    source_event_id="close-stop",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(
                        entry_price="64000",
                        exit_price="66000",
                        exit_reason="stop",
                        net_pnl="-200",
                        result=TradeResult.LOSS.value,
                    ),
                ),
                make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id),
            )
        )
        session.commit()
        assert result.record.facts.risk_adherence.axis is RiskAdherence.STOP_VIOLATION
        assert result.record.facts.setup_quality.axis.value == "confirmed"
        suggestions = lesson_suggestions(result.record)
        assert any(item.mistake_type == "stop_violation" for item in suggestions)
        assert all(item.persist is False for item in suggestions)


def test_reject_cannot_later_become_executed(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    store = PostgresAttributionStore
    with attribution_sessions() as session:
        pg_store = store(session)
        service = learning_service(session, pg_store)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        service.apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.REJECT, source_event_id="rej-then-fill"),
                make_lineage(),
            )
        )
        session.commit()
        with pytest.raises(ExecutedOutcomeForbiddenError):
            service.apply(
                command_for(
                    lifecycle_event(
                        JournalLifecycleEventType.FILL,
                        source_event_id="fill-after-rej",
                        execution_lifecycle_id=lifecycle_id,
                        payload=instrument_payload(entry_price="64000"),
                    ),
                    lineage,
                )
            )


def test_venue_mode_conflict_fails_closed(
    attribution_sessions: sessionmaker[Session],
) -> None:
    with attribution_sessions() as session:
        store = PostgresAttributionStore(session)
        service = learning_service(session, store)
        lineage = make_lineage()
        service.apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.REJECT, source_event_id="rej-venue"),
                lineage,
            )
        )
        session.commit()
        with pytest.raises(LearningAttributionConflictError, match="venue"):
            service.apply(
                command_for(
                    lifecycle_event(JournalLifecycleEventType.SKIP, source_event_id="skip-venue"),
                    lineage,
                    learning_venue_mode=LearningVenueMode.PAPER_EXCHANGE_DEMO,
                )
            )


def test_record_identity_and_events_are_immutable(
    attribution_sessions: sessionmaker[Session],
) -> None:
    with attribution_sessions() as session:
        store = PostgresAttributionStore(session)
        learning_service(session, store).apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.REJECT, source_event_id="rej-immut"),
                make_lineage(),
            )
        )
        session.commit()
        row = session.scalars(select(LearningAttributionRecordRow)).one()
        row.evidence_window_hash = "b" * 64
        with pytest.raises(LearningAttributionIdentityMutationError):
            session.flush()
        session.rollback()
        event_row = session.scalars(select(LearningAttributionEventRow)).one()
        event_row.event_content_hash = "c" * 64
        with pytest.raises(LearningAttributionHistoryImmutabilityError):
            session.flush()


def test_learning_does_not_write_historical_candles(
    attribution_sessions: sessionmaker[Session],
) -> None:
    with attribution_sessions() as session:
        learning_service(session).apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.SKIP, source_event_id="skip-candles"),
                make_lineage(),
            )
        )
        session.commit()
        assert session.scalar(select(func.count()).select_from(HistoricalCandle)) == 0


def test_alembic_single_head_includes_phase8() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    assert script.get_heads() == [HEAD]
    ancestry = {revision.revision for revision in script.walk_revisions(base="base", head=HEAD)}
    assert WATCHER_TENANT_WATCHLIST in ancestry
    assert PHASE8_REVISION in ancestry
    assert script.get_revision(PHASE8_REVISION).down_revision == PREVIOUS_HEAD


@requires_postgres
def test_phase8_alembic_upgrade_downgrade_reupgrade() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    engine = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = _alembic_config()
    command.upgrade(config, "head")
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == HEAD
        for table_name in (
            "learning_attribution_records",
            "learning_attribution_events",
            "paper_evaluation_observations",
        ):
            present = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = :name"
                ),
                {"name": table_name},
            ).scalar()
            assert present == 1, table_name
        candidate_col = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'journal_trades' AND column_name = 'candidate_id'"
            )
        ).scalar()
        assert candidate_col == 1
        account_col = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'journal_trades' AND column_name = 'account_id'"
            )
        ).scalar()
        assert account_col == 1
        event_trigger = conn.execute(
            text(
                "SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'trg_learning_attribution_events_immutable'"
            )
        ).scalar()
        assert event_trigger == 1
        identity_trigger = conn.execute(
            text(
                "SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'trg_learning_attribution_records_identity_immutable'"
            )
        ).scalar()
        assert identity_trigger == 1

    command.downgrade(config, PREVIOUS_HEAD)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == PREVIOUS_HEAD
        missing = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_name = 'learning_attribution_records'"
            )
        ).scalar()
        assert missing is None
        candidate_col = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'journal_trades' AND column_name = 'candidate_id'"
            )
        ).scalar()
        assert candidate_col is None
        account_col = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'journal_trades' AND column_name = 'account_id'"
            )
        ).scalar()
        assert account_col is None

    command.upgrade(config, "head")
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == HEAD
    engine.dispose()


@requires_postgres
def test_postgres_restart_and_conflict_on_real_db() -> None:
    factory = phase7_plan_session_factory()
    from app.db.models import ExecutionAccount, Membership, Organization, User
    from app.schemas.common import MembershipRole
    from app.schemas.trade_plan import AccountMode, ExecutionMode

    with factory() as session:
        session.add_all(
            [
                Organization(id=ORG_ID, name="Attribution Org PG"),
                Organization(id=OTHER_ORG, name="Other Org PG"),
                User(id=USER_ID, email="attr-pg@test.example", hashed_password="not-a-real-hash"),
                User(
                    id=OTHER_USER, email="other-pg@test.example", hashed_password="not-a-real-hash"
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                Membership(organization_id=ORG_ID, user_id=USER_ID, role=MembershipRole.TRADER),
                Membership(
                    organization_id=OTHER_ORG, user_id=OTHER_USER, role=MembershipRole.TRADER
                ),
                ExecutionAccount(
                    id=ACCOUNT_ID,
                    organization_id=ORG_ID,
                    user_id=USER_ID,
                    name="Paper A",
                    execution_mode=ExecutionMode.PAPER,
                    account_mode=AccountMode.NET,
                ),
                ExecutionAccount(
                    id=OTHER_ACCOUNT,
                    organization_id=OTHER_ORG,
                    user_id=OTHER_USER,
                    name="Paper B",
                    execution_mode=ExecutionMode.PAPER,
                    account_mode=AccountMode.NET,
                ),
            ]
        )
        session.commit()

    lifecycle_id = uuid4()
    with factory() as session:
        store = PostgresAttributionStore(session)
        service = learning_service(session, store)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        first = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.FILL,
                    source_event_id="fill-restart",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(entry_price="64000"),
                ),
                lineage,
            )
        )
        session.commit()
        attribution_id = first.record.attribution_id

    with factory() as session:
        store = PostgresAttributionStore(session)
        loaded = store.get(organization_id=ORG_ID, candidate_id=CANDIDATE_ID)
        assert loaded is not None
        assert loaded.attribution_id == attribution_id
        query = LearningQueryService(store)
        stats = query.strategy_pattern_stats(organization_id=ORG_ID)
        assert stats.human_vs_system.executed_outcomes == 1
        assert query.strategy_pattern_stats(organization_id=OTHER_ORG).patterns == ()
