"""Staging -> production settings mirror: export, dry run, apply, idempotency."""

from __future__ import annotations

import dataclasses
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.autonomous_trading_settings import AutonomousTradingSettingsRow
from app.db.models import Organization, User, UserStrategy, UserStrategyVersion
from app.db.risk_policy import RiskPolicyProposal, RiskPolicyVersion
from app.db.strategy_immutability import strategy_version_content_hash
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
        bundle = export_bundle(session, org_name=ORG_NAME, user_email=EMAIL)
    bundle["risk_policy_profiles"] = [_risk_payload()]
    assert bundle["format"] == BUNDLE_FORMAT
    assert not any(key.endswith("id") for key in bundle["strategies"][0])
    with production() as session:
        _tenant(session)
        session.commit()

    with production() as session:
        report = import_bundle(session, bundle, org_name=ORG_NAME, user_email=EMAIL)
        session.rollback()
        assert report.dry_run and report.changes == 4, report.actions
        assert session.scalar(select(func.count()).select_from(UserStrategy)) == 0

    with production() as session:
        report = import_bundle(session, bundle, org_name=ORG_NAME, user_email=EMAIL, apply=True)
        session.commit()
        assert report.changes == 4, report.actions
    with production() as session:
        assert session.scalar(select(func.count()).select_from(UserStrategy)) == 1
        settings = session.scalar(select(AutonomousTradingSettingsRow))
        assert settings.max_trades_per_day == 6 and settings.strategy_id is not None
        assert session.scalar(select(func.count()).select_from(RiskPolicyProposal)) == 1
        # Never applied directly: a human applies the proposal in the app.
        assert session.scalar(select(func.count()).select_from(RiskPolicyVersion)) == 0
        org = session.scalar(select(Organization.id).where(Organization.name == ORG_NAME))
        slots = WatcherWatchlistRepository(session).load(org).slots
        assert [s.symbol for s in slots][:4] == ["BTCUSDT", "ETHUSDT", "HYPEUSDT", "TAOUSDT"]

    with production() as session:
        again = import_bundle(session, bundle, org_name=ORG_NAME, user_email=EMAIL, apply=True)
        session.commit()
        assert again.changes == 0, again.actions


@requires_postgres
def test_changed_strategy_forks_a_new_version(two_databases) -> None:
    staging, production = two_databases
    with staging() as session:
        _seed_staging(session)
        bundle = export_bundle(session, org_name=ORG_NAME, user_email=EMAIL)
    with production() as session:
        _tenant(session)
        import_bundle(session, bundle, org_name=ORG_NAME, user_email=EMAIL, apply=True)
        session.commit()
    bundle["strategies"][0]["card"] = {"strategy_name": "Nested 1H", "entry": "sweep"}
    with production() as session:
        report = import_bundle(session, bundle, org_name=ORG_NAME, user_email=EMAIL, apply=True)
        session.commit()
        assert [a["action"] for a in report.actions if a["area"] == "strategy"] == ["new_version"]
        assert session.scalar(select(UserStrategy)).current_version == 2


@requires_postgres
def test_missing_target_tenant_is_refused(two_databases) -> None:
    _staging, production = two_databases
    with production() as session, pytest.raises(MirrorError):
        import_bundle(
            session, {"format": BUNDLE_FORMAT}, org_name=ORG_NAME, user_email=EMAIL, apply=True
        )


@pytest.mark.parametrize("key", ["api_key", "telegram_bot_token", "Passphrase", "jwt_secret"])
def test_secret_like_keys_are_refused(key: str) -> None:
    with pytest.raises(MirrorError):
        assert_no_secrets({"strategies": [{"card": {key: "x"}}]})
