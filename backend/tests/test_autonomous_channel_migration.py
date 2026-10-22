"""Alembic-migrated approval channel accepts AUTONOMOUS and rejects anything else."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.models import ApprovalAuthorization
from tests.support.alembic_heads import assert_down_revision_chain, single_alembic_head
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres

_REVISION = "a14autonomouschannel001"
_A15 = "a15reissueauth001"
_A16 = "a16riskpolicy001"
_A17 = "a17riskpolicy002"
_A18 = "a18riskpolicy003"
_A19 = "a19riskpolicy004"
_A20 = "a20autonomoussettings001"
_A21 = "a21killswitchkey001"
_PARENT = "a13voicemonitor001"
_CHANNEL_PREDICATE = "channel IN ('WEB', 'API', 'TELEGRAM', 'AUTONOMOUS')"


def test_metadata_channel_check_matches_the_alembic_revision() -> None:
    channel = ApprovalAuthorization.__table__.c.channel
    operation = ApprovalAuthorization.__table__.c.operation
    assert channel.type.create_constraint is False  # type: ignore[attr-defined]
    assert operation.type.create_constraint is False  # type: ignore[attr-defined]
    checks = [
        item
        for item in ApprovalAuthorization.__table__.constraints
        if isinstance(item, CheckConstraint) and "channel IN" in str(item.sqltext)
    ]
    assert [str(item.sqltext) for item in checks] == [_CHANNEL_PREDICATE]
    source = (
        Path(__file__).resolve().parents[1]
        / "src/app/db/migrations/versions"
        / "a14autonomouschannel001_autonomous_authorization_channel.py"
    ).read_text(encoding="utf-8")
    assert _CHANNEL_PREDICATE in source


def test_autonomous_channel_revision_follows_the_previous_head() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "src/app/db/migrations"))
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision(_REVISION)
    assert revision is not None
    assert revision.down_revision == _PARENT
    a15 = script.get_revision(_A15)
    assert a15 is not None
    assert a15.down_revision == _REVISION
    a16 = script.get_revision(_A16)
    assert a16 is not None
    assert a16.down_revision == _A15
    a17 = script.get_revision(_A17)
    assert a17 is not None
    assert a17.down_revision == _A16
    a18 = script.get_revision(_A18)
    assert a18 is not None
    assert a18.down_revision == _A17
    a19 = script.get_revision(_A19)
    assert a19 is not None
    assert a19.down_revision == _A18
    a20 = script.get_revision(_A20)
    assert a20 is not None
    assert a20.down_revision == _A19
    assert_down_revision_chain(script, (_A21, _A20))
    single_alembic_head(script)


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
        with engine.connect() as connection:
            indexdef = connection.scalar(
                text(
                    """
                    SELECT indexdef FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND indexname = 'uq_reusable_approval_authorization'
                    """
                )
            )
            leftover_unique = connection.scalar(
                text(
                    """
                    SELECT c.conname
                    FROM pg_constraint c
                    JOIN pg_class t ON t.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = current_schema()
                      AND t.relname = 'approval_authorizations'
                      AND c.contype = 'u'
                      AND pg_get_constraintdef(c.oid) ILIKE '%approval_request_id%'
                    """
                )
            )
        assert isinstance(indexdef, str)
        assert "AVAILABLE" in indexdef
        assert "CONSUMED" in indexdef
        assert leftover_unique is None
        _insert_channel(engine, "AUTONOMOUS")
        with pytest.raises(IntegrityError):
            _insert_sibling_authorization(engine, state="AVAILABLE")
        with engine.begin() as connection:
            connection.execute(text("UPDATE approval_authorizations SET state = 'REVOKED'"))
        _insert_sibling_authorization(engine, state="AVAILABLE")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM approval_authorizations WHERE state = 'REVOKED'"))
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


@requires_postgres
def test_create_all_stores_autonomous_and_rejects_an_invalid_channel() -> None:
    admin = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    schema = "autonomous_channel_meta_" + uuid4().hex
    isolated = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(isolated, poolclass=NullPool, future=True)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        Base.metadata.create_all(engine)
        with engine.connect() as connection:
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
                      AND pg_get_constraintdef(c.oid) ILIKE '%AUTONOMOUS%'
                    """
                )
            )
        assert isinstance(definition, str)
        assert "AUTONOMOUS" in definition
        assert "WEB" in definition
        _insert_channel(engine, "AUTONOMOUS")
        with pytest.raises(IntegrityError):
            _insert_channel(engine, "NOT_A_CHANNEL")
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


def _insert_sibling_authorization(engine: object, *, state: str) -> None:
    """Insert another authorization for the approval already stored in this schema."""

    from sqlalchemy.engine import Engine

    assert isinstance(engine, Engine)
    with engine.begin() as connection:
        existing = connection.execute(
            text(
                """
                SELECT approval_request_id, organization_id, user_id, account_id, plan_id,
                       revision_id, plan_content_hash
                FROM approval_authorizations
                LIMIT 1
                """
            )
        ).one()
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
                "'v1', now() + interval '1 day', :state, 'AUTONOMOUS', 'SYSTEM', :user, "
                ":correlation, :auth_hash)"
            ),
            {
                "id": uuid4(),
                "approval": existing.approval_request_id,
                "org": existing.organization_id,
                "user": existing.user_id,
                "account": existing.account_id,
                "plan": existing.plan_id,
                "revision": existing.revision_id,
                "digest": existing.plan_content_hash,
                "attestation": uuid4(),
                "state": state,
                "correlation": uuid4(),
                "auth_hash": uuid4().hex + uuid4().hex,
            },
        )


def _migration_module(name: str):
    path = Path(__file__).resolve().parents[1] / "src/app/db/migrations/versions" / name
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_autonomous_downgrade_refusal_sees_rows_on_sqlite() -> None:
    module = _migration_module("a14autonomouschannel001_autonomous_authorization_channel.py")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE approval_authorizations (channel VARCHAR(40))"))
        connection.execute(
            text("INSERT INTO approval_authorizations (channel) VALUES ('AUTONOMOUS')")
        )
        assert module._autonomous_rows_exist(connection) is True
        connection.execute(text("DELETE FROM approval_authorizations"))
        assert module._autonomous_rows_exist(connection) is False


@pytest.mark.parametrize("unique_name", [None, "approval_request_id_key"])
def test_sqlite_a14_a15_upgrade_and_downgrade(unique_name: str | None) -> None:
    """a14 and a15 run on SQLite without a hardcoded unique-constraint name."""

    a14 = _migration_module("a14autonomouschannel001_autonomous_authorization_channel.py")
    a15 = _migration_module("a15reissueauth001_reissue_authorization_after_revoke.py")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = MetaData()
    Table("approvals", metadata, Column("id", String(36), primary_key=True))
    Table(
        "approval_authorizations",
        metadata,
        Column("id", String(36), primary_key=True),
        Column(
            "approval_request_id",
            String(36),
            ForeignKey("approvals.id"),
            nullable=False,
        ),
        Column("channel", String(40), nullable=False),
        Column("state", String(20), nullable=False),
        CheckConstraint(
            "channel IN ('WEB', 'API', 'TELEGRAM')",
            name="ck_legacy_channel",
        ),
        UniqueConstraint("approval_request_id", name=unique_name),
    )
    metadata.create_all(engine)

    def migrate(revision: object, method: str) -> None:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                getattr(revision, method)()

    migrate(a14, "upgrade")
    checks = inspect(engine).get_check_constraints("approval_authorizations")
    assert any("AUTONOMOUS" in str(check["sqltext"]) for check in checks)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO approvals (id) VALUES ('approval-1')"))
        connection.execute(
            text(
                "INSERT INTO approval_authorizations "
                "(id, approval_request_id, channel, state) "
                "VALUES ('auth-1', 'approval-1', 'API', 'AVAILABLE')"
            )
        )
    migrate(a15, "upgrade")
    uniques = inspect(engine).get_unique_constraints("approval_authorizations")
    assert all(item["column_names"] != ["approval_request_id"] for item in uniques)
    indexes = inspect(engine).get_indexes("approval_authorizations")
    reusable = next(
        item for item in indexes if item["name"] == "uq_reusable_approval_authorization"
    )
    assert reusable["unique"] == 1
    assert "AVAILABLE" in str(reusable["dialect_options"]["sqlite_where"])
    assert "CONSUMED" in str(reusable["dialect_options"]["sqlite_where"])
    foreign_keys = inspect(engine).get_foreign_keys("approval_authorizations")
    assert any(item["referred_table"] == "approvals" for item in foreign_keys)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO approval_authorizations "
                "(id, approval_request_id, channel, state) "
                "VALUES ('auth-2', 'approval-1', 'AUTONOMOUS', 'REVOKED')"
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO approval_authorizations "
                "(id, approval_request_id, channel, state) "
                "VALUES ('auth-3', 'approval-1', 'AUTONOMOUS', 'AVAILABLE')"
            )
        )
    with pytest.raises(RuntimeError, match="AUTONOMOUS approval authorizations exist"):
        migrate(a14, "downgrade")
    with pytest.raises(RuntimeError, match="Multiple approval authorizations"):
        migrate(a15, "downgrade")
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM approval_authorizations WHERE id = 'auth-2'"))
        connection.execute(
            text("UPDATE approval_authorizations SET channel = 'API' WHERE id = 'auth-1'")
        )
    migrate(a15, "downgrade")
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO approval_authorizations "
                "(id, approval_request_id, channel, state) "
                "VALUES ('auth-4', 'approval-1', 'API', 'REVOKED')"
            )
        )
    migrate(a14, "downgrade")
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO approval_authorizations "
                "(id, approval_request_id, channel, state) "
                "VALUES ('auth-5', 'approval-1', 'AUTONOMOUS', 'AVAILABLE')"
            )
        )
    legacy = inspect(engine).get_check_constraints("approval_authorizations")
    assert legacy
    assert all("AUTONOMOUS" not in str(check["sqltext"]) for check in legacy)


def test_reissue_downgrade_refusal_sees_duplicate_rows_on_sqlite() -> None:
    module = _migration_module("a15reissueauth001_reissue_authorization_after_revoke.py")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    approval_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE approval_authorizations "
                "(id VARCHAR(36) PRIMARY KEY, approval_request_id VARCHAR(36))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO approval_authorizations (id, approval_request_id) "
                "VALUES (:id, :approval)"
            ),
            {"id": str(uuid4()), "approval": str(approval_id)},
        )
        assert module._duplicate_approval_authorizations(connection) is False
        connection.execute(
            text(
                "INSERT INTO approval_authorizations (id, approval_request_id) "
                "VALUES (:id, :approval)"
            ),
            {"id": str(uuid4()), "approval": str(approval_id)},
        )
        assert module._duplicate_approval_authorizations(connection) is True
