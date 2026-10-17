"""Trusted adapter seam. HTTP cannot submit source/UID claims or performance values."""

from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.market_contracts.models import CanonicalModel
from app.schemas.experiments import ExperimentSource, ExperimentVersion


class ExperimentSourceProof(CanonicalModel):
    organization_id: UUID
    execution_account_id: UUID
    native_uid: str | None
    source: ExperimentSource
    version_id: UUID
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_group_id: UUID
    strategy_version_id: UUID
    variant_key: str
    kind: Literal["closed_trade", "setup_observation"]
    source_record_id: str
    source_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority_origin: Literal["experiment"]
    ownership_command_id: UUID | None = None
    native_entry_order_id: str | None = None
    native_exit_order_id: str | None = None
    opened_at: AwareDatetime
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def native_trade_proof(self) -> "ExperimentSourceProof":
        if (
            self.kind == "closed_trade"
            and self.source is ExperimentSource.BLOFIN_DEMO
            and not (
                self.native_uid
                and self.ownership_command_id
                and self.native_entry_order_id
                and self.native_exit_order_id
            )
        ):
            raise ValueError(
                "Native closed trades require experiment-owned entry and exit evidence"
            )
        if self.source is ExperimentSource.INTERNAL_SIMULATION and self.native_uid is not None:
            raise ValueError("Simulated samples cannot carry a native UID")
        return self


class ExperimentSourceResolver(Protocol):
    def resolve(
        self, version: ExperimentVersion, variant_key: str, source_record_id: str
    ) -> ExperimentSourceProof:
        """Verify canonical stored record, ownership and closure; never fabricate PnL."""
        ...
