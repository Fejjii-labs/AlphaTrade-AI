"""Alembic-migrated approval channel accepts AUTONOMOUS and rejects anything else."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from tests.support.alembic_heads import single_alembic_head
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres

_REVISION = "a14autonomouschannel001"
_PARENT = "a13voicemonitor001"


def test_autonomous_channel_revision_follows_the_previous_head() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "src/app/db/migrations"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision(_REVISION)
    assert revision is not None
    assert revision.down_revision == _PARENT
    assert single_alembic_head(script) == _REVISION


@requires_postgres
def test_alembic_upgrade_stores_autonomous_and_rejects_an_invalid_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    schema = "autonomous_channel_" + uuid4().hex
    isolated = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={schema}"})
    dsn = isolated.render_as_string(hide_password=False)
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", dsn)
    config = _config()
    engine = create_engine(isolated, poolclass=NullPool, future=True)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                single_alembic_head()
            )
            definition = connection.scalar(
                text(
                    """
                    SELECT pg_get_constraintdef(c.oid)
                    FROM pg_constraint c
                    JOIN pg_class t ON t.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = current_schema()
                      AND t.relname = 'approval_authorizations'
                      AND c.contype = 'c'
                      AND pg_get_constraintdef(c.oid) ILIKE '%channel%'
                    """
                )
            )
        assert isinstance(definition, str)
        assert "AUTONOMOUS" in definition
        _insert_channel(engine, "AUTONOMOUS")
        with pytest.raises(IntegrityError):
            _insert_channel(engine, "NOT_A_CHANNEL")
        with pytest.raises(RuntimeError, match="AUTONOMOUS approval authorizations exist"):
            command.downgrade(config, _PARENT)
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM approval_authorizations WHERE channel = 'AUTONOMOUS'")
            )
        command.downgrade(config, _PARENT)
        with pytest.raises(IntegrityError):
            _insert_channel(engine, "AUTONOMOUS")
        _insert_channel(engine, "API")
        command.upgrade(config, "head")
        _insert_channel(engine, "AUTONOMOUS")
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _config() -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config()
    config.set_main_option("script_location", str(backend_root / "src/app/db/migrations"))
    return config


def _insert_channel(engine: object, channel: str) -> None:
    org_id = uuid4()
    user_id = uuid4()
    account_id = uuid4()
    proposal_id = uuid4()
    revision_id = uuid4()
    approval_id = uuid4()
    authorization_id = uuid4()
    digest = uuid4().hex + uuid4().hex
    with engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text("INSERT INTO organizations (id, name) VALUES (:id, :name)"),
            {"id": org_id, "name": f"channel-{org_id.hex[:12]}"},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, role, risk_profile, "
                "timezone, is_active, email_verified) VALUES "
                "(:id, :email, 'not-a-secret', 'trader', 'moderate', 'UTC', true, false)"
            ),
            {"id": user_id, "email": f"channel-{user_id.hex[:12]}@example.com"},
        )
        connection.execute(
            text(
                "INSERT INTO execution_accounts "
                "(id, organization_id, user_id, name, execution_mode, account_mode, enabled) "
                "VALUES (:id, :org, :user, 'paper', 'PAPER', 'NET', true)"
            ),
            {"id": account_id, "org": org_id, "user": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO trade_proposals ("
                "id, organization_id, user_id, symbol, timeframe, direction, entry_price, "
                "position_size, leverage, stop_loss, take_profits, runner_enabled, "
                "invalidation, confidence, risk_level, rationale, status, approval_required, "
                "loss_acceptance_required, loss_acceptance_status, plan_root_kind"
                ") VALUES ("
                ":id, :org, :user, 'BTCUSDT', '1h', 'long', 100, 1, 1, 90, '[]', false, "
                "'fixture', 0.5, 'low', 'fixture', 'draft', false, false, 'not_required', "
                "'manual_demo_test')"
            ),
            {"id": proposal_id, "org": org_id, "user": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO trade_plan_revisions ("
                "id, plan_id, organization_id, user_id, account_id, schema_version, operation, "
                "plan_authority, expected_account_mode, permission_attestation_id, "
                "permission_attestation_version, execution_venue, execution_instrument, "
                "execution_policy_version, valid_from, valid_until, semantic_payload, "
                "correlation_id, content_hash, presentation_metadata"
                ") VALUES ("
                ":id, :plan, :org, :user, :account, 'ManualDemoTradePlanV1', 'SUBMIT_ENTRY', "
                "'manual_demo_test', 'NET', :attestation, 'v1', 'BLOFIN_DEMO', 'BTCUSDT', "
                "'manual-blofin-demo/v1', now(), now() + interval '1 day', '{}', :correlation, "
                ":digest, '{}')"
            ),
            {
                "id": revision_id,
                "plan": proposal_id,
                "org": org_id,
                "user": user_id,
                "account": account_id,
                "attestation": uuid4(),
                "correlation": uuid4(),
                "digest": digest,
            },
        )
        connection.execute(
            text(
                "INSERT INTO approvals ("
                "id, proposal_id, organization_id, user_id, status, risk_level, confidence"
                ") VALUES (:id, :proposal, :org, :user, 'pending', 'low', 0.5)"
            ),
            {"id": approval_id, "proposal": proposal_id, "org": org_id, "user": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO approval_authorizations ("
                "id, approval_request_id, organization_id, user_id, account_id, "
                "exchange_account_scope_key, operation, execution_mode, plan_id, revision_id, "
                "plan_content_hash, execution_venue, execution_instrument, verified_account_mode, "
                "permission_attestation_id, permission_attestation_version, expires_at, state, "
                "channel, actor_type, actor_id, correlation_id, authorization_content_hash"
                ") VALUES ("
                ":id, :approval, :org, :user, :account, 'NONE', 'SUBMIT_ENTRY', 'PAPER', "
                ":plan, :revision, :digest, 'BLOFIN_DEMO', 'BTCUSDT', 'NET', :attestation, "
                "'v1', now() + interval '1 day', 'AVAILABLE', :channel, 'SYSTEM', :user, "
                ":correlation, :auth_hash)"
            ),
            {
                "id": authorization_id,
                "approval": approval_id,
                "org": org_id,
                "user": user_id,
                "account": account_id,
                "plan": proposal_id,
                "revision": revision_id,
                "digest": digest,
                "attestation": uuid4(),
                "channel": channel,
                "correlation": uuid4(),
                "auth_hash": uuid4().hex + uuid4().hex,
            },
        )
