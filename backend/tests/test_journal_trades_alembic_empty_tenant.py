"""Regression: empty synthetic tenant journal list on Alembic PostgreSQL.

Staging canonical smoke step 10 hits GET /journal/trades after registering a
fresh tenant. SQLite create_all tests hide ORM columns that Alembic never
added. This module upgrades a real PostgreSQL schema with Alembic (the
deployed path) and asserts empty-state 200s — never HTTP 500.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.db.models import JournalTrade
from app.db.session import get_session
from app.main import create_app
from app.runtime.canonical import build_production_canonical_runtime
from app.security.rate_limit import reset_rate_limiter
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres

CURRENT_HEAD = "a13voicemonitor001"


def _alembic_config() -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", POSTGRES_URL)
    config.set_main_option("script_location", str(backend_root / "src/app/db/migrations"))
    os.environ["ALEMBIC_DATABASE_URL"] = POSTGRES_URL
    return config


def _upgrade_alembic_schema() -> None:
    engine = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(_alembic_config(), "head")
    engine.dispose()


@requires_postgres
def test_alembic_journal_trades_includes_orm_account_id() -> None:
    _upgrade_alembic_schema()
    engine = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert version == CURRENT_HEAD
            present = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND table_name = 'journal_trades' "
                    "AND column_name = 'account_id'"
                )
            ).scalar()
            assert present == 1
            db_columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'journal_trades'"
                    )
                )
            }
            orm_columns = {column.name for column in JournalTrade.__table__.columns}
            missing = sorted(orm_columns - db_columns)
            assert missing == [], missing
    finally:
        engine.dispose()


@requires_postgres
def test_empty_synthetic_tenant_journal_list_and_strategy_stats_are_200() -> None:
    """Canonical smoke step 10 against Alembic PostgreSQL with zero journal rows."""

    _upgrade_alembic_schema()
    engine = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    reset_rate_limiter()
    get_settings.cache_clear()
    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url=POSTGRES_URL,
        jwt_secret="journal-alembic-empty-tenant-secret-32",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
        alert_delivery_enabled=False,
        telegram_alerts_enabled=False,
        worker_enabled=False,
        market_watcher_enabled=False,
        demo_seed_enabled=False,
    )
    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    email = f"canonical-empty-{uuid4().hex[:12]}@example.com"
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = POSTGRES_URL
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            app.state.canonical_runtime = build_production_canonical_runtime(
                factory, settings=settings
            )
            registered = client.post(
                "/auth/register",
                json={
                    "email": email,
                    "password": "secure-password-1",
                    "organization_name": f"Canonical Empty {uuid4()}",
                },
            )
            assert registered.status_code == 201, registered.text
            token = registered.json()["tokens"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            stats = client.get("/canonical/learning/strategy-stats", headers=headers)
            assert stats.status_code == 200, stats.text
            body = stats.json()
            assert body["authority"] == "canonical"
            assert body["snapshot"]["patterns"] == []
            assert body["snapshot"]["human_vs_system"]["human_approvals"] == 0

            evaluation = client.get("/canonical/paper-evaluation/summary", headers=headers)
            assert evaluation.status_code == 200, evaluation.text
            summary = evaluation.json()
            assert summary["authority"] == "canonical"
            assert summary["live_executable"] is False
            assert summary["watcher_activated"] is False
            assert summary["summary"]["facts"]["conversion"]["closed"] == 0
            assert summary["summary"]["facts"]["missed_opportunities"]["counterfactual_pnl"] is None
            assert all(
                item["activate"] is False and item["auto_activate"] is False
                for item in summary["summary"]["refinements"]
            )

            trades = client.get("/journal/trades", headers=headers)
            assert trades.status_code == 200, trades.text
            listing = trades.json()
            assert listing["items"] == []
            assert listing["total"] == 0
            assert listing["limit"] == 50
            assert listing["offset"] == 0
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        engine.dispose()
