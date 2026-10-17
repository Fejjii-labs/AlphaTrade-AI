"""Disposable PostgreSQL spec integration; research only, never runtime activation."""

import pytest

from app.core.errors import AppError, ConflictError
from app.db.models import UserStrategy
from app.experiments.risk import preview_admission
from app.schemas.common import StrategyId
from app.schemas.experiments import ExperimentConfiguration
from app.strategy_brain.trendpulse_1r.experiment import bind_experiment_signal
from tests.support.experiment_fixtures import (
    experiment_engine as _experiment_engine,  # noqa: F401
)
from tests.support.experiment_fixtures import (
    experiment_world as _experiment_world,  # noqa: F401
)
from tests.test_experiment_risk import inputs
from tests.test_trendpulse_1r_adapter import scan, spec


def configure(w, **changes):
    # New research library record; neither a Nested/SFP rename nor a compiler registration.
    w.strategy = UserStrategy(
        organization_id=w.tenant.organization_id,
        user_id=w.tenant.user_id,
        name="Fixture TrendPulse research",
        setup_type=StrategyId.MANUAL_REVIEW,
    )
    w.session.add(w.strategy)
    w.session.flush()
    w.spec = spec()
    w.strategy_version = w.add_strategy_version(w.spec, 1)
    return w.configuration(
        timeframes=["15m", "5m"],
        sample_target={"kind": "setup_observation", "minimum": 1, "maximum": 5},
        **changes,
    )


def test_immutable_authored_adapter_is_recognized_without_execution_or_sample_authority(
    experiment_world,
):
    w = experiment_world
    config = configure(w)
    version = w.running(config)
    result = bind_experiment_signal(
        version,
        variant_key="baseline",
        spec=w.spec,
        strategy_content_hash=w.strategy_version.content_hash,
        signal=scan().signal,
    )
    assert result.experiment_version_id == version.id
    assert version.configuration.timeframes == config.timeframes
    assert not result.sample_eligible and not result.runtime_activated
    assert version.sample_counts == {"baseline": 0} and version.performance is None
    plan, state = inputs(w)
    assert (
        preview_admission(version, plan, state, now=w.now).reason
        == "trendpulse_source_or_1r_geometry_mismatch"
    )
    assert not version.runtime_activated


@pytest.mark.parametrize(
    "change",
    [
        {"timeframes": ["5m"]},
        {"timeframes": ["15m"]},
        {"timeframes": ["15m", "5m", "1h"]},
    ],
)
def test_research_configuration_requires_the_exact_authored_two_timeframe_universe(
    experiment_world, change
):
    w = experiment_world
    config = configure(w)
    config = ExperimentConfiguration.model_validate({**config.model_dump(), **change})
    with pytest.raises(ConflictError, match="Universe"):
        w.create(config)


def test_geometry_does_not_authorize_closed_trade_samples(experiment_world):
    w = experiment_world
    config = configure(w)
    config = config.model_copy(update={"sample_target": w.configuration().sample_target})
    with pytest.raises(ConflictError, match="no authorized automatic trade plan"):
        w.create(config)


def test_parameters_cannot_be_overridden_under_the_baseline_version(experiment_world):
    w = experiment_world
    config = configure(w)
    variant = config.variants[0]
    bad = variant.model_copy(
        update={"parameters": {**variant.parameters, "pullback_tolerance": "0.01"}}
    )
    with pytest.raises(ConflictError, match="invalid"):
        w.create(config.model_copy(update={"variants": (bad,)}))


def test_default_source_resolver_cannot_ingest_research_geometry(experiment_world):
    w = experiment_world
    version = w.running(configure(w))
    w.service.source_resolver = None
    with pytest.raises(AppError) as error:
        w.sample(version, str(scan().signal.signal_id))
    assert error.value.status_code == 503
    current = w.service.detail(w.tenant, version.experiment_id).versions[0]
    assert current.sample_counts == {"baseline": 0} and current.performance is None
