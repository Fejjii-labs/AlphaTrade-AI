"""Stored executor attestations, separate from manual identity and HTTP sample claims.

No writer, execution adapter or endpoint is installed here. The reviewed executor
must persist these exact bindings in existing AuditLog storage; hashes detect
content corruption, while the server-only producer and relational checks establish
trust. An echoed client order ID is never an attestation.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.experiments.attribution import ExperimentSourceProof
from app.market_contracts.models import CanonicalModel

Hash = str


class NativeEntryBinding(CanonicalModel):
    contract_version: Literal["experiment-native-entry/v1"] = "experiment-native-entry/v1"
    organization_id: UUID
    user_id: UUID
    experiment_id: UUID
    version_id: UUID
    configuration_hash: Hash = Field(pattern=r"^[0-9a-f]{64}$")
    sample_group_id: UUID
    execution_account_id: UUID
    native_uid: str = Field(min_length=1, max_length=128)
    strategy_version_id: UUID
    strategy_content_hash: Hash = Field(pattern=r"^[0-9a-f]{64}$")
    variant_key: str
    ownership_command_id: UUID
    plan_content_hash: Hash = Field(pattern=r"^[0-9a-f]{64}$")
    environment: Literal["demo"] = "demo"
    authority_origin: Literal["experiment"] = "experiment"
    pre_entry_position_flat: bool | None = Field(
        default=None,
        strict=True,
        description=(
            "Trusted server producer verified the exact UID/instrument NET position was flat "
            "before this opening command; a client echo or reduction cannot establish this."
        ),
    )


class NativeExitLineage(CanonicalModel):
    contract_version: Literal["experiment-native-exit/v1"] = "experiment-native-exit/v1"
    entry_binding_hash: Hash = Field(pattern=r"^[0-9a-f]{64}$")
    native_entry_order_id: str = Field(min_length=1, max_length=128)
    native_exit_order_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    entry_fill_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    exit_fill_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    position_lineage_verified: bool | None = Field(
        default=None,
        strict=True,
        description=(
            "Trusted server producer verified complete flat-to-open-to-flat native position "
            "ancestry, including all owned fills and absence of unrelated inventory or fills."
        ),
    )
    # Absent official/independently verified monetary semantics remain unknown.
    pnl_excludes_fees: Literal[True] | None = None
    fee_convention: Literal["positive_cost", "signed_cashflow"] | None = None
    monetary_methodology_version: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("native_exit_order_ids", "entry_fill_ids", "exit_fill_ids")
    @classmethod
    def unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or any(not v or len(v) > 128 for v in values):
            raise ValueError("Native lineage identities must be nonempty and unique.")
        return tuple(sorted(values))


class NativeExperimentSourceProof(ExperimentSourceProof):
    """Additive stored JSON proof; a11 already stores opaque proof/evidence hashes."""

    lineage_methodology_version: Literal["experiment-native-lineage/v1"] = (
        "experiment-native-lineage/v1"
    )
    native_exit_order_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    native_entry_fill_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    native_exit_fill_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)

    @field_validator("native_exit_order_ids", "native_entry_fill_ids", "native_exit_fill_ids")
    @classmethod
    def complete_unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return NativeExitLineage.unique_ids(values)

    @model_validator(mode="after")
    def complete_native_lineage(self) -> "NativeExperimentSourceProof":
        if (
            self.kind != "closed_trade"
            or not self.native_uid
            or self.native_exit_order_id != self.native_exit_order_ids[0]
            or (
                self.native_entry_order_id in self.native_exit_order_ids
                or set(self.native_entry_fill_ids) & set(self.native_exit_fill_ids)
            )
        ):
            raise ValueError("Native source proof requires disjoint complete entry/exit lineage.")
        return self
