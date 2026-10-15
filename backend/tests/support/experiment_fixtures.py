"""Disposable PostgreSQL and trusted source fixtures; no exchange connections."""

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    ExecutionAccount,
    Membership,
    Organization,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.experiments.attribution import ExperimentSourceProof
from app.experiments.service import ExperimentService
from app.schemas.common import MembershipRole, StrategyId
from app.schemas.experiments import (
    ExperimentApproval,
    ExperimentConfiguration,
    ExperimentCreate,
    ExperimentSampleCreate,
    ExperimentTransition,
)
from app.schemas.nested_continuation import NestedContinuationSpec
from app.security.tenant import TenantContext
from tests.support.phase5_market import EVALUATED_AT


@pytest.fixture(scope="module", name="experiment_engine")
def experiment_engine():
    url = os.getenv("EXPERIMENT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires an explicitly supplied disposable EXPERIMENT_TEST_POSTGRES_URL")
    schema = "experiment_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = make_url(url).update_query_dict({"options": "-csearch_path=" + schema})
    engine = create_engine(isolated)
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture(name="experiment_world")
def experiment_world(experiment_engine):
    with experiment_engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as session:
            yield World(session)
        transaction.rollback()


class SourceFixture:
    def __init__(self, clock):
        self.clock = clock
        self.overrides = {}

    def resolve(self, version, variant_key, source_record_id):
        variant = next(v for v in version.configuration.variants if v.key == variant_key)
        return ExperimentSourceProof.model_validate(
            {
                "organization_id": version.organization_id,
                "execution_account_id": version.configuration.account.execution_account_id,
                "native_uid": version.configuration.account.native_uid,
                "source": version.configuration.account.source,
                "version_id": version.id,
                "configuration_hash": version.configuration_hash,
                "sample_group_id": version.sample_group_id,
                "strategy_version_id": variant.strategy_version_id,
                "variant_key": variant_key,
                "kind": version.configuration.sample_target.kind,
                "source_record_id": source_record_id,
                "source_content_hash": "a" * 64,
                "authority_origin": "experiment",
                "opened_at": self.clock() - timedelta(seconds=1),
                "completed_at": self.clock(),
                **self.overrides,
            }
        )


class World:
    def __init__(self, session):
        self.session = session
        self.now = EVALUATED_AT
        suffix = uuid4().hex
        self.tenant = TenantContext(
            uuid4(), uuid4(), suffix + "@fixture.test", MembershipRole.OWNER
        )
        session.add(Organization(id=self.tenant.organization_id, name="experiment-" + suffix))
        session.add(
            User(id=self.tenant.user_id, email=self.tenant.email, hashed_password="fixture")
        )
        session.flush()
        self.membership = Membership(
            organization_id=self.tenant.organization_id,
            user_id=self.tenant.user_id,
            role=MembershipRole.OWNER,
        )
        self.account = ExecutionAccount(
            id=uuid4(),
            organization_id=self.tenant.organization_id,
            user_id=self.tenant.user_id,
            name="Fixture PAPER/NET",
        )
        self.strategy = UserStrategy(
            id=uuid4(),
            organization_id=self.tenant.organization_id,
            user_id=self.tenant.user_id,
            name="Fixture Nested",
            setup_type=StrategyId.NESTED_CONTINUATION,
        )
        session.add_all([self.membership, self.account, self.strategy])
        session.flush()
        self.spec = NestedContinuationSpec(symbol="BTCUSDT")
        self.strategy_version = self.add_strategy_version(self.spec, 1)
        self.settings = Settings(_env_file=None, environment="local", provider_mode="mock")
        self.resolver = SourceFixture(lambda: self.now)
        self.service = ExperimentService(
            session, self.settings, clock=lambda: self.now, source_resolver=self.resolver
        )

    def add_strategy_version(self, spec, number):
        row = UserStrategyVersion(
            id=uuid4(),
            strategy_id=self.strategy.id,
            version=number,
            card={"fixture": True},
            pattern_spec=spec.model_dump(mode="json"),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def configuration(self, **updates):
        values = {
            "mode": "exploration",
            "account": {
                "execution_account_id": self.account.id,
                "source": "internal_simulation",
            },
            "family": self.spec.kind,
            "strategy_id": self.strategy.id,
            "strategy_version_id": self.strategy_version.id,
            "variants": [
                {
                    "key": "baseline",
                    "strategy_version_id": self.strategy_version.id,
                    "parameters": self.spec.parameters.model_dump(mode="json"),
                }
            ],
            "model_policy": {"mode": "disabled"},
            "symbols": [self.spec.symbol],
            "timeframes": [self.spec.trigger_timeframe],
            "risk_limits": {
                "max_risk_per_trade": "10",
                "max_position_notional": "500",
                "max_total_exposure": "1000",
                "max_daily_loss": "50",
                "max_weekly_loss": "100",
                "max_drawdown": "100",
                "max_leverage": "2",
                "max_open_positions": 5,
                "max_trades_per_day": 10,
                "max_trades_total": 20,
                "cost_allowance": "1",
            },
            "sample_target": {"kind": "closed_trade", "minimum": 1, "maximum": 5},
            **updates,
        }
        return ExperimentConfiguration.model_validate(values)

    def create(self, config=None, key=None):
        return self.service.create(
            self.tenant,
            ExperimentCreate(
                name="Fixture experiment",
                idempotency_key=key or uuid4().hex,
                configuration=config or self.configuration(),
            ),
        )

    def transition(self, version, action):
        self.now += timedelta(seconds=1)
        return self.service.transition(
            self.tenant,
            version.experiment_id,
            version.id,
            ExperimentTransition(action=action, expected_revision=version.revision),
        )

    def approve(self, version, **updates):
        self.now += timedelta(seconds=1)
        return self.service.approve(
            self.tenant,
            version.experiment_id,
            version.id,
            ExperimentApproval.model_validate(
                {
                    "expected_revision": version.revision,
                    "configuration_hash": version.configuration_hash,
                    "authorized_until": self.now + timedelta(days=1),
                    "confirm": "APPROVE_BOUNDED_EXPERIMENT",
                    **updates,
                }
            ),
        )

    def running(self, config=None):
        return self.transition(
            self.approve(self.transition(self.create(config), "submit")), "start"
        )

    def sample(self, version, reference=None, variant="baseline"):
        self.now += timedelta(seconds=2)
        return self.service.record_sample(
            self.tenant,
            version.experiment_id,
            version.id,
            ExperimentSampleCreate(variant_key=variant, source_record_id=reference or uuid4().hex),
        )
