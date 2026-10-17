"""Pure research attribution using experiment-config/v1; never trusted sample proof."""

from typing import Literal
from uuid import UUID, uuid5

from pydantic import Field

from app.market_contracts.models import CanonicalModel
from app.schemas.experiments import ExperimentFamily, ExperimentSource, ExperimentVersion
from app.strategy_brain.trendpulse_1r.contracts import (
    TRENDPULSE_NAMESPACE,
    TrendPulseParameters,
    TrendPulseSignal,
    TrendPulseSpec,
    exact_hash,
)


class ExperimentBoundTrendPulse(CanonicalModel):
    contract_version: Literal["trendpulse-experiment-research/v1"] = (
        "trendpulse-experiment-research/v1"
    )
    binding_id: UUID
    organization_id: UUID
    experiment_id: UUID
    experiment_version_id: UUID
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_group_id: UUID
    variant_key: str
    strategy_id: UUID
    strategy_version_id: UUID
    strategy_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    declared_execution_source: ExperimentSource
    declared_execution_account_id: UUID
    declared_native_uid: str | None
    signal: TrendPulseSignal
    account_verified_here: Literal[False] = False
    sample_eligible: Literal[False] = False
    native_execution: Literal[False] = False
    runtime_activated: Literal[False] = False
    performance: None = None


def bind_experiment_signal(
    version: ExperimentVersion,
    *,
    variant_key: str,
    spec: TrendPulseSpec,
    strategy_content_hash: str,
    signal: TrendPulseSignal,
) -> ExperimentBoundTrendPulse:
    """Caller supplies a trusted immutable strategy lookup; this function has no I/O.

    Research attribution can be calculated for a draft. It grants neither approval,
    account verification nor permission to ingest a sample. Runtime must resolve all
    source/strategy/account records again through their existing authorities.
    """
    config = version.configuration
    digest = exact_hash(
        {
            "organization_id": version.organization_id,
            "user_id": version.user_id,
            "experiment_id": version.experiment_id,
            "version_id": version.id,
            "sample_group_id": version.sample_group_id,
            "configuration": config.model_dump(),
            "strategy_content_hashes": version.strategy_content_hashes,
        }
    )
    if digest != version.configuration_hash:
        raise ValueError("experiment_configuration_hash_mismatch")
    variants = [v for v in config.variants if v.key == variant_key]
    if config.family is not ExperimentFamily.TRENDPULSE_1R or len(variants) != 1:
        raise ValueError("experiment_family_or_variant_mismatch")
    variant = variants[0]
    if (
        version.strategy_content_hashes.get(str(variant.strategy_version_id))
        != strategy_content_hash
        or len(strategy_content_hash) != 64
    ):
        raise ValueError("immutable_strategy_content_mismatch")
    parameters = TrendPulseParameters.model_validate(variant.parameters)
    if exact_hash(parameters) != exact_hash(spec.parameters):
        raise ValueError("immutable_strategy_parameters_mismatch")
    if (
        spec.symbol not in config.symbols
        or not {spec.trend_timeframe, spec.trigger_timeframe}.issubset(config.timeframes)
        or signal.spec_hash != exact_hash(spec)
        or signal.identity.instrument.provider_symbol != spec.symbol
        or signal.direction != spec.direction
        or signal.content_hash != exact_hash(signal.model_dump(exclude={"content_hash"}))
    ):
        raise ValueError("signal_binding_mismatch")
    fields = {
        "organization_id": version.organization_id,
        "experiment_id": version.experiment_id,
        "experiment_version_id": version.id,
        "configuration_hash": version.configuration_hash,
        "sample_group_id": version.sample_group_id,
        "variant_key": variant_key,
        "strategy_id": config.strategy_id,
        "strategy_version_id": variant.strategy_version_id,
        "strategy_content_hash": strategy_content_hash,
        "declared_execution_source": config.account.source,
        "declared_execution_account_id": config.account.execution_account_id,
        "declared_native_uid": config.account.native_uid,
        "signal": signal,
    }
    return ExperimentBoundTrendPulse(
        binding_id=uuid5(
            TRENDPULSE_NAMESPACE, exact_hash({**fields, "signal": signal.model_dump()})
        ),
        **fields,
    )
