"""PostgreSQL enforcement of UserStrategyVersion semantic immutability."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.persistence_firewall import install_persistence_firewall
from app.db.base import Base
from app.db.models import Membership, Organization, User, UserStrategy, UserStrategyVersion
from app.db.strategy_immutability import (
    backfill_strategy_version_content_hashes,
    is_strategy_version_immutability_error,
    strategy_version_content_hash,
)
from app.schemas.common import MembershipRole, StrategyId
from app.schemas.strategy_library import StrategyCard, UserStrategyCreate
from app.schemas.strategy_pattern_spec import FIRST_SLICE_NAME
from app.services.strategy_library_service import StrategyLibraryService
from tests.support.postgres_persistence import reset_public_schema

POSTGRES_URL = os.environ.get(
    "PHASE1_POSTGRES_URL",
    os.environ.get(
        "AT028_POSTGRES_URL",
        "postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade_test",
    ),
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


def _factory() -> sessionmaker[Session]:
    engine = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    reset_public_schema(engine)
    Base.metadata.create_all(engine)
    install_persistence_firewall()
    return sessionmaker(bind=engine, expire_on_commit=False)


def _card() -> StrategyCard:
    return StrategyCard.model_validate(
        {
            "strategy_name": FIRST_SLICE_NAME,
            "market_type": "crypto_perp",
            "asset_universe": ["BTCUSDT"],
            "timeframes": ["15m", "4h"],
            "entry_conditions": ["Bearish liquidity sweep at 4h resistance"],
            "confirmation_conditions": ["CVD divergence"],
            "invalidation": ["Close back above sweep high"],
            "stop_loss": ["Above sweep high"],
            "take_profit_plan": ["TP1 at 1R"],
            "runner_plan": [],
            "position_sizing": ["1%"],
            "add_rules": [],
            "no_trade_rules": [],
            "backtest_rules": [],
            "success_criteria": [],
            "validation_status": "draft",
        }
    )


ORG = uuid.UUID("00000000-0000-0000-0000-00000000e001")
USER = uuid.UUID("00000000-0000-0000-0000-00000000e011")


def _seed(session: Session) -> UserStrategy:
    session.add_all(
        [
            Organization(id=ORG, name="PG Org"),
            User(id=USER, email="pg@test.example", hashed_password="not-a-real-hash"),
        ]
    )
    session.flush()
    session.add(Membership(organization_id=ORG, user_id=USER, role=MembershipRole.TRADER))
    session.flush()
    created = StrategyLibraryService(session).create(
        UserStrategyCreate(
            organization_id=ORG,
            user_id=USER,
            name="PG Sweep",
            setup_type=StrategyId.LIQUIDITY_SWEEP_REVERSAL,
            card=_card(),
        )
    )
    session.commit()
    return created


@requires_postgres
def test_postgres_direct_sql_rejects_semantic_update() -> None:
    factory = _factory()
    with factory() as session:
        created = _seed(session)
        version = session.scalars(select(UserStrategyVersion)).one()
        original = version.content_hash
        with pytest.raises(DBAPIError) as exc:
            session.execute(
                text(
                    "UPDATE user_strategy_versions SET card = '{}'::json WHERE strategy_id = :sid"
                ),
                {"sid": created.id},
            )
            session.commit()
        assert is_strategy_version_immutability_error(exc.value)
        session.rollback()
        session.expire_all()
        restored = session.scalars(select(UserStrategyVersion)).one()
        assert restored.content_hash == original
        assert restored.card["strategy_name"] == FIRST_SLICE_NAME


@requires_postgres
def test_postgres_direct_sql_rejects_delete() -> None:
    factory = _factory()
    with factory() as session:
        _seed(session)
        with pytest.raises(DBAPIError) as exc:
            session.execute(text("DELETE FROM user_strategy_versions"))
            session.commit()
        assert is_strategy_version_immutability_error(exc.value)
        session.rollback()
        assert session.scalar(select(UserStrategyVersion)) is not None


@requires_postgres
def test_postgres_evaluation_status_remains_mutable() -> None:
    factory = _factory()
    with factory() as session:
        _seed(session)
        session.execute(text("UPDATE user_strategy_versions SET validation_status = 'VALIDATED'"))
        session.commit()
        version = session.scalars(select(UserStrategyVersion)).one()
        assert version.validation_status.value == "validated"


@requires_postgres
def test_postgres_hash_backfill_validation_is_idempotent() -> None:
    factory = _factory()
    with factory() as session:
        _seed(session)
        version = session.scalars(select(UserStrategyVersion)).one()
        expected = strategy_version_content_hash(
            card=version.card,
            structured_rules=version.structured_rules,
            lesson_source_metadata=version.lesson_source_metadata,
            pattern_spec=version.pattern_spec,
        )
        assert version.content_hash == expected
        again = backfill_strategy_version_content_hashes(session.connection())
        session.commit()
        assert again == 0
