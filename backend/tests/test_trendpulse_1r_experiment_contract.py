"""Pure experiment-config/v1 attribution, without claiming sample or account proof."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.schemas.experiments import ExperimentConfiguration, ExperimentMode, ExperimentVersion
from app.strategy_brain.trendpulse_1r.contracts import exact_hash
from app.strategy_brain.trendpulse_1r.experiment import bind_experiment_signal
from tests.test_trendpulse_1r_adapter import END, scan, spec


@pytest.fixture(scope="module")
def signal():
    return scan().signal


def version(*, source="internal_simulation", **changes):
    strategy_version = uuid4()
    account = {"execution_account_id": uuid4(), "source": source}
    if source == "blofin_demo":
        account.update(native_uid="demo-A", execution_identity_audit_id=uuid4())
    config = ExperimentConfiguration.model_validate(
        {
            "mode": "exploration",
            "account": account,
            "family": "trendpulse_1r/v1",
            "strategy_id": uuid4(),
            "strategy_version_id": strategy_version,
            "variants": [
                {
                    "key": "baseline",
                    "strategy_version_id": strategy_version,
                    "parameters": spec().parameters.model_dump(mode="json"),
                }
            ],
            "model_policy": {"mode": "disabled"},
            "symbols": ["BTCUSDT"],
            "timeframes": ["15m", "5m"],
            "risk_limits": {
                "max_risk_per_trade": "10",
                "max_position_notional": "1000",
                "max_total_exposure": "2000",
                "max_daily_loss": "30",
                "max_weekly_loss": "100",
                "max_drawdown": "100",
                "max_leverage": "2",
                "max_open_positions": 2,
                "max_trades_per_day": 3,
                "max_trades_total": 20,
                "cost_allowance": "1",
            },
            "sample_target": {"kind": "setup_observation", "minimum": 5, "maximum": 10},
        }
    )
    result = ExperimentVersion.model_validate(
        {
            "id": uuid4(),
            "experiment_id": uuid4(),
            "organization_id": uuid4(),
            "user_id": uuid4(),
            "version": 1,
            "parent_version_id": None,
            "state": "draft",
            "revision": 0,
            "configuration": config,
            "configuration_hash": "0" * 64,
            "strategy_content_hashes": {str(strategy_version): "a" * 64},
            "sample_group_id": uuid4(),
            "sample_counts": {"baseline": 0},
            "created_at": END,
            "submitted_at": None,
            "approved_at": None,
            "approved_by": None,
            "authorized_until": None,
            "started_at": None,
            "paused_at": None,
            "completed_at": None,
            "promoted_at": None,
            "promotion_version_id": None,
            **changes,
        }
    )
    return seal(result)


def seal(record):
    digest = exact_hash(
        {
            "organization_id": record.organization_id,
            "user_id": record.user_id,
            "experiment_id": record.experiment_id,
            "version_id": record.id,
            "sample_group_id": record.sample_group_id,
            "configuration": record.configuration.model_dump(),
            "strategy_content_hashes": record.strategy_content_hashes,
        }
    )
    return record.model_copy(update={"configuration_hash": digest})


def bind(record, signal, **changes):
    return bind_experiment_signal(
        record,
        **{
            "variant_key": "baseline",
            "spec": spec(),
            "strategy_content_hash": "a" * 64,
            "signal": signal,
            **changes,
        },
    )


@pytest.mark.parametrize("source", ["internal_simulation", "blofin_demo"])
def test_bound_research_uses_exact_experiment_contract_and_never_claims_execution(source, signal):
    record = version(source=source)
    result = bind(record, signal)
    assert result.organization_id == record.organization_id
    assert result.experiment_version_id == record.id
    assert result.configuration_hash == record.configuration_hash
    assert result.sample_group_id == record.sample_group_id
    assert result.strategy_version_id == record.configuration.strategy_version_id
    assert result.declared_execution_source.value == source
    assert result.declared_native_uid == ("demo-A" if source == "blofin_demo" else None)
    assert not result.account_verified_here and not result.sample_eligible
    assert not result.native_execution and not result.runtime_activated
    assert result.performance is None and record.sample_counts == {"baseline": 0}
    assert bind(record, signal) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("organization_id", uuid4()),
        ("sample_group_id", uuid4()),
        ("id", uuid4()),
        ("configuration_hash", "b" * 64),
    ],
)
def test_stale_hash_cannot_relabel_tenant_version_or_sample_group(field, value, signal):
    record = version().model_copy(update={field: value})
    with pytest.raises(ValueError, match="experiment_configuration_hash_mismatch"):
        bind(record, signal)


def test_account_uid_and_source_isolation_are_explicit_in_research_bindings(signal):
    a = version(source="blofin_demo")
    b = a.model_copy(
        update={
            "configuration": a.configuration.model_copy(
                update={
                    "account": a.configuration.account.model_copy(
                        update={"native_uid": "demo-B", "execution_account_id": uuid4()}
                    )
                }
            )
        }
    )
    with pytest.raises(ValueError, match="experiment_configuration_hash_mismatch"):
        bind(b, signal)
    b = seal(b)
    aa, bb = bind(a, signal), bind(b, signal)
    simulation = bind(version(), signal)
    assert aa.binding_id != bb.binding_id != simulation.binding_id
    assert aa.declared_native_uid == "demo-A" and bb.declared_native_uid == "demo-B"
    assert simulation.declared_native_uid is None
    assert simulation.declared_execution_source != aa.declared_execution_source


@pytest.mark.parametrize(
    "change,message",
    [
        ({"variant_key": "unknown"}, "experiment_family_or_variant_mismatch"),
        ({"strategy_content_hash": "b" * 64}, "immutable_strategy_content_mismatch"),
    ],
)
def test_wrong_variant_or_immutable_strategy_hash_is_refused(change, message, signal):
    with pytest.raises(ValueError, match=message):
        bind(version(), signal, **change)


@pytest.mark.parametrize(
    "change",
    [
        {"symbols": ("ETHUSDT",)},
        {"timeframes": ("5m",)},
        {"family": "swing_failure_pattern/v1"},
    ],
)
def test_family_and_universe_mismatch_do_not_reinterpret_nested_or_sfp(change, signal):
    record = version()
    config = ExperimentConfiguration.model_validate({**record.configuration.model_dump(), **change})
    record = seal(record.model_copy(update={"configuration": config}))
    with pytest.raises(ValueError, match="mismatch"):
        bind(record, signal)


def test_changed_parameters_and_signal_prices_are_not_immutable_variant_overrides(signal):
    record = version()
    variant = record.configuration.variants[0]
    bad = variant.model_copy(
        update={"parameters": {**variant.parameters, "minimum_slope_ratio": "0.0001"}}
    )
    record = seal(
        record.model_copy(
            update={"configuration": record.configuration.model_copy(update={"variants": (bad,)})}
        )
    )
    with pytest.raises(ValueError, match="fixed"):
        bind(record, signal)
    with pytest.raises(ValueError, match="signal_binding_mismatch"):
        bind(version(), signal.model_copy(update={"target": signal.target + 1}))


def test_promotion_gets_a_fresh_attribution_and_cannot_carry_a_sample_or_approval(signal):
    exploration = version()
    validation = seal(
        exploration.model_copy(
            update={
                "id": uuid4(),
                "parent_version_id": exploration.id,
                "version": 2,
                "sample_group_id": uuid4(),
                "configuration": exploration.configuration.model_copy(
                    update={"mode": ExperimentMode.VALIDATION}
                ),
                "created_at": END + timedelta(days=1),
            }
        )
    )
    a, b = bind(exploration, signal), bind(validation, signal)
    assert a.binding_id != b.binding_id and a.sample_group_id != b.sample_group_id
    assert not a.sample_eligible and not b.sample_eligible
    assert validation.sample_counts == {"baseline": 0} and validation.approved_at is None
    # Real promotion freshness enforcement remains the domain service/resolver's job.


def test_published_pure_interface_schema_matches_the_authoritative_models():
    import json
    from pathlib import Path

    from app.strategy_brain.trendpulse_1r.interface import TrendPulseInterface

    artifact = Path(__file__).resolve().parents[2] / "docs/contracts/trendpulse_1r.v1.schema.json"
    assert json.loads(artifact.read_text()) == TrendPulseInterface.model_json_schema()
