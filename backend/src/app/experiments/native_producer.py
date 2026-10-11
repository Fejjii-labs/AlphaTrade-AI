"""Server-owned native origin/position receipts, without execution authority.

The existing governed canonical coordinator calls ``register_plan`` before
creating a command and commits origin, then ``bind_entry`` and its receipt before
dispatch, when explicit server-validated experiment context is supplied.
Neither method is exposed through
HTTP or selected by a strategy/client-order similarity match. Transactions belong
to that caller, not this producer. No order is sent.

Normal activity history and the existing governed close projection do not carry
complete protected parent/child ancestry or independently verified money semantics.
``reconcile`` therefore records a durable diagnostic, never a guessed exit proof.
The existing reader preserves its separately trusted resolver contract, while
producer-owned commands remain unavailable. HTTP sample admission remains closed.
"""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.blofin_identity import connection_binding
from app.core.blofin_readonly_access import BloFinReadOnlyClient
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.db.blofin_activity import BloFinActivityFact
from app.db.models import (
    ApprovalAuthorization,
    AuditLog,
    ExecutionCommand,
    GovernedDemoLifecycleResolution,
    TradePlanRevision,
    UserStrategy,
    UserStrategyVersion,
    VenueSubmitEffect,
)
from app.db.strategy_immutability import strategy_version_content_hash
from app.experiments.blofin_source import BloFinExperimentSourceResolver, exact, plan_symbol, refuse
from app.experiments.identity import utc
from app.experiments.native_lineage import NativeEntryBinding
from app.experiments.outcome_contract import NativeOutcome
from app.experiments.repository import ExperimentRepository
from app.experiments.service import ExperimentService, semantic_hash
from app.market_contracts.models import CanonicalModel
from app.persistence.trade_plan_postgres import PostgresCanonicalTradePlanStore
from app.providers.exchange.blofin_activity import BloFinActivityProvider, identifier
from app.schemas.canonical_trade_plan import CanonicalTradePlanRevision
from app.schemas.common import ActorType, AuditResult
from app.schemas.execution_protocol import ExecutionCommandOutcome, VenueSubmitEffectState
from app.schemas.experiments import ExperimentSource, ExperimentVersion
from app.schemas.trade_plan import AuthorizationState, QuantityUnit, TradePlanRevisionSemantic
from app.security.tenant import TenantContext
from app.services.canonical_serialization import canonical_sha256

ACTOR = "experiment_executor"
RESOURCE = "experiment_execution"
ORIGIN = "experiment_native_plan_origin"
POSITION = "experiment_native_pre_entry_position"
ENTRY = "experiment_native_entry_binding"
DIAGNOSTIC = "experiment_native_producer_diagnostic"


class NativePlanOrigin(CanonicalModel):
    contract_version: Literal["experiment-native-plan-origin/v1"] = (
        "experiment-native-plan-origin/v1"
    )
    organization_id: UUID
    user_id: UUID
    experiment_id: UUID
    version_id: UUID
    configuration_hash: str
    sample_group_id: UUID
    execution_account_id: UUID
    native_uid: str
    variant_key: str
    strategy_version_id: UUID
    strategy_content_hash: str
    revision_id: UUID
    plan_content_hash: str
    envelope_content_hash: str
    recorded_at: AwareDatetime


class NativePreEntryPosition(CanonicalModel):
    contract_version: Literal["experiment-native-pre-entry-position/v1"] = (
        "experiment-native-pre-entry-position/v1"
    )
    origin_hash: str
    command_id: UUID
    execution_account_id: UUID
    native_uid: str
    credential_binding: str
    instrument: str
    observed_at: AwareDatetime
    positions_source: Literal["/api/v1/account/positions"] = "/api/v1/account/positions"
    positions: tuple[dict[str, str], ...]
    instrument_source: Literal["/api/v1/market/instruments"] = "/api/v1/market/instruments"
    instrument_metadata: dict[str, str]


class NativeProducerDiagnostic(CanonicalModel):
    contract_version: Literal["experiment-native-producer-diagnostic/v1"] = (
        "experiment-native-producer-diagnostic/v1"
    )
    command_id: UUID
    origin_hash: str
    position_hash: str
    reason: str
    # A lifecycle close is useful evidence, never a monetary/parent attestation.
    governed_resolution_id: UUID | None = None
    governed_resolution_hash: str | None = None


class BloFinNativeOutcomeProducer:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        tenant: TenantContext,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session, self.settings, self.tenant = session, settings, tenant
        self.clock = clock or (lambda: datetime.now(UTC))
        self.resolver = BloFinExperimentSourceResolver(session, settings, tenant, clock=self.clock)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("Native producer clock must be timezone-aware.")
        return value.astimezone(UTC)

    def _lock(self, scope: str) -> None:
        if self.session.get_bind().dialect.name != "postgresql":
            refuse("native_attribution_postgres_required")
        key = int.from_bytes(hashlib.sha256(scope.encode()).digest()[:8], "big") >> 1
        self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    def _version(self, experiment_id: UUID, version_id: UUID) -> ExperimentVersion:
        row = ExperimentRepository(self.session, self.tenant).version(experiment_id, version_id)
        version = ExperimentService(self.session, self.settings).view(row)
        if version.configuration.account.source is not ExperimentSource.BLOFIN_DEMO:
            refuse("experiment_native_source_required")
        self.resolver._version(version, self._now())
        return version

    def _audit(self, resource_id: UUID, operation: str) -> AuditLog | None:
        rows = list(
            self.session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.organization_id == self.tenant.organization_id,
                    AuditLog.user_id == self.tenant.user_id,
                    AuditLog.resource_type == RESOURCE,
                    AuditLog.resource_id == str(resource_id),
                    AuditLog.redacted_metadata["operation"].as_string() == operation,
                )
                .limit(2)
            )
        )
        if len(rows) > 1:
            refuse("experiment_lineage_conflict")
        return rows[0] if rows else None

    def _body(self, row: AuditLog) -> dict[str, Any]:
        body = dict(row.redacted_metadata)
        digest = body.pop("evidence_hash", None)
        operation = body.pop("operation", None)
        model: type[CanonicalModel]
        if operation == ORIGIN:
            model = NativePlanOrigin
        elif operation == POSITION:
            model = NativePreEntryPosition
        elif operation == ENTRY:
            model = NativeEntryBinding
        elif isinstance(operation, str) and operation.startswith(DIAGNOSTIC + ":"):
            model = NativeProducerDiagnostic
        else:
            refuse("experiment_ownership_unverified")
        # Rehydrate typed dates/decimals before hashing; wire JSON strings have
        # different canonical semantics from Python datetime/Decimal values.
        body = model.model_validate(body).model_dump()
        if (
            row.actor != ACTOR
            or row.actor_type != ActorType.SYSTEM
            or row.result != AuditResult.SUCCESS
            or utc(row.event_at) != utc(row.created_at)
            or utc(row.created_at) > self._now()
            or row.payload_hash != digest
            or semantic_hash(body) != digest
        ):
            refuse("experiment_ownership_unverified")
        return body

    def _store(
        self,
        resource_id: UUID,
        operation: str,
        contract: CanonicalModel,
        *,
        recorded_at: datetime | None = None,
    ) -> AuditLog:
        digest = semantic_hash(contract.model_dump())
        existing = self._audit(resource_id, operation)
        if existing is not None:
            if semantic_hash(self._body(existing)) != digest:
                refuse("experiment_lineage_conflict")
            return existing
        now = recorded_at if recorded_at is not None else self._now()
        if now.tzinfo is None or now > self._now():
            raise ValueError("Native receipt time must be aware and not future-dated.")
        row = AuditLog(
            id=uuid5(resource_id, operation),
            organization_id=self.tenant.organization_id,
            user_id=self.tenant.user_id,
            actor=ACTOR,
            actor_type=ActorType.SYSTEM,
            action="demo_lifecycle_reconciled",
            resource_type=RESOURCE,
            resource_id=str(resource_id),
            result=AuditResult.SUCCESS,
            payload_hash=digest,
            redacted_metadata={
                "operation": operation,
                "evidence_hash": digest,
                **contract.model_dump(mode="json"),
            },
            created_at=now,
            updated_at=now,
            event_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _canonical_plan(
        self,
        version: ExperimentVersion,
        variant_key: str,
        revision_id: UUID,
        *,
        require_current_validity: bool = True,
    ) -> CanonicalTradePlanRevision:
        store = PostgresCanonicalTradePlanStore(sessionmaker(bind=self.session.get_bind()))
        with store.bind_session(self.session):
            envelope = store.get_by_revision(
                organization_id=self.tenant.organization_id,
                user_id=self.tenant.user_id,
                revision_id=revision_id,
            )
        if envelope is None:
            refuse("experiment_canonical_plan_required")
        stored_plan = self.session.get(TradePlanRevision, revision_id)
        if stored_plan is None:
            refuse("experiment_canonical_plan_required")
        plan = envelope.plan
        variant = next((v for v in version.configuration.variants if v.key == variant_key), None)
        if variant is None:
            refuse("experiment_variant_unknown")
        semantic = TradePlanRevisionSemantic.model_validate(
            plan.model_dump(
                exclude={"correlation_id", "content_hash", "created_at", "presentation_metadata"}
            )
        )
        envelope_hash = canonical_sha256(
            {
                "lineage": envelope.lineage.model_dump(),
                "plan_content_hash": plan.content_hash,
                "uniqueness_hash": envelope.uniqueness_hash,
            }
        )
        # Require persisted canonical authority and recheck both immutable hashes;
        # a phase-1/manual plan with a strategy UUID alone cannot pass this boundary.
        if (
            canonical_sha256(semantic) != plan.content_hash
            or envelope_hash != envelope.content_hash
            or plan.account_id != version.configuration.account.execution_account_id
            or plan.strategy_version_id != variant.strategy_version_id
            or plan.execution_venue != "BLOFIN_DEMO"
            or plan.quantity_unit is not QuantityUnit.CONTRACTS
            or plan.timeframe not in version.configuration.timeframes
            or plan_symbol(self.session, stored_plan) not in version.configuration.symbols
            or (require_current_validity and not plan.valid_from <= self._now() < plan.valid_until)
        ):
            refuse("experiment_command_lineage_invalid")
        strategy = self.session.get(UserStrategyVersion, variant.strategy_version_id)
        parent = self.session.get(UserStrategy, strategy.strategy_id) if strategy else None
        if (
            strategy is None
            or parent is None
            or parent.id != version.configuration.strategy_id
            or (parent.organization_id, parent.user_id)
            != (self.tenant.organization_id, self.tenant.user_id)
            or strategy.content_hash != version.strategy_content_hashes[str(strategy.id)]
            or strategy_version_content_hash(
                card=strategy.card,
                structured_rules=strategy.structured_rules,
                lesson_source_metadata=strategy.lesson_source_metadata,
                pattern_spec=strategy.pattern_spec,
            )
            != strategy.content_hash
        ):
            refuse("experiment_attribution_mismatch")
        return envelope

    def register_plan(
        self, experiment_id: UUID, version_id: UUID, variant_key: str, revision_id: UUID
    ) -> NativePlanOrigin:
        """Persist explicit experiment origin before any command for this plan.

        The coordinator must commit before command creation. A replay returns the
        original timestamp; it never reassigns an existing plan to another version.
        """
        self._lock(f"native-plan-origin:{self.tenant.organization_id}:{revision_id}")
        version = self._version(experiment_id, version_id)
        existing = self._audit(revision_id, ORIGIN)
        envelope = self._canonical_plan(
            version, variant_key, revision_id, require_current_validity=existing is None
        )
        if existing is not None:
            origin = NativePlanOrigin.model_validate(self._body(existing))
            if (
                origin.version_id != version.id
                or origin.variant_key != variant_key
                or origin.configuration_hash != version.configuration_hash
                or origin.plan_content_hash != envelope.plan.content_hash
                or origin.envelope_content_hash != envelope.content_hash
            ):
                refuse("experiment_lineage_conflict")
            return origin
        if (
            self.session.scalar(
                select(ExecutionCommand.id)
                .where(ExecutionCommand.revision_id == revision_id)
                .limit(1)
            )
            is not None
        ):
            refuse("experiment_origin_must_precede_command")
        if (
            version.state != "running"
            or version.started_at is None
            or version.authorized_until is None
            or not utc(version.started_at) <= self._now() < utc(version.authorized_until)
        ):
            refuse("experiment_sample_window")
        native_uid = version.configuration.account.native_uid
        assert native_uid is not None
        origin = NativePlanOrigin(
            organization_id=version.organization_id,
            user_id=version.user_id,
            experiment_id=version.experiment_id,
            version_id=version.id,
            configuration_hash=version.configuration_hash,
            sample_group_id=version.sample_group_id,
            execution_account_id=envelope.plan.account_id,
            native_uid=native_uid,
            variant_key=variant_key,
            strategy_version_id=envelope.plan.strategy_version_id,
            strategy_content_hash=version.strategy_content_hashes[
                str(envelope.plan.strategy_version_id)
            ],
            revision_id=revision_id,
            plan_content_hash=envelope.plan.content_hash,
            envelope_content_hash=envelope.content_hash,
            recorded_at=self._now(),
        )
        self._store(revision_id, ORIGIN, origin, recorded_at=origin.recorded_at)
        return origin

    def _command(self, command_id: UUID) -> ExecutionCommand:
        command = self.session.get(ExecutionCommand, command_id, populate_existing=True)
        if command is None or (command.organization_id, command.user_id) != (
            self.tenant.organization_id,
            self.tenant.user_id,
        ):
            raise NotFoundError("Execution command not found.")
        return command

    def _origin(self, command: ExecutionCommand) -> tuple[AuditLog, NativePlanOrigin]:
        audit = self._audit(command.revision_id, ORIGIN)
        if audit is None:
            refuse("experiment_ownership_unverified")
        # An origin flushed in this transaction is not pre-existing durable
        # authority. Observe it through a separate committed read before any
        # position IO, as the canonical dispatch boundary does for its fence.
        with Session(self.session.get_bind().engine) as probe:
            committed = probe.get(AuditLog, audit.id)
            if committed is None or committed.payload_hash != audit.payload_hash:
                refuse("experiment_origin_not_committed")
        origin = NativePlanOrigin.model_validate(self._body(audit))
        if (
            origin.organization_id != command.organization_id
            or origin.user_id != command.user_id
            or origin.execution_account_id != command.account_id
            or origin.revision_id != command.revision_id
            or origin.plan_content_hash != command.plan_content_hash
            or utc(audit.created_at) != origin.recorded_at
            or not origin.recorded_at < utc(command.created_at) <= self._now()
            or command.outcome is not ExecutionCommandOutcome.ALLOW
        ):
            refuse("experiment_command_lineage_invalid")
        return audit, origin

    def bind_entry(
        self, command_id: UUID, *, provider: BloFinActivityProvider
    ) -> NativeEntryBinding:
        """Capture signed UID + flat inventory before dispatch, never caller flags.

        Accepts only the restricted GET transport. Its credential selector must
        match the stored/current read-only connection. No retry or dispatch occurs.
        """
        self._lock(f"native-entry:{self.tenant.organization_id}:{command_id}")
        command = self._command(command_id)
        origin_audit, origin = self._origin(command)
        version = self._version(origin.experiment_id, origin.version_id)
        existing = self._audit(command_id, ENTRY)
        envelope = self._canonical_plan(
            version,
            origin.variant_key,
            command.revision_id,
            require_current_validity=existing is None,
        )
        if existing is not None:
            # Reloads return the exact original receipt, without provider IO or a
            # new observation replacing the original pre-entry position.
            self._position(command, origin_audit)
            binding = NativeEntryBinding.model_validate(self._body(existing))
            if binding != self._binding(origin, command):
                refuse("experiment_lineage_conflict")
            return binding
        authorization = self.session.get(ApprovalAuthorization, command.authorization_id)
        effect = self.session.scalar(
            select(VenueSubmitEffect)
            .where(VenueSubmitEffect.command_id == command_id)
            .with_for_update()
        )
        if (
            authorization is None
            or authorization.state is not AuthorizationState.CONSUMED
            or authorization.consumed_by_execution_command_id != command.id
            or authorization.revision_id != command.revision_id
            or authorization.plan_content_hash != command.plan_content_hash
            or effect is None
            or effect.state is not VenueSubmitEffectState.CREATED
            or effect.attempt != 0
            or effect.uncertainty
            or effect.dispatch_authorized_at is not None
        ):
            refuse("native_position_capture_must_precede_dispatch")
        if version.state != "running":
            refuse("experiment_sample_window")
        self._verify_provider_binding(provider)
        provider.verify_identity(origin.native_uid, lambda: None)
        data = provider.client.request("GET", "/api/v1/account/positions", signed=True)
        if not isinstance(data, list) or len(data) >= 100:
            refuse("native_pre_entry_position_unverified")
        positions = []
        for item in data:
            if not isinstance(item, dict) or item.get("positionSide") != "net":
                refuse("native_pre_entry_position_unverified")
            instrument = identifier(item.get("instId"))
            quantity = exact(item.get("positions"))
            if quantity != 0:
                refuse("native_pre_entry_position_not_flat")
            positions.append({"instrument": instrument, "position_side": "net", "quantity": "0"})
        if len({p["instrument"] for p in positions}) != len(positions):
            refuse("native_pre_entry_position_unverified")
        metadata = provider.client.request("GET", "/api/v1/market/instruments")
        if not isinstance(metadata, list) or len(metadata) > 5000:
            refuse("native_contract_metadata_unverified")
        matches = [
            m
            for m in metadata
            if isinstance(m, dict) and m.get("instId") == envelope.plan.execution_instrument
        ]
        if len(matches) != 1:
            refuse("native_contract_metadata_unverified")
        item, rules = matches[0], envelope.plan.instrument_rules
        if (
            item.get("state") != "live"
            or item.get("instType") not in {"SWAP", "PERPETUAL"}
            or item.get("contractType") != rules.contract_type.value.lower()
            or item.get("baseCurrency") != rules.base_currency
            or item.get("quoteCurrency") != rules.quote_currency
            or item.get("settleCurrency") != rules.settlement_currency
            or item.get("contractValueCurrency") != rules.base_currency
            or exact(item.get("contractValue")) != rules.contract_multiplier
        ):
            refuse("native_contract_metadata_unverified")
        # Reverify identity after the position/metadata reads: a changed connection
        # never signs an old UID's position as this experiment's original state.
        provider.verify_identity(origin.native_uid, lambda: None)
        observed = self._now()
        if version.authorized_until is None or observed >= utc(version.authorized_until):
            refuse("experiment_sample_window")
        proof = NativePreEntryPosition(
            origin_hash=origin_audit.payload_hash,
            command_id=command.id,
            execution_account_id=command.account_id,
            native_uid=origin.native_uid,
            credential_binding=connection_binding(self.settings, readonly=True),
            instrument=envelope.plan.execution_instrument,
            observed_at=observed,
            positions=tuple(sorted(positions, key=lambda p: p["instrument"])),
            instrument_metadata={
                k: str(item[k])
                for k in (
                    "instId",
                    "state",
                    "instType",
                    "contractType",
                    "baseCurrency",
                    "quoteCurrency",
                    "settleCurrency",
                    "contractValueCurrency",
                    "contractValue",
                )
            },
        )
        self._store(command_id, POSITION, proof, recorded_at=observed)
        binding = self._binding(origin, command)
        self._store(command_id, ENTRY, binding, recorded_at=observed)
        return binding

    @staticmethod
    def _binding(origin: NativePlanOrigin, command: ExecutionCommand) -> NativeEntryBinding:
        return NativeEntryBinding(
            organization_id=origin.organization_id,
            user_id=origin.user_id,
            experiment_id=origin.experiment_id,
            version_id=origin.version_id,
            configuration_hash=origin.configuration_hash,
            sample_group_id=origin.sample_group_id,
            execution_account_id=origin.execution_account_id,
            native_uid=origin.native_uid,
            strategy_version_id=origin.strategy_version_id,
            strategy_content_hash=origin.strategy_content_hash,
            variant_key=origin.variant_key,
            ownership_command_id=command.id,
            plan_content_hash=command.plan_content_hash,
            pre_entry_position_flat=True,
        )

    def _verify_provider_binding(self, provider: BloFinActivityProvider) -> None:
        # The transport is created by the existing read-only capability gate.
        # An injected connection must carry the same original selector.
        if not isinstance(provider, BloFinActivityProvider) or not isinstance(
            provider.client, BloFinReadOnlyClient
        ):
            refuse("experiment_account_unverified")
        client = provider.client
        expected = self.settings.model_copy(
            update={
                "blofin_readonly_api_key": client._api_key,
                "blofin_readonly_api_secret": client._api_secret.decode("utf-8"),
                "blofin_readonly_api_passphrase": client._api_passphrase,
                "blofin_demo_rest_base_url": client._base_url,
            }
        )
        if connection_binding(expected, readonly=True) != connection_binding(
            self.settings, readonly=True
        ):
            refuse("experiment_account_unverified")

    def _position(
        self, command: ExecutionCommand, origin_audit: AuditLog
    ) -> NativePreEntryPosition:
        row = self._audit(command.id, POSITION)
        if row is None:
            refuse("native_pre_entry_position_unverified")
        proof = NativePreEntryPosition.model_validate(self._body(row))
        origin = NativePlanOrigin.model_validate(self._body(origin_audit))
        plan = self.session.get(TradePlanRevision, command.revision_id)
        effect = self.session.scalar(
            select(VenueSubmitEffect).where(VenueSubmitEffect.command_id == command.id)
        )
        if (
            proof.command_id != command.id
            or proof.execution_account_id != command.account_id
            or proof.origin_hash != origin_audit.payload_hash
            or proof.native_uid != origin.native_uid
            or plan is None
            or proof.instrument != plan.execution_instrument
            or any(
                p.get("position_side") != "net" or p.get("quantity") != "0" for p in proof.positions
            )
            or proof.observed_at != utc(row.created_at)
            or proof.observed_at < utc(command.created_at)
            or (
                effect is not None
                and effect.dispatch_authorized_at is not None
                and proof.observed_at > utc(effect.dispatch_authorized_at)
            )
        ):
            refuse("native_pre_entry_position_unverified")
        return proof

    def reconcile(self, command_id: UUID) -> NativeOutcome:
        """Durable, scoped reconciliation; incomplete facts never become samples.

        Diagnose the exact missing source. The governed resolution's isolated
        close does not substitute for native protected parent evidence, and raw
        exit-attestation booleans are never evidence for this producer.
        """
        self._lock(f"native-entry:{self.tenant.organization_id}:{command_id}")
        command = self._command(command_id)
        origin_audit, origin = self._origin(command)
        version = self._version(origin.experiment_id, origin.version_id)
        self._position(command, origin_audit)
        result = self.resolver.read(version, origin.variant_key, str(command.id))
        result = self.qualify_existing_origin(version, origin.variant_key, str(command.id), result)
        reason, resolution = self._closing_diagnostic(command, origin)
        position_row = self._audit(command.id, POSITION)
        assert position_row is not None
        diagnostic = NativeProducerDiagnostic(
            command_id=command.id,
            origin_hash=origin_audit.payload_hash,
            position_hash=position_row.payload_hash,
            reason=reason,
            governed_resolution_id=resolution.id if resolution else None,
            governed_resolution_hash=resolution.content_hash if resolution else None,
        )
        # Reasons evolve as native facts arrive; one immutable receipt per exact
        # diagnostic converges across restart/concurrent reconciliation.
        operation = DIAGNOSTIC + ":" + semantic_hash(diagnostic.model_dump())
        self._store(command.id, operation, diagnostic)
        return result

    def qualify_existing_origin(
        self,
        version: ExperimentVersion,
        variant_key: str,
        source_record_id: str,
        result: NativeOutcome,
    ) -> NativeOutcome:
        """Database-only reader guard for producer-owned commands.

        Retain the prior resolver contract for separately trusted legacy adapters.
        This producer's newly registered commands cannot obtain performance merely
        by storing legacy exit booleans. Its closing evidence source is incomplete.
        """
        try:
            command_id = UUID(source_record_id)
        except ValueError:
            return result
        command = self.session.get(ExecutionCommand, command_id)
        if (
            command is None
            or (command.organization_id, command.user_id)
            != (self.tenant.organization_id, self.tenant.user_id)
            or self._audit(command.revision_id, ORIGIN) is None
        ):
            return result
        try:
            origin_audit, origin = self._origin(command)
            self._position(command, origin_audit)
            if (origin.experiment_id, origin.version_id, origin.variant_key) != (
                version.experiment_id,
                version.id,
                variant_key,
            ):
                refuse("experiment_attribution_mismatch")
            reason, _resolution = self._closing_diagnostic(command, origin)
        except ConflictError as error:
            reason = error.code
        except (ValidationError, ValueError, TypeError, KeyError):
            reason = "native_evidence_invalid"
        # Never expose provisional money or a supposedly eligible sample proof.
        return result.model_copy(
            update={
                "status": "unavailable",
                "reason": reason,
                "proof": None,
                "reported_realized_pnl": None,
                "fee_cost": None,
                "realized_pnl_after_fees_excluding_funding": None,
                "coverage": "incomplete",
                "freshness": "unavailable",
            }
        )

    def _closing_diagnostic(
        self, command: ExecutionCommand, origin: NativePlanOrigin
    ) -> tuple[str, GovernedDemoLifecycleResolution | None]:
        resolution = self.session.scalar(
            select(GovernedDemoLifecycleResolution).where(
                GovernedDemoLifecycleResolution.command_id == command.id,
                GovernedDemoLifecycleResolution.organization_id == self.tenant.organization_id,
                GovernedDemoLifecycleResolution.user_id == self.tenant.user_id,
                GovernedDemoLifecycleResolution.account_id == command.account_id,
                GovernedDemoLifecycleResolution.revision_id == command.revision_id,
            )
        )
        native_entry = self.session.scalar(
            select(BloFinActivityFact)
            .join(
                VenueSubmitEffect,
                BloFinActivityFact.payload["client_order_id"].as_string()
                == VenueSubmitEffect.client_order_id,
            )
            .where(
                VenueSubmitEffect.command_id == command.id,
                BloFinActivityFact.organization_id == self.tenant.organization_id,
                BloFinActivityFact.environment == "demo",
                BloFinActivityFact.account_uid == origin.native_uid,
                BloFinActivityFact.kind == "order",
            )
            .limit(1)
        )
        reason = (
            "native_entry_receipt_unavailable"
            if native_entry is None
            else (
                "native_exit_parent_unverified"
                if resolution is not None
                else "native_closed_position_evidence_unavailable"
            )
        )
        if (
            resolution is not None
            and canonical_sha256(resolution.evidence_payload) != resolution.content_hash
        ):
            reason = "native_lifecycle_evidence_invalid"
        return reason, resolution
