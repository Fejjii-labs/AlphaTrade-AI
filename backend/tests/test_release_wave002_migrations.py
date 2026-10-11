"""Both accepted migration heads converge without losing the SFP receipt history."""

from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres
from tests.test_phase2_4_alembic_postgres import _alembic_config


@requires_postgres
@pytest.mark.parametrize("source_head", ["a2tgpolicy002", "a2sfp002"])
def test_accepted_branch_upgrade_merge_downgrade_reupgrade(source_head: str) -> None:
    engine = create_engine(POSTGRES_URL, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        config = _alembic_config()
        command.upgrade(config, source_head)
        receipt_id = uuid4()
        if source_head == "a2sfp002":
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO public_market_observations "
                        "(identity_hash, observation_id, payload) "
                        "VALUES (:identity, :id, :payload)"
                    ),
                    {"identity": "a" * 64, "id": receipt_id, "payload": '{"receipt": "original"}'},
                )
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert (
                conn.scalar(text("SELECT version_num FROM alembic_version")) == "a13voicemonitor001"
            )
            assert "public_market_observations" in inspect(conn).get_table_names()
            assert "telegram_policy" in {
                column["name"]
                for column in inspect(conn).get_columns("user_notification_preferences")
            }
            if source_head == "a2sfp002":
                assert conn.scalar(
                    text(
                        "SELECT payload FROM public_market_observations WHERE observation_id = :id"
                    ),
                    {"id": receipt_id},
                ) == {"receipt": "original"}
        # Downgrading the additive metadata and merge preserves accepted schemas and receipts.
        command.downgrade(config, source_head)
        with engine.connect() as conn:
            assert set(conn.scalars(text("SELECT version_num FROM alembic_version"))) == {
                "a2tgpolicy002",
                "a2sfp002",
            }
            assert "public_market_observations" in inspect(conn).get_table_names()
            if source_head == "a2sfp002":
                assert conn.scalar(text("SELECT count(*) FROM public_market_observations")) == 1
        command.upgrade(config, "head")
        command.downgrade(config, "a1brain001")
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "a1brain001"
            assert "public_market_observations" not in inspect(conn).get_table_names()
            assert "telegram_policy" not in {
                column["name"]
                for column in inspect(conn).get_columns("user_notification_preferences")
            }
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert (
                conn.scalar(text("SELECT version_num FROM alembic_version")) == "a13voicemonitor001"
            )
    finally:
        engine.dispose()
