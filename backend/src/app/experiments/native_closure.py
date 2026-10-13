"""Verify owned flat→entry→protective exit→flat receipts using stored evidence.

This boundary has no exchange transport or HTTP writer. A manual close can release
account exposure but cannot become an experiment-owned protective outcome.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import AwareDatetime
from sqlalchemy import select

from app.db.blofin_activity import BloFinActivityFact
from app.db.models import ExecutionFillFact, JournalLifecycleEvent
from app.experiments.blofin_source import event_time, exact, refuse, rounded
from app.experiments.identity import utc
from app.experiments.native_lineage import NativeEntryBinding, NativeExitLineage
from app.market_contracts.models import CanonicalDecimal, CanonicalModel
from app.schemas.common import JournalLifecycleEventType
from app.services.canonical_execution_journal import CANONICAL_EXECUTION_SOURCE_SYSTEM
from app.services.canonical_serialization import canonical_sha256

if TYPE_CHECKING:
    from app.db.models import ExecutionCommand, GovernedDemoLifecycleResolution
    from app.experiments.native_producer import BloFinNativeOutcomeProducer, NativePlanOrigin

EXIT = "experiment_native_exit_lineage"
CLOSURE = "experiment_native_verified_closure"


class NativeClosureReceipt(CanonicalModel):
    contract_version: Literal["experiment-native-closure/v1"] = "experiment-native-closure/v1"
    command_id: UUID
    origin_hash: str
    position_hash: str
    resolution_id: UUID
    resolution_hash: str
    entry_binding_hash: str
    lineage: NativeExitLineage
    fact_hashes: tuple[tuple[str, str, str], ...]
    closed_at: AwareDatetime
    price_pnl_excluding_fees_and_funding: CanonicalDecimal
    calculation_method: Literal["verified-linear-fill-prices/v1"] = "verified-linear-fill-prices/v1"


def verify_closure(
    producer: BloFinNativeOutcomeProducer,
    command: ExecutionCommand,
    origin: NativePlanOrigin,
    resolution: GovernedDemoLifecycleResolution,
) -> NativeClosureReceipt:
    session, resolver = producer.session, producer.resolver
    origin_row, _ = producer._origin(command)
    position = producer._position(command, origin_row)
    position_row = producer._audit(command.id, "experiment_native_pre_entry_position")
    binding_row = producer._audit(command.id, "experiment_native_entry_binding")
    if position_row is None or binding_row is None:
        refuse("experiment_entry_binding_invalid")
    binding = NativeEntryBinding.model_validate(producer._body(binding_row))
    if binding != producer._binding(origin, command):
        refuse("experiment_entry_binding_invalid")
    payload = resolution.evidence_payload
    if (
        canonical_sha256(payload) != resolution.content_hash
        or (
            resolution.organization_id,
            resolution.user_id,
            resolution.account_id,
            resolution.command_id,
            resolution.revision_id,
        )
        != (
            command.organization_id,
            command.user_id,
            command.account_id,
            command.id,
            command.revision_id,
        )
        or payload.get("command_id") != str(command.id)
        or payload.get("plan_content_hash") != command.plan_content_hash
        or payload.get("native_account_uid") != origin.native_uid
        or payload.get("positions") != "flat_account_wide"
        or payload.get("pending_orders") != "none_account_wide"
    ):
        refuse("native_lifecycle_evidence_invalid")
    close = session.get(JournalLifecycleEvent, resolution.journal_close_event_id)
    close_evidence = close.payload.get("demo_exit_evidence") if close is not None else None
    if (
        close is None
        or close.execution_lifecycle_id != command.id
        or (close.organization_id, close.user_id, close.account_id)
        != (command.organization_id, command.user_id, command.account_id)
        or close.event_type != JournalLifecycleEventType.CLOSE
        or close.payload.get("demo_exit_content_hash") != resolution.content_hash
        or not isinstance(close_evidence, dict)
        or canonical_sha256(close_evidence) != resolution.content_hash
    ):
        refuse("native_lifecycle_evidence_invalid")
    exits = payload.get("exit_fills")
    if not isinstance(exits, list) or not 0 < len(exits) <= 100:
        refuse("native_closure_incomplete")
    if any(not isinstance(f, dict) or f.get("category") not in {"tp", "sl"} for f in exits):
        refuse("native_exit_parent_unverified")
    exit_orders = {f["order_id"] for f in exits}
    parents = payload.get("exit_order_protection_ids")
    protections = set(payload.get("protection_ids", ()))
    recorded = set(payload.get("recorded_entry_protection_ids", ()))
    if payload.get("native_entry_tpsl_id"):
        recorded.add(payload["native_entry_tpsl_id"])
    if (
        not isinstance(parents, list)
        or len(parents) != len(exit_orders)
        or {p.get("order_id") for p in parents if isinstance(p, dict)} != exit_orders
        or len(protections) != 1
        or not protections <= recorded
        or any(not isinstance(p, dict) or p.get("tpsl_id") not in protections for p in parents)
    ):
        refuse("native_exit_parent_unverified")
    entries = list(
        session.scalars(
            select(ExecutionFillFact)
            .where(
                ExecutionFillFact.command_id == command.id,
                ExecutionFillFact.organization_id == command.organization_id,
                ExecutionFillFact.venue_source == "blofin_demo",
            )
            .limit(1001)
        )
    )
    entry_id = payload["entry_order_id"]
    if (
        not entries
        or len(entries) > 1000
        or sorted(f.source_fill_identity for f in entries)
        != sorted(payload["entry_fill_identities"])
        or any(not f.source_fill_identity.startswith(entry_id + ":") for f in entries)
    ):
        refuse("native_lineage_incomplete")
    entry_ids = tuple(f.source_fill_identity[len(entry_id) + 1 :] for f in entries)
    exit_ids = tuple(f["trade_id"] for f in exits)
    lineage = NativeExitLineage(
        entry_binding_hash=binding_row.payload_hash,
        native_entry_order_id=entry_id,
        native_exit_order_ids=tuple(exit_orders),
        entry_fill_ids=entry_ids,
        exit_fill_ids=exit_ids,
        position_lineage_verified=True,
    )
    facts = resolver._facts(origin.native_uid, {entry_id, *exit_orders}, producer._now())
    native = {f.native_id: f for _, f in facts if f.kind == "fill"}
    if set(native) != set(entry_ids) | set(exit_ids):
        refuse("native_lineage_incomplete")
    fee_events = list(
        session.scalars(
            select(JournalLifecycleEvent)
            .where(
                JournalLifecycleEvent.organization_id == command.organization_id,
                JournalLifecycleEvent.user_id == command.user_id,
                JournalLifecycleEvent.account_id == command.account_id,
                JournalLifecycleEvent.execution_lifecycle_id == command.id,
                JournalLifecycleEvent.event_type == JournalLifecycleEventType.FILL,
                JournalLifecycleEvent.source_system == CANONICAL_EXECUTION_SOURCE_SYSTEM,
                JournalLifecycleEvent.source_aggregate == "execution-command",
            )
            .limit(1001)
        )
    )
    by_fill = {event.source_event_id: event for event in fee_events}
    if len(fee_events) != len(entries) or set(by_fill) != {str(f.id) for f in entries}:
        refuse("native_entry_fill_receipt_mismatch")
    for fill in entries:
        fact = native[fill.source_fill_identity[len(entry_id) + 1 :]]
        event = by_fill[str(fill.id)]
        if (
            fact.order_id != entry_id
            or exact(fact.quantity) != fill.quantity
            or exact(fact.price) != fill.price
            or event_time(fact.occurred_at_ms) != utc(fill.occurred_at)
            or fact.fee_currency != event.payload.get("venue_fill_fee_currency")
            or (exact(fact.fee) if fact.fee is not None else None)
            != (
                exact(event.payload["venue_fill_fee"])
                if event.payload.get("venue_fill_fee") is not None
                else None
            )
        ):
            refuse("native_entry_fill_receipt_mismatch")
    for fill in exits:
        fact = native[fill["trade_id"]]
        if (
            fact.order_id != fill["order_id"]
            or exact(fact.quantity) != exact(fill["quantity"])
            or exact(fact.price) != exact(fill["price"])
            or event_time(fact.occurred_at_ms).isoformat() != fill["occurred_at"]
            or fact.fee_currency != fill.get("fee_currency")
            or (exact(fact.fee) if fact.fee is not None else None)
            != (exact(fill["fee"]) if fill.get("fee") is not None else None)
            or (exact(fact.realized_pnl) if fact.realized_pnl is not None else None)
            != (exact(fill["fillPnl"]) if fill.get("fillPnl") is not None else None)
        ):
            refuse("native_exit_fill_receipt_mismatch")
    opened = min(utc(f.occurred_at) for f in entries)
    closed = max(event_time(native[i].occurred_at_ms) for i in exit_ids)
    if (
        not position.observed_at
        <= opened
        <= closed
        <= utc(resolution.observed_at)
        <= producer._now()
    ):
        refuse("native_position_lineage_unverified")
    resolver._coverage(
        origin.native_uid, position.observed_at, utc(resolution.observed_at), producer._now()
    )
    # Account-wide interval, all instruments. Every fill must be one of the
    # owned legs, including activity between final entry and final flat read.
    interval = list(
        session.scalars(
            select(BloFinActivityFact)
            .where(
                BloFinActivityFact.organization_id == command.organization_id,
                BloFinActivityFact.environment == "demo",
                BloFinActivityFact.account_uid == origin.native_uid,
                BloFinActivityFact.kind == "fill",
                BloFinActivityFact.occurred_at_ms >= int(position.observed_at.timestamp() * 1000),
                BloFinActivityFact.occurred_at_ms
                <= int(utc(resolution.observed_at).timestamp() * 1000),
            )
            .limit(1001)
        )
    )
    if len(interval) > 1000 or {f.native_id for f in interval} != set(native):
        refuse("native_unrelated_account_activity")
    plan = producer._canonical_plan(
        producer._version(origin.experiment_id, origin.version_id),
        origin.variant_key,
        command.revision_id,
        require_current_validity=False,
    ).plan
    rules = plan.instrument_rules
    metadata = position.instrument_metadata
    if (
        rules.contract_type.value != "LINEAR"
        or metadata.get("contractType") != "linear"
        or metadata.get("contractValueCurrency") != rules.base_currency
        or metadata.get("settleCurrency") != rules.settlement_currency
        or exact(metadata.get("contractValue")) != rules.contract_multiplier
    ):
        refuse("native_contract_metadata_unverified")
    with localcontext() as context:
        context.prec = 80
        quantity = sum((f.quantity for f in entries), Decimal(0))
        if quantity != sum((exact(f["quantity"]) for f in exits), Decimal(0)):
            refuse("native_closure_incomplete")
        entry_value = sum((f.quantity * f.price for f in entries), Decimal(0))
        exit_value = sum((exact(f["quantity"]) * exact(f["price"]) for f in exits), Decimal(0))
        pnl = (exit_value - entry_value) * rules.contract_multiplier
        if plan.side.value == "SELL":
            pnl = -pnl
        pnl = rounded(pnl)
    return NativeClosureReceipt(
        command_id=command.id,
        origin_hash=origin_row.payload_hash,
        position_hash=position_row.payload_hash,
        resolution_id=resolution.id,
        resolution_hash=resolution.content_hash,
        entry_binding_hash=binding_row.payload_hash,
        lineage=lineage,
        fact_hashes=tuple((f.kind, f.native_id, r.content_hash) for r, f in facts),
        closed_at=closed,
        price_pnl_excluding_fees_and_funding=pnl,
    )
