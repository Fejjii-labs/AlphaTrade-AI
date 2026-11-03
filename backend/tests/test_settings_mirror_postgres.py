"""Staging -> production settings mirror: export, dry run, apply, idempotency."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.autonomous_trading_settings import AutonomousTradingSettingsRow
from app.db.models import AuditLog, Organization, User, UserStrategy, UserStrategyVersion
from app.db.risk_policy import RiskPolicyProposal, RiskPolicyVersion
from app.db.strategy_immutability import strategy_version_content_hash
from app.db.watcher_watchlist import WatcherWatchlistRow
from app.operations.settings_mirror import (
    BUNDLE_FORMAT,
    MirrorError,
    assert_no_secrets,
    export_bundle,
    import_bundle,
)
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.common import StrategyChangeSource, StrategyId
from app.services.risk_policy.profile import PolicyKind, canonical_profile, placeholder_profile
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres
from tests.test_deployment_safety import _STAGING_BASE
from tests.test_production_demo_profile import _MIRROR

STAGING = Settings(**_STAGING_BASE)
_DEMO_ONLY = {
    "deployment_profile",
    "perpetual_evidence_source",
    "exchange_mode",
    "blofin_demo_enabled",
    "governed_blofin_demo_enabled",
    "watcher_orchestration_enabled",
    "watcher_paper_staging_activation",
    "telegram_interaction_enabled",
    "telegram_paper_activation_armed",
    "telegram_inbound_mode",
    "telegram_network_permitted",
    "autonomous_blofin_demo_enabled",
}
PRODUCTION = Settings(**_MIRROR)

ORG_NAME = "Mirror Org"
EMAIL = "owner@mirror.example"


@pytest.fixture
def two_databases():
    from tests.support.postgres_template import clone_database, drop_clone

    engines = [clone_database(POSTGRES_URL), clone_database(POSTGRES_URL)]
    try:
        yield [sessionmaker(bind=e, expire_on_commit=False) for e in engines]
    finally:
        for engine in engines:
            drop_clone(engine)


def _tenant(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    org, user = uuid.uuid4(), uuid.uuid4()
    session.add(Organization(id=org, name=ORG_NAME))
    session.add(User(id=user, email=EMAIL, hashed_password="not-a-real-hash"))
    session.flush()
    return org, user


def _seed_staging(session: Session) -> None:
    org, user = _tenant(session)
    strategy = UserStrategy(
        id=uuid.uuid4(),
        organization_id=org,
        user_id=user,
        name="Nested 1H",
        setup_type=next(iter(StrategyId)),
        current_version=1,
        enabled=True,
        paper_eligible=True,
    )
    session.add(strategy)
    session.flush()
    card = {"strategy_name": "Nested 1H", "entry": "retest"}
    session.add(
        UserStrategyVersion(
            strategy_id=strategy.id,
            version=1,
            card=card,
            change_source=StrategyChangeSource.CREATE,
            content_hash=strategy_version_content_hash(
                card=card, structured_rules=None, lesson_source_metadata=None
            ),
        )
    )
    session.add(
        AutonomousTradingSettingsRow(
            organization_id=org,
            user_id=user,
            daily_loss_limit_percent=Decimal("3"),
            max_trades_per_day=6,
            risk_per_trade_percent=Decimal("0.5"),
            strategy_id=strategy.id,
            symbol=None,
        )
    )
    session.flush()
    WatcherWatchlistRepository(session).replace(
        org,
        [("BTCUSDT", True), ("ETHUSDT", True), ("HYPEUSDT", True), ("TAOUSDT", False)],
        expected_revision=0,
    )
    session.commit()


def _risk_payload() -> dict[str, str]:
    profile = dataclasses.replace(
        placeholder_profile(PolicyKind.DEMO),
        max_leverage=Decimal("10"),
        default_leverage=Decimal("5"),
    )
    return canonical_profile(profile)


@requires_postgres
def test_export_import_dry_run_apply_and_rerun_is_noop(two_databases) -> None:
    staging, production = two_databases
    with staging() as session:
        _seed_staging(session)
        bundle = export_bundle(session, settings=STAGING, org_name=ORG_NAME, user_email=EMAIL)
    bundle["risk_policy_profiles"] = [_risk_payload()]
    assert bundle["format"] == BUNDLE_FORMAT
    assert not any(key.endswith("id") for key in bundle["strategies"][0])
    with production() as session:
        _tenant(session)
        session.commit()

    with production() as session:
        report = import_bundle(
            session, bundle, settings=PRODUCTION, org_name=ORG_NAME, user_email=EMAIL
        )
        session.rollback()
        assert report.dry_run and report.changes == 4, report.actions
    with production() as session:  # dry run wrote nothing anywhere
        for model in (
            UserStrategy,
            UserStrategyVersion,
            RiskPolicyProposal,
            AutonomousTradingSettingsRow,
            WatcherWatchlistRow,
            AuditLog,
        ):
            assert session.scalar(select(func.count()).select_from(model)) == 0, model

    with production() as session:
        report = import_bundle(
            session, bundle, settings=PRODUCTION, org_name=ORG_NAME, user_email=EMAIL, apply=True
        )
        session.commit()
        assert report.changes == 4, report.actions
    with production() as session:
        assert session.scalar(select(func.count()).select_from(UserStrategy)) == 1
        assert session.scalar(select(UserStrategy)).paper_eligible is False
        assert session.scalar(select(func.count()).select_from(AuditLog)) >= 1
        settings = session.scalar(select(AutonomousTradingSettingsRow))
        assert settings.max_trades_per_day == 6 and settings.strategy_id is not None
        assert session.scalar(select(func.count()).select_from(RiskPolicyProposal)) == 1
        # Never applied directly: a human applies the proposal in the app.
        assert session.scalar(select(func.count()).select_from(RiskPolicyVersion)) == 0
        org = session.scalar(select(Organization.id).where(Organization.name == ORG_NAME))
        slots = WatcherWatchlistRepository(session).load(org).slots
        assert [s.symbol for s in slots][:4] == ["BTCUSDT", "ETHUSDT", "HYPEUSDT", "TAOUSDT"]

    with production() as session:
        again = import_bundle(
            session, bundle, settings=PRODUCTION, org_name=ORG_NAME, user_email=EMAIL, apply=True
        )
        session.commit()
        assert again.changes == 0, again.actions


@requires_postgres
def test_changed_strategy_forks_a_new_version(two_databases) -> None:
    staging, production = two_databases
    with staging() as session:
        _seed_staging(session)
        bundle = export_bundle(session, settings=STAGING, org_name=ORG_NAME, user_email=EMAIL)
    with production() as session:
        _tenant(session)
        import_bundle(
            session, bundle, settings=PRODUCTION, org_name=ORG_NAME, user_email=EMAIL, apply=True
        )
        session.commit()
    bundle["strategies"][0]["card"] = {"strategy_name": "Nested 1H", "entry": "sweep"}
    with production() as session:
        report = import_bundle(
            session, bundle, settings=PRODUCTION, org_name=ORG_NAME, user_email=EMAIL, apply=True
        )
        session.commit()
        assert [a["action"] for a in report.actions if a["area"] == "strategy"] == ["new_version"]
        assert session.scalar(select(UserStrategy)).current_version == 2


@requires_postgres
def test_missing_target_tenant_is_refused(two_databases) -> None:
    _staging, production = two_databases
    with production() as session, pytest.raises(MirrorError):
        import_bundle(
            session,
            {"format": BUNDLE_FORMAT, "source_environment": "staging"},
            settings=PRODUCTION,
            org_name=ORG_NAME,
            user_email=EMAIL,
            apply=True,
        )


@requires_postgres
def test_expired_proposal_does_not_block_refiling(two_databases) -> None:
    from app.services.audit_service import AuditService
    from app.services.risk_policy.governance import PolicyActor, PolicyChannel
    from app.services.risk_policy.persistence import RiskPolicyService
    from app.services.risk_policy.profile import profile_from_canonical

    _staging, production = two_databases
    bundle = {
        "format": BUNDLE_FORMAT,
        "source_environment": "staging",
        "risk_policy_profiles": [_risk_payload()],
    }
    with production() as session:
        org, _user = _tenant(session)
        RiskPolicyService(session, AuditService(session)).propose(
            organization_id=org,
            actor=PolicyActor(actor_id="old", channel=PolicyChannel.BOT, authenticated=True),
            profile=profile_from_canonical(_risk_payload()),
            reason="old expired proposal",
            now=datetime.now(UTC) - timedelta(days=30),
        )
        session.commit()
    with production() as session:
        report = import_bundle(
            session, bundle, settings=PRODUCTION, org_name=ORG_NAME, user_email=EMAIL, apply=True
        )
        session.commit()
        assert [a["action"] for a in report.actions] == ["propose"]
    with production() as session:
        again = import_bundle(
            session, bundle, settings=PRODUCTION, org_name=ORG_NAME, user_email=EMAIL
        )
        assert again.changes == 0  # the fresh proposal is pending


def test_export_refuses_outside_staging() -> None:
    with pytest.raises(MirrorError, match="export refused"):
        export_bundle(None, settings=PRODUCTION, org_name=ORG_NAME, user_email=EMAIL)  # type: ignore[arg-type]
    with pytest.raises(MirrorError, match="export refused"):
        export_bundle(None, settings=Settings(), org_name=ORG_NAME, user_email=EMAIL)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("settings", "source"),
    [
        (STAGING, "staging"),
        (Settings(), "staging"),
        (
            Settings(
                **{k: v for k, v in _MIRROR.items() if k not in _DEMO_ONLY},
                deployment_profile="standard",
            ),
            "staging",
        ),
        (PRODUCTION, "production"),
        (PRODUCTION, None),
    ],
    ids=["staging-target", "local-target", "standard-production", "bundle-from-prod", "no-source"],
)
def test_import_direction_guards(settings: Settings, source: str | None) -> None:
    bundle = {"format": BUNDLE_FORMAT}
    if source:
        bundle["source_environment"] = source
    with pytest.raises(MirrorError, match="import refused"):
        import_bundle(None, bundle, settings=settings, org_name=ORG_NAME, user_email=EMAIL)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        "sk-" + "a" * 40,
        "postgresql://user:hunter2@db.example.com:5432/x",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3OCJ9.c2lnbmF0dXJlLXZhbHVl",
        "123456789:AAHtestTokenValueForStagingPackage_xyz",
    ],
    ids=["openai", "db-url", "jwt", "telegram"],
)
def test_secret_like_values_are_refused(value: str) -> None:
    with pytest.raises(MirrorError, match="value"):
        assert_no_secrets({"strategies": [{"card": {"notes": value}}]})


@pytest.mark.parametrize(
    "key", ["key", "credentials", "auth_header", "sentry_dsn", "webhook_url", "chat_id"]
)
def test_broadened_secret_key_names_are_refused(key: str) -> None:
    with pytest.raises(MirrorError):
        assert_no_secrets({"card": {key: "x"}})


@pytest.mark.parametrize("name", ["api_key", "telegram_bot_token", "Passphrase", "jwt_secret"])
def test_secret_like_keys_are_refused(name: str) -> None:
    with pytest.raises(MirrorError):
        assert_no_secrets({"strategies": [{"card": {name: "x"}}]})
