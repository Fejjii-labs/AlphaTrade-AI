"""Fail-closed trusted BloFin resolver over a10 native facts and a11 domain records."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, NoReturn
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError
from app.db.blofin_activity import BloFinActivityAccount, BloFinActivityCursor, BloFinActivityFact
from app.db.canonical_candidates import CanonicalCandidateRow
from app.db.models import (
    AuditLog,
    ExecutionCommand,
    ExecutionReceipt,
    TradePlanRevision,
    UserStrategyVersion,
    VenueSubmitEffect,
)
from app.db.strategy_immutability import strategy_version_content_hash
from app.experiments.attribution import ExperimentSourceProof
from app.experiments.identity import require_account, utc
from app.experiments.models import ExperimentEventRow, ExperimentSampleRow, ExperimentVersionRow
from app.experiments.native_lineage import (
    NativeEntryBinding,
    NativeExitLineage,
    NativeExperimentSourceProof,
)
from app.experiments.outcome_contract import NativeFactProvenance, NativeOutcome
from app.experiments.service import semantic_hash
from app.market_contracts.identity import EvidenceMarketIdentity
from app.market_contracts.models import parse_canonical_decimal
from app.repositories.blofin_activity import fact_hash
from app.schemas.blofin_activity import NativeActivityFact
from app.schemas.common import ActorType, AuditResult
from app.schemas.execution_protocol import ExecutionCommandOutcome
from app.schemas.experiments import ExperimentSource, ExperimentVersion
from app.schemas.trade_plan import QuantityUnit, TradePlanRevisionSemantic
from app.security.tenant import TenantContext

QUANTUM = Decimal("0.000000000000000001")


def refuse(reason: str) -> NoReturn:
    raise ConflictError("Native experiment outcome is unavailable.", code=reason)


def exact(value: object) -> Decimal:
    number = parse_canonical_decimal(value)
    _, digits, exponent = number.as_tuple()
    if (
        not isinstance(exponent, int)
        or len(digits) > 32
        or exponent < -18
        or number.adjusted() > 30
    ):
        refuse("native_decimal_out_of_bounds")
    return number


def rounded(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


def event_time(ms: str | int) -> datetime:
    # Integer millisecond arithmetic avoids timestamp binary-float rounding.
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(ms))


def plan_symbol(session: Session, plan: TradePlanRevision) -> str:
    """Use canonical server identity, never parse a symbol from a client ID.

    Legacy resolver fixtures use phase-1 symbol values. A persisted canonical
    Candidate instead binds a canonical instrument ID and its provider symbol.
    """
    semantic = TradePlanRevisionSemantic.model_validate(plan.semantic_payload)
    if plan.plan_authority != "canonical":
        return semantic.evidence_instrument
    candidate = session.get(CanonicalCandidateRow, plan.canonical_candidate_id)
    if candidate is None:
        refuse("experiment_command_lineage_invalid")
    identity = EvidenceMarketIdentity.model_validate(candidate.evidence_identity)
    if (
        candidate.organization_id != plan.organization_id
        or candidate.strategy_version_id != plan.strategy_version_id
        or candidate.setup_definition_id != plan.compiled_setup_definition_id
        or candidate.evidence_instrument != semantic.evidence_instrument
        or identity.instrument.instrument_id != semantic.evidence_instrument
        or identity.timeframe is None
        or identity.timeframe.value != semantic.timeframe
        or identity.venue.value != semantic.evidence_venue
        or identity.market_type.name != semantic.evidence_market.value
    ):
        refuse("experiment_command_lineage_invalid")
    return identity.instrument.provider_symbol


class BloFinExperimentSourceResolver:
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

    def resolve(
        self, version: ExperimentVersion, variant_key: str, source_record_id: str
    ) -> ExperimentSourceProof:
        # Domain holds version locks first; one account attribution lock serializes
        # competing versions before reconciliation and the sample insert commit.
        if self.session.get_bind().dialect.name != "postgresql":
            refuse("native_attribution_postgres_required")
        uid = version.configuration.account.native_uid
        if version.configuration.account.source is not ExperimentSource.BLOFIN_DEMO or uid is None:
            refuse("experiment_native_source_required")
        scope = f"experiment-outcome:{self.tenant.organization_id}:{uid}"
        lock_id = int.from_bytes(hashlib.sha256(scope.encode()).digest()[:8], "big") >> 1
        self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
        outcome = self.read(version, variant_key, source_record_id)
        if outcome.status != "available" or outcome.proof is None:
            refuse(outcome.reason or "native_outcome_unavailable")
        return outcome.proof

    def read(
        self, version: ExperimentVersion, variant_key: str, source_record_id: str
    ) -> NativeOutcome:
        if version.configuration.account.source is not ExperimentSource.BLOFIN_DEMO:
            refuse("experiment_native_source_required")
        variant = next((v for v in version.configuration.variants if v.key == variant_key), None)
        if variant is None:
            refuse("experiment_variant_unknown")
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("Outcome evaluation clock must be timezone-aware.")
        base: dict[str, Any] = {
            "organization_id": version.organization_id,
            "execution_account_id": version.configuration.account.execution_account_id,
            "native_uid": version.configuration.account.native_uid,
            "version_id": version.id,
            "strategy_version_id": variant.strategy_version_id,
            "variant_key": variant_key,
            "source_record_id": source_record_id,
            "evaluated_at": now,
        }
        with localcontext() as context:
            context.prec, context.rounding = 80, ROUND_HALF_EVEN
            try:
                return self._read(version, base, now)
            except ConflictError as exc:
                return NativeOutcome(**base, status="unavailable", reason=exc.code)
            except (ValidationError, ValueError, ArithmeticError, TypeError, KeyError):
                return NativeOutcome(**base, status="unavailable", reason="native_evidence_invalid")

    def _version(self, version: ExperimentVersion, now: datetime) -> ExperimentVersionRow:
        row = self.session.get(ExperimentVersionRow, version.id, populate_existing=True)
        if (
            row is None
            or (row.organization_id, row.user_id)
            != (self.tenant.organization_id, self.tenant.user_id)
            or (version.organization_id, version.user_id)
            != (self.tenant.organization_id, self.tenant.user_id)
        ):
            refuse("experiment_attribution_mismatch")
        expected_hash = semantic_hash(
            {
                "organization_id": row.organization_id,
                "user_id": row.user_id,
                "experiment_id": row.experiment_id,
                "version_id": row.id,
                "sample_group_id": row.sample_group_id,
                "configuration": version.configuration.model_dump(),
                "strategy_content_hashes": row.strategy_content_hashes,
            }
        )
        if expected_hash != row.configuration_hash or (
            version.configuration_hash,
            version.sample_group_id,
            version.experiment_id,
            version.strategy_content_hashes,
            version.configuration.model_dump(mode="json"),
        ) != (
            row.configuration_hash,
            row.sample_group_id,
            row.experiment_id,
            row.strategy_content_hashes,
            row.configuration,
        ):
            refuse("experiment_attribution_mismatch")
        require_account(
            self.session, self.tenant, version.configuration.account, self.settings, now=now
        )
        return row

    def _attestation(
        self, command_id: UUID, operation: str, now: datetime
    ) -> tuple[AuditLog, dict[str, Any], str]:
        rows = list(
            self.session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.organization_id == self.tenant.organization_id,
                    AuditLog.user_id == self.tenant.user_id,
                    AuditLog.resource_type == "experiment_execution",
                    AuditLog.resource_id == str(command_id),
                    AuditLog.redacted_metadata["operation"].as_string() == operation,
                )
                .order_by(AuditLog.created_at, AuditLog.id)
                .limit(101)
            )
        )
        if not rows or len(rows) > 100:
            refuse("experiment_ownership_unverified")
        hashes = set()
        for row in rows:
            body = dict(row.redacted_metadata)
            digest = body.pop("evidence_hash", None)
            body.pop("operation", None)
            if (
                row.actor_type != ActorType.SYSTEM
                or row.actor != "experiment_executor"
                or (
                    row.result != AuditResult.SUCCESS
                    or utc(row.created_at) > now
                    or utc(row.event_at) > utc(row.created_at)
                    or semantic_hash(body) != digest
                    or row.payload_hash != digest
                )
            ):
                refuse("experiment_ownership_unverified")
            hashes.add(digest)
        if len(hashes) != 1:
            refuse("experiment_lineage_conflict")
        body = dict(rows[0].redacted_metadata)
        digest = str(body.pop("evidence_hash"))
        body.pop("operation")
        return rows[0], body, digest

    def _facts(
        self, uid: str, order_ids: set[str], now: datetime
    ) -> list[tuple[BloFinActivityFact, NativeActivityFact]]:
        rows = list(
            self.session.scalars(
                select(BloFinActivityFact)
                .where(
                    BloFinActivityFact.organization_id == self.tenant.organization_id,
                    BloFinActivityFact.environment == "demo",
                    BloFinActivityFact.account_uid == uid,
                    BloFinActivityFact.order_id.in_(order_ids),
                )
                .order_by(BloFinActivityFact.kind, BloFinActivityFact.native_id)
                .limit(2101)
            )
        )
        if len(rows) > 2100:
            refuse("native_lineage_budget_exceeded")
        result = []
        for row in rows:
            fact = NativeActivityFact.model_validate(row.payload)
            if (
                (row.kind, row.native_id, row.order_id, row.occurred_at_ms, row.content_hash)
                != (
                    fact.kind,
                    fact.native_id,
                    fact.order_id,
                    int(fact.occurred_at_ms),
                    fact_hash(fact),
                )
                or (fact.kind == "fill" and fact.trade_id != fact.native_id)
                or (
                    fact.kind == "order"
                    and (
                        fact.native_id != fact.order_id or fact.updated_at_ms != fact.occurred_at_ms
                    )
                )
                or event_time(fact.occurred_at_ms) > utc(row.first_observed_at)
                or utc(row.first_observed_at) > now
            ):
                refuse("native_fact_provenance_invalid")
            result.append((row, fact))
        return result

    def _coverage(self, uid: str, opened: datetime, completed: datetime, now: datetime) -> None:
        cursor = self.session.get(
            BloFinActivityCursor,
            (self.tenant.organization_id, "demo", uid, "fill"),
            populate_existing=True,
        )
        if (
            cursor is None
            or cursor.covered_begin_ms is None
            or cursor.covered_end_ms is None
            or (
                cursor.gap_detected
                or cursor.last_error_code
                or cursor.last_successful_sync is None
                or not cursor.window_complete
                or not 0 <= (now - utc(cursor.last_successful_sync)).total_seconds() <= 300
                or not event_time(cursor.covered_begin_ms)
                <= opened
                <= completed
                <= event_time(cursor.covered_end_ms)
                <= now
            )
        ):
            refuse("native_fill_coverage_incomplete_or_stale")
        # Completed orders are checked individually; a cursor sweep is not historical coverage.

    def _exclusive_lineage(self, uid: str, source_id: str, lineage: NativeExitLineage) -> None:
        samples = list(
            self.session.scalars(
                select(ExperimentSampleRow)
                .where(
                    ExperimentSampleRow.organization_id == self.tenant.organization_id,
                    ExperimentSampleRow.source == ExperimentSource.BLOFIN_DEMO.value,
                    ExperimentSampleRow.account_scope == uid,
                    ExperimentSampleRow.kind == "closed_trade",
                    ExperimentSampleRow.source_record_id != source_id,
                )
                .limit(2001)
            )
        )
        if len(samples) > 2000:
            refuse("native_attribution_budget_exceeded")
        fills = set(lineage.entry_fill_ids) | set(lineage.exit_fill_ids)
        for sample in samples:
            other = sample.proof
            other_orders = set(other.get("native_exit_order_ids", ())) | {
                other.get("native_exit_order_id")
            }
            other_fills = set(other.get("native_entry_fill_ids", ())) | set(
                other.get("native_exit_fill_ids", ())
            )
            if other.get("native_entry_order_id") == lineage.native_entry_order_id or (
                other_orders & set(lineage.native_exit_order_ids) or other_fills & fills
            ):
                refuse("native_lineage_already_attributed")

    def _read(
        self, version: ExperimentVersion, base: dict[str, Any], now: datetime
    ) -> NativeOutcome:
        row = self._version(version, now)
        identity = self.session.get(
            BloFinActivityAccount, (row.organization_id, "demo", base["native_uid"])
        )
        if identity is None or identity.identity_verified_at is None:
            refuse("experiment_account_unverified")
        base["identity_verified_at"] = utc(identity.identity_verified_at)
        if version.configuration.sample_target.kind != "closed_trade":
            refuse("native_closed_trade_required")
        try:
            command_id = UUID(base["source_record_id"])
        except ValueError:
            refuse("experiment_ownership_unverified")
        if str(command_id) != base["source_record_id"]:
            refuse("experiment_source_record_noncanonical")
        entry_audit, entry_body, entry_hash = self._attestation(
            command_id, "experiment_native_entry_binding", now
        )
        exit_audit, exit_body, exit_hash = self._attestation(
            command_id, "experiment_native_exit_lineage", now
        )
        binding, lineage = (
            NativeEntryBinding.model_validate(entry_body),
            NativeExitLineage.model_validate(exit_body),
        )
        strategy_id = base["strategy_version_id"]
        if (
            binding.organization_id,
            binding.user_id,
            binding.experiment_id,
            binding.version_id,
            binding.configuration_hash,
            binding.sample_group_id,
            binding.execution_account_id,
            binding.native_uid,
            binding.strategy_version_id,
            binding.strategy_content_hash,
            binding.variant_key,
            binding.ownership_command_id,
        ) != (
            row.organization_id,
            row.user_id,
            row.experiment_id,
            row.id,
            row.configuration_hash,
            row.sample_group_id,
            base["execution_account_id"],
            base["native_uid"],
            strategy_id,
            row.strategy_content_hashes.get(str(strategy_id)),
            base["variant_key"],
            command_id,
        ) or lineage.entry_binding_hash != entry_hash:
            refuse("experiment_attribution_mismatch")
        if (
            binding.pre_entry_position_flat is not True
            or lineage.position_lineage_verified is not True
        ):
            refuse("native_position_lineage_unverified")
        strategy = self.session.get(UserStrategyVersion, strategy_id, populate_existing=True)
        command = self.session.get(ExecutionCommand, command_id, populate_existing=True)
        plan = self.session.get(TradePlanRevision, command.revision_id) if command else None
        if (
            strategy is None
            or strategy.strategy_id != version.configuration.strategy_id
            or strategy.content_hash != binding.strategy_content_hash
            or strategy_version_content_hash(
                card=strategy.card,
                structured_rules=strategy.structured_rules,
                lesson_source_metadata=strategy.lesson_source_metadata,
                pattern_spec=strategy.pattern_spec,
            )
            != strategy.content_hash
            or command is None
            or plan is None
        ):
            refuse("experiment_command_lineage_invalid")
        semantic = TradePlanRevisionSemantic.model_validate(plan.semantic_payload)
        if (
            (
                command.organization_id,
                command.user_id,
                command.account_id,
                command.plan_content_hash,
                plan.organization_id,
                plan.user_id,
                plan.account_id,
                plan.strategy_version_id,
                plan.content_hash,
                plan.execution_venue,
                semantic.strategy_version_id,
                semantic.execution_venue,
                semantic.revision_id,
                semantic.plan_id,
                semantic.organization_id,
                semantic.user_id,
                semantic.account_id,
            )
            != (
                row.organization_id,
                row.user_id,
                row.execution_account_id,
                binding.plan_content_hash,
                row.organization_id,
                row.user_id,
                row.execution_account_id,
                strategy_id,
                binding.plan_content_hash,
                "BLOFIN_DEMO",
                strategy_id,
                "BLOFIN_DEMO",
                plan.id,
                command.plan_id,
                row.organization_id,
                row.user_id,
                row.execution_account_id,
            )
            or utc(command.created_at) > utc(entry_audit.created_at)
            or command.outcome != ExecutionCommandOutcome.ALLOW
            or plan.plan_authority == "manual_demo_test"
            or plan.execution_instrument != semantic.execution_instrument
            or semantic.timeframe not in version.configuration.timeframes
            or plan_symbol(self.session, plan) not in version.configuration.symbols
            or semantic_hash(semantic.model_dump()) != plan.content_hash
            or semantic.quantity_unit is not QuantityUnit.CONTRACTS
        ):
            refuse("experiment_command_lineage_invalid")
        base.update(
            instrument=semantic.execution_instrument,
            entry_binding_hash=entry_hash,
            exit_lineage_hash=exit_hash,
            native_exit_order_ids=lineage.native_exit_order_ids,
            monetary_methodology_version=lineage.monetary_methodology_version,
        )
        entry_id, exits = lineage.native_entry_order_id, set(lineage.native_exit_order_ids)
        if entry_id in exits or set(lineage.entry_fill_ids) & set(lineage.exit_fill_ids):
            refuse("native_lineage_conflict")
        facts = self._facts(binding.native_uid, {entry_id, *exits}, now)
        orders = {f.order_id: f for _, f in facts if f.kind == "order"}
        fills = {f.native_id: f for _, f in facts if f.kind == "fill"}
        if set(orders) != {entry_id, *exits} or set(fills) != set(lineage.entry_fill_ids) | set(
            lineage.exit_fill_ids
        ):
            refuse("native_lineage_incomplete")
        base["facts"] = tuple(
            NativeFactProvenance(
                kind=f.kind,
                native_id=f.native_id,
                content_hash=r.content_hash,
                event_time=event_time(f.occurred_at_ms),
                first_observed_at=utc(r.first_observed_at),
                instrument=f.instrument,
                quantity=exact(f.quantity),
                price=exact(f.price) if f.price is not None else None,
                fee=exact(f.fee) if f.fee is not None else None,
                fee_currency=f.fee_currency,
                realized_pnl=exact(f.realized_pnl) if f.realized_pnl is not None else None,
            )
            for r, f in facts
        )
        entry = orders[entry_id]
        effect = self.session.scalar(
            select(VenueSubmitEffect).where(VenueSubmitEffect.command_id == command_id)
        )
        receipt = self.session.get(ExecutionReceipt, effect.receipt_id) if effect else None
        if (
            effect is None
            or receipt is None
            or not entry.client_order_id
            or entry.client_order_id != effect.client_order_id
            or (receipt.command_id, receipt.organization_id, receipt.user_id, receipt.account_id)
            != (command_id, row.organization_id, row.user_id, row.execution_account_id)
        ):
            refuse("experiment_native_entry_link_invalid")
        if entry.created_at_ms is None:
            refuse("native_order_fill_reconciliation_failed")
        if entry.reduce_only != "false":
            refuse("native_opening_entry_invalid")
        opposite = "sell" if semantic.side.value == "BUY" else "buy"
        for order_id, order in orders.items():
            related = [f for f in fills.values() if f.order_id == order_id]
            is_entry = order_id == entry_id
            expected_ids = (
                set(lineage.entry_fill_ids)
                if is_entry
                else {i for i in lineage.exit_fill_ids if fills[i].order_id == order_id}
            )
            if (
                {f.native_id for f in related} != expected_ids
                or not related
                or (
                    order.instrument != semantic.execution_instrument
                    or order.position_side != "net"
                    or order.side != (semantic.side.value.lower() if is_entry else opposite)
                    or order.created_at_ms is None
                    or order.updated_at_ms is None
                    or order.state not in {"filled", "canceled", "partially_canceled"}
                    or (not is_entry and order.reduce_only != "true")
                    or exact(order.filled_quantity)
                    != sum((exact(f.quantity) for f in related), Decimal(0))
                    or not 0 < exact(order.filled_quantity) <= exact(order.quantity)
                    or (
                        order.state == "filled"
                        and exact(order.filled_quantity) != exact(order.quantity)
                    )
                    or (is_entry and exact(order.quantity) != semantic.quantity.value)
                    or any(
                        f.instrument != order.instrument
                        or f.side != order.side
                        or f.position_side != order.position_side
                        or exact(f.quantity) <= 0
                        or exact(f.price) <= 0
                        or not int(order.created_at_ms)
                        <= int(f.occurred_at_ms)
                        <= int(order.updated_at_ms)
                        for f in related
                    )
                )
            ):
                refuse("native_order_fill_reconciliation_failed")
        entries = [fills[i] for i in lineage.entry_fill_ids]
        closing = [fills[i] for i in lineage.exit_fill_ids]
        if any(f.realized_pnl is not None and exact(f.realized_pnl) != 0 for f in entries):
            refuse("native_opening_entry_invalid")
        quantity = sum((exact(f.quantity) for f in entries), Decimal(0))
        if quantity != sum((exact(f.quantity) for f in closing), Decimal(0)) or min(
            int(f.occurred_at_ms) for f in closing
        ) < max(int(f.occurred_at_ms) for f in entries):
            refuse("native_closure_incomplete")
        opened = event_time(min(int(f.occurred_at_ms) for f in entries))
        completed = event_time(max(int(f.occurred_at_ms) for f in closing))
        event = self.session.scalar(
            select(ExperimentEventRow)
            .where(
                ExperimentEventRow.version_id == row.id,
                ExperimentEventRow.organization_id == row.organization_id,
                ExperimentEventRow.user_id == row.user_id,
                ExperimentEventRow.occurred_at <= utc(entry_audit.created_at),
            )
            .order_by(ExperimentEventRow.revision.desc())
            .limit(1)
        )
        opened_event = self.session.scalar(
            select(ExperimentEventRow)
            .where(
                ExperimentEventRow.version_id == row.id,
                ExperimentEventRow.occurred_at <= opened,
            )
            .order_by(ExperimentEventRow.revision.desc())
            .limit(1)
        )
        if (
            opened_event is None
            or opened_event.state != "running"
            or not semantic.valid_from <= event_time(entry.created_at_ms) < semantic.valid_until
            or row.started_at is None
            or row.authorized_until is None
            or event is None
            or event.state != "running"
            or event.configuration_hash != row.configuration_hash
            or not (
                utc(row.started_at)
                <= utc(entry_audit.created_at)
                <= event_time(entry.created_at_ms)
                <= opened
                < utc(row.authorized_until)
                and completed <= utc(exit_audit.created_at) <= now
            )
        ):
            refuse("experiment_sample_window")
        self._coverage(binding.native_uid, opened, completed, now)
        provenance = base["facts"]
        self._exclusive_lineage(binding.native_uid, str(command_id), lineage)
        proof = NativeExperimentSourceProof(
            organization_id=row.organization_id,
            execution_account_id=row.execution_account_id,
            native_uid=binding.native_uid,
            source=ExperimentSource.BLOFIN_DEMO,
            version_id=row.id,
            configuration_hash=row.configuration_hash,
            sample_group_id=row.sample_group_id,
            strategy_version_id=strategy_id,
            variant_key=binding.variant_key,
            kind="closed_trade",
            source_record_id=str(command_id),
            authority_origin="experiment",
            ownership_command_id=command_id,
            native_entry_order_id=entry_id,
            native_exit_order_id=sorted(exits)[0],
            native_exit_order_ids=lineage.native_exit_order_ids,
            native_entry_fill_ids=lineage.entry_fill_ids,
            native_exit_fill_ids=lineage.exit_fill_ids,
            opened_at=opened,
            completed_at=completed,
            source_content_hash=semantic_hash(
                {
                    "entry": entry_hash,
                    "exit": exit_hash,
                    "facts": [(f.kind, f.native_id, r.content_hash) for r, f in facts],
                }
            ),
        )
        currency = semantic.instrument_rules.settlement_currency
        base.update(
            proof=proof,
            facts=provenance,
            quantity=quantity,
            currency=currency,
            coverage="reconciled_lineage_only",
            freshness="fresh",
        )
        if any(f.realized_pnl is None for f in closing):
            refuse("native_profit_missing")
        if any(f.fee is None or f.fee_currency != currency for f in [*entries, *closing]):
            refuse("native_fee_or_currency_missing")
        if (
            not lineage.pnl_excludes_fees
            or not lineage.fee_convention
            or not lineage.monetary_methodology_version
        ):
            refuse("native_monetary_semantics_unverified")
        gross = sum((exact(f.realized_pnl) for f in closing), Decimal(0))
        native_fees = sum((exact(f.fee) for f in [*entries, *closing]), Decimal(0))
        fees = native_fees if lineage.fee_convention == "positive_cost" else -native_fees
        identity = self.session.get(
            BloFinActivityAccount, (row.organization_id, "demo", binding.native_uid)
        )
        if identity is None or identity.identity_verified_at is None:
            refuse("experiment_account_unverified")
        base["identity_verified_at"] = utc(identity.identity_verified_at)
        return NativeOutcome(
            **base,
            status="available",
            reported_realized_pnl=rounded(gross),
            fee_cost=rounded(fees),
            realized_pnl_after_fees_excluding_funding=rounded(gross - fees),
        )
