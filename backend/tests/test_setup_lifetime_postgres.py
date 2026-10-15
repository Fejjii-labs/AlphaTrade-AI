"""PostgreSQL restart proofs for durable setup lifetime.

Watcher, Telegram, and live trading stay disabled.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Organization
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.setup_lifetime import (
    SetupLifetimeStore,
    SetupTriggerPin,
    setup_lifetime_lineage_hash,
    utc_trigger_end,
)
from app.market_contracts.adapters.replay import ReplayPerpetualSource
from app.market_contracts.first_slice import CANONICAL_EVALUATED_AT, CANONICAL_TRIGGER_INTERVAL_END
from app.persistence.setup_lifetime import SqlAlchemySetupLifetimeStore
from app.schemas.common import Timeframe
from app.signal_fusion.enums import SetupAssessmentState
from app.signal_fusion.evaluator import evaluate_setup
from app.signal_fusion.first_slice_types import FIRST_SLICE_EXPIRY_BARS
from tests.support.phase6_evaluator import subsequent_bars
from tests.support.phase6_fusion import ORG_ID, fusion_policy
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres
from tests.support.postgres_template import refresh_database


def _engine() -> sessionmaker[Session]:
    engine = refresh_database(POSTGRES_URL, kind="full")
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def _seed_org(session: Session) -> None:
    if session.get(Organization, ORG_ID) is None:
        session.add(Organization(id=ORG_ID, name="Setup lifetime org"))
        session.commit()


def _policy():
    return fusion_policy()


def _lifetime_key(policy):
    from app.evidence_pipeline.setup_lifetime import lifetime_key_from_policy

    return lifetime_key_from_policy(
        organization_id=policy.organization_id,
        symbol="BTCUSDT",
        timeframe=Timeframe.M15,
        strategy_version_id=policy.strategy_version_id,
        compiled_setup_definition_id=policy.executable_setup.setup_definition_id,
        compiled_content_hash=policy.executable_setup.content_hash,
    )


@requires_postgres
def test_postgres_restart_reconstructs_the_same_setup_lifetime() -> None:
    factory = _engine()
    policy = _policy()
    with factory() as session:
        _seed_org(session)
        store = SqlAlchemySetupLifetimeStore(session)
        first = FirstSliceEvidenceAssembler(
            ReplayPerpetualSource(), replay=True, lifetime=store
        ).assemble(organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT)
        session.commit()
        trigger_end = first.trigger_bar.interval_end
        trigger_hash = first.trigger_bar.content_hash
        lineage = store.get(_lifetime_key(policy))
        assert lineage is not None
        assert lineage.expired is False
        assert lineage.trigger_end == utc_trigger_end(trigger_end)
        assert lineage.trigger_bar_hash == trigger_hash

    with factory() as restarted:
        store = SqlAlchemySetupLifetimeStore(restarted)
        reconstructed = store.get(_lifetime_key(policy))
        assert reconstructed is not None
        assert reconstructed.trigger_end == utc_trigger_end(trigger_end)
        assert reconstructed.trigger_bar_hash == trigger_hash
        assert reconstructed.expired is False
        later = FirstSliceEvidenceAssembler(
            ReplayPerpetualSource(), replay=True, lifetime=store
        ).assemble(organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT)
        assert later.trigger_bar.interval_end == trigger_end
        assert later.clocks.setup_trigger_bar_hash == trigger_hash
        assert later.clocks.setup_expired is False


@requires_postgres
def test_restart_between_detection_and_later_bars_still_expires() -> None:
    factory = _engine()
    policy = _policy()
    fixture = ReplayPerpetualSource()
    with factory() as session:
        _seed_org(session)
        store = SqlAlchemySetupLifetimeStore(session)
        first = FirstSliceEvidenceAssembler(fixture, replay=True, lifetime=store).assemble(
            organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT
        )
        session.commit()
        assert first.clocks.setup_expired is False
        assert first.trigger_bar.interval_end == CANONICAL_TRIGGER_INTERVAL_END

    extra = subsequent_bars(
        fixture._bars_15m[-1],
        count=FIRST_SLICE_EXPIRY_BARS,
        high=fixture._bars_15m[-1].high,
    )
    later_source = ReplayPerpetualSource(bars_15m=list(fixture._bars_15m) + extra)
    with factory() as restarted:
        store = SqlAlchemySetupLifetimeStore(restarted)
        later = FirstSliceEvidenceAssembler(later_source, replay=True, lifetime=store).assemble(
            organization_id=ORG_ID,
            policy=policy,
            evaluated_at=extra[-1].interval_end + timedelta(seconds=5),
        )
        restarted.commit()
        assert later.trigger_bar.interval_end == first.trigger_bar.interval_end
        assert later.clocks.setup_expired is True
        assert later.clocks.setup_lifetime_remaining_bars == 0
        expired = evaluate_setup(
            policy=policy,
            command=first.assessment_command,
            evidence=first.bundle.model_copy(
                update={"subsequent_final_15m": later.bundle.subsequent_final_15m}
            ),
            evaluated_at=first.evaluated_at,
        )
        assert expired.state is SetupAssessmentState.EXPIRED

    with factory() as after_expiry:
        store = SqlAlchemySetupLifetimeStore(after_expiry)
        pin = store.get(_lifetime_key(policy))
        assert pin is not None
        assert pin.expired is True
        assert store.active_trigger_end(_lifetime_key(policy)) is None
        # A retry of retired lineage cannot reactivate it. Current assembly must
        # acquire its own complete trade window before evaluating a new trigger.
        store.remember(
            _lifetime_key(policy),
            SetupTriggerPin(
                trigger_end=first.trigger_bar.interval_end,
                trigger_bar_hash=first.trigger_bar.content_hash,
            ),
        )
        assert store.active_trigger_end(_lifetime_key(policy)) is None
        assert store.get(_lifetime_key(policy)).expired is True
        assert pin.trigger_end == utc_trigger_end(first.trigger_bar.interval_end)


@requires_postgres
def test_duplicate_writes_converge_and_tenants_stay_isolated() -> None:
    factory = _engine()
    policy = _policy()
    other_org = uuid4()
    with factory() as session:
        session.add(Organization(id=ORG_ID, name="Lifetime A"))
        session.add(Organization(id=other_org, name="Lifetime B"))
        session.commit()
        store = SqlAlchemySetupLifetimeStore(session)
        first = FirstSliceEvidenceAssembler(
            ReplayPerpetualSource(), replay=True, lifetime=store
        ).assemble(organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT)
        FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True, lifetime=store).assemble(
            organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT
        )
        from app.signal_fusion.policy import build_fusion_policy

        other_bound = build_fusion_policy(
            policy_version=policy.policy_version,
            organization_id=other_org,
            strategy_version_id=policy.strategy_version_id,
            executable_setup=policy.executable_setup,
            required_roles=policy.required_roles,
            thresholds=policy.thresholds,
            freshness_policy_version=policy.freshness_policy_version,
            finality_policy_version=policy.finality_policy_version,
        )
        FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True, lifetime=store).assemble(
            organization_id=other_org, policy=other_bound, evaluated_at=CANONICAL_EVALUATED_AT
        )
        session.commit()
        left = store.get(_lifetime_key(policy))
        right_key = _lifetime_key(other_bound)
        right = store.get(right_key)
        assert left is not None
        assert right is not None
        assert left.trigger_bar_hash == first.trigger_bar.content_hash
        assert left.required_lineage_hash != right.required_lineage_hash
        assert left.required_lineage_hash == setup_lifetime_lineage_hash(
            _lifetime_key(policy),
            trigger_end=left.trigger_end,
            trigger_bar_hash=left.trigger_bar_hash,
        )


def test_lineage_hash_excludes_transport_metadata() -> None:
    policy = _policy()
    key = _lifetime_key(policy)
    left = setup_lifetime_lineage_hash(
        key,
        trigger_end=CANONICAL_TRIGGER_INTERVAL_END,
        trigger_bar_hash="ab" * 32,
    )
    right = setup_lifetime_lineage_hash(
        key,
        trigger_end=CANONICAL_TRIGGER_INTERVAL_END,
        trigger_bar_hash="ab" * 32,
    )
    assert left == right
    assert "connection" not in left
    memory = SetupLifetimeStore()
    from app.evidence_pipeline.setup_lifetime import SetupTriggerPin

    memory.remember(
        key,
        SetupTriggerPin(
            trigger_end=CANONICAL_TRIGGER_INTERVAL_END,
            trigger_bar_hash="ab" * 32,
        ),
    )
    memory.remember(
        key,
        SetupTriggerPin(
            trigger_end=CANONICAL_TRIGGER_INTERVAL_END,
            trigger_bar_hash="ab" * 32,
        ),
    )
    pin = memory.get(key)
    assert pin is not None
    assert pin.required_lineage_hash == left
    memory.expire(key)
    expired = memory.get(key)
    assert expired is not None
    assert expired.expired is True
    memory.remember(
        key,
        SetupTriggerPin(
            trigger_end=CANONICAL_TRIGGER_INTERVAL_END,
            trigger_bar_hash="ab" * 32,
        ),
    )
    still = memory.get(key)
    assert still is not None
    assert still.expired is True
