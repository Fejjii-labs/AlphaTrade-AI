"""Read one manual demo identity from stored native facts, without provider IO."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ApprovalAuthorization,
    AuditLog,
    ExecutionCommand,
    ExecutionFillFact,
    ExecutionReceipt,
    JournalTrade,
    TradePlanRevision,
    VenueSubmitEffect,
)
from app.interactive_agent.actions import RecordedTradeInput
from app.interactive_agent.presentation import readable_number
from app.interactive_agent.recorded_trade import RecordedTradeRead, _Evidence, _require
from app.schemas.common import AuditEventType, JournalTradeSource
from app.schemas.trade_plan import AuthorizationState, TradePlanRevisionSemantic
from app.services.canonical_serialization import canonical_sha256
from app.services.manual_demo_plan import MANUAL_DEMO_ORIGIN
from app.services.mappers.trade_plan_mapper import trade_plan_revision_to_schema


def select_manual_demo(
    session: Session, inputs: RecordedTradeInput, *, organization_id: UUID, user_id: UUID
) -> RecordedTradeRead:
    from app.interactive_agent.contracts import ArtifactKind, ConnectionRef, ProvenanceSource
    from app.schemas.common import MembershipRole
    from app.schemas.manual_demo import ManualDemoHistoryFilter
    from app.schemas.trade_plan import EntrySide
    from app.security.tenant import TenantContext
    from app.services.manual_demo_history import ManualDemoHistoryService, command_query

    if (
        inputs.paper_only
        or inputs.execution_venue not in {None, "BLOFIN_DEMO"}
        or inputs.trade_origin not in {None, "manual_demo_test"}
    ):
        return _missing(journal=inputs.journal_trade_id is not None)
    if inputs.journal_trade_id:
        journal = session.scalar(
            select(JournalTrade).where(
                JournalTrade.id == inputs.journal_trade_id,
                JournalTrade.organization_id == organization_id,
                JournalTrade.user_id == user_id,
                JournalTrade.source == JournalTradeSource.MANUAL_DEMO_TEST,
                JournalTrade.exchange == "BLOFIN_DEMO",
            )
        )
        if journal is None or (
            inputs.command_id is not None and inputs.command_id != journal.execution_lifecycle_id
        ):
            return _missing(journal=True)
        inputs = inputs.model_copy(update={"command_id": journal.execution_lifecycle_id})
    tenant = TenantContext(user_id, organization_id, "recorded-read", MembershipRole.OWNER)
    symbol = inputs.symbol or (inputs.market_name + "USDT" if inputs.market_name else None)
    filters = ManualDemoHistoryFilter(
        command_id=inputs.command_id,
        account_id=inputs.account_id,
        symbol=symbol,
        side=EntrySide.BUY
        if inputs.direction and inputs.direction.value == "long"
        else EntrySide.SELL
        if inputs.direction
        else None,
        since=inputs.since,
        until=inputs.until,
        requested_quantity=inputs.requested_quantity,
        submission_status=inputs.submission_status,
        limit=6,
    )
    page = ManualDemoHistoryService(session).list(tenant, filters)
    if not page.items:
        return _missing(journal=inputs.journal_trade_id is not None)
    accounts = set(
        session.scalars(
            command_query(tenant, filters)
            .with_only_columns(ExecutionCommand.account_id)
            .distinct()
            .limit(2)
        )
    )
    ambiguous = len(page.items) > 1 and (
        not inputs.latest
        or len(accounts) > 1
        or (page.items[0].submitted_at or page.items[0].attempted_at)
        == (page.items[1].submitted_at or page.items[1].attempted_at)
    )
    if ambiguous:
        text = (
            "Multiple manual BloFin demo orders match. Select an exact command "
            "from these recorded attempts:\n"
        )
        choices = page.items[:5]
        for item in choices:
            text += (
                f"{item.submitted_at or item.attempted_at} | "
                f"{item.requested_contracts} contracts | "
                f"{item.evidence.execution_status} | account {item.account_id} | "
                f"command {item.command_id} | {item.detail_url}\n"
            )
        if page.total > 5:
            text += (
                f"{page.total} total matches; refine time, quantity, account or submission status."
            )
        return RecordedTradeRead(
            text,
            text,
            [
                ConnectionRef(
                    artifact_kind=ArtifactKind.TRADE_DECISION,
                    record_id=str(item.command_id),
                    title=(
                        f"{item.submitted_at or item.attempted_at} | {item.requested_contracts} "
                        f"contracts | {item.evidence.execution_status}"
                    ),
                    relation="manual demo choice",
                    provenance=ProvenanceSource.SYSTEM_GENERATED,
                )
                for item in choices
            ],
            allow_model=False,
        )
    selected = page.items[0]
    command = session.get(ExecutionCommand, selected.command_id)
    assert command is not None
    trade = session.scalar(
        select(JournalTrade).where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.user_id == user_id,
            JournalTrade.account_id == command.account_id,
            JournalTrade.execution_lifecycle_id == command.id,
            JournalTrade.source == JournalTradeSource.MANUAL_DEMO_TEST,
            JournalTrade.exchange == "BLOFIN_DEMO",
        )
    )
    return _read(session, command, trade)


def _missing(*, journal: bool = False) -> RecordedTradeRead:
    text = (
        "No recorded Journal trade matches your selection in your authenticated scope. "
        if journal
        else ""
    ) + (
        "No matching recorded manual BloFin demo order or fill is available in your authenticated "
        "account scope. Reconcile the existing command to obtain native evidence. "
        "No internal simulator or different-origin trade is substituted."
    )
    return RecordedTradeRead(text, text, allow_model=False)


def read_manual_journal(session: Session, trade: JournalTrade) -> RecordedTradeRead:
    command = session.scalar(
        select(ExecutionCommand).where(
            ExecutionCommand.id == trade.execution_lifecycle_id,
            ExecutionCommand.organization_id == trade.organization_id,
            ExecutionCommand.user_id == trade.user_id,
            ExecutionCommand.account_id == trade.account_id,
        )
    )
    if command is None:
        return _missing()
    _require(trade.exchange == "BLOFIN_DEMO", "manual Journal venue")
    return _read(session, command, trade)


def _read(
    session: Session, command: ExecutionCommand, trade: JournalTrade | None
) -> RecordedTradeRead:
    row = session.scalar(
        select(TradePlanRevision).where(
            TradePlanRevision.id == command.revision_id,
            TradePlanRevision.organization_id == command.organization_id,
            TradePlanRevision.user_id == command.user_id,
            TradePlanRevision.account_id == command.account_id,
            TradePlanRevision.plan_authority == MANUAL_DEMO_ORIGIN,
            TradePlanRevision.execution_venue == "BLOFIN_DEMO",
        )
    )
    _require(row is not None, "manual command/plan")
    assert row is not None
    plan = trade_plan_revision_to_schema(row)
    semantic = TradePlanRevisionSemantic.model_validate(row.semantic_payload)
    _require(
        plan.schema_version == "ManualDemoTradePlanV1"
        and canonical_sha256(semantic) == command.plan_content_hash == plan.content_hash
        and command.plan_id == plan.plan_id,
        "manual immutable plan",
    )
    evidence = _Evidence()
    if trade:
        _require(
            trade.trade_plan_revision_id in {None, row.id}
            and trade.symbol.replace("-", "") == plan.execution_instrument.replace("-", "")
            and trade.direction.value == ("long" if plan.side.value == "BUY" else "short"),
            "manual Journal/plan",
        )
        evidence.add(
            trade.id,
            "Journal",
            f"source manual_demo_test; exchange BLOFIN_DEMO; account {trade.account_id}; "
            f"execution lifecycle {command.id}; projected size {trade.size} BTC.",
            journal=True,
        )
    else:
        evidence.missing.append(
            "Journal fill projection; a submitted order is not a filled position"
        )
    evidence.add(
        command.id,
        "Manual demo execution command",
        f"origin manual_demo_test; outcome {command.outcome.value}; "
        f"account {command.account_id}; revision {row.id}; hash {plan.content_hash}.",
    )
    evidence.refs[-1] = evidence.refs[-1].model_copy(update={"relation": "manual demo command"})
    evidence.add(
        row.id,
        "Manual demo plan",
        f"planned venue BLOFIN_DEMO; planned entry {plan.entry_zone.lower} to "
        f"{plan.entry_zone.upper}; "
        f"quantity {plan.quantity.value} contracts; multiplier "
        f"{plan.instrument_rules.contract_multiplier} BTC per contract; "
        f"planned stop {plan.risk_and_exits.stop.value}; planned target "
        f"{plan.risk_and_exits.targets[0].price.value}.",
    )
    authorization = session.scalar(
        select(ApprovalAuthorization).where(
            ApprovalAuthorization.id == command.authorization_id,
            ApprovalAuthorization.organization_id == command.organization_id,
            ApprovalAuthorization.user_id == command.user_id,
            ApprovalAuthorization.account_id == command.account_id,
        )
    )
    if authorization:
        _require(
            authorization.revision_id == row.id
            and authorization.plan_content_hash == plan.content_hash
            and authorization.execution_venue == "BLOFIN_DEMO"
            and authorization.execution_instrument == plan.execution_instrument
            and (
                authorization.state != AuthorizationState.CONSUMED
                or authorization.consumed_by_execution_command_id == command.id
            ),
            "manual authorization",
        )
        evidence.add(
            authorization.id,
            "Authorization",
            f"state {authorization.state.value}; exact manual plan; channel "
            f"{authorization.channel.value}.",
        )
        evidence.allowed.append(
            "The owner confirmed this exact manual demo plan. Strategy qualification and minimum "
            "1R do not apply to this connectivity test."
        )
    else:
        evidence.missing.append("stored exact manual plan authorization")
    effect = session.scalar(
        select(VenueSubmitEffect).where(VenueSubmitEffect.command_id == command.id)
    )
    receipt = session.scalar(
        select(ExecutionReceipt).where(
            ExecutionReceipt.command_id == command.id,
            ExecutionReceipt.organization_id == command.organization_id,
            ExecutionReceipt.user_id == command.user_id,
            ExecutionReceipt.account_id == command.account_id,
        )
    )
    fills = list(
        session.scalars(
            select(ExecutionFillFact)
            .where(
                ExecutionFillFact.command_id == command.id,
                ExecutionFillFact.organization_id == command.organization_id,
            )
            .order_by(ExecutionFillFact.occurred_at, ExecutionFillFact.id)
            .limit(101)
        )
    )
    _require(len(fills) <= 100, "manual bounded fill evidence")
    if receipt:
        _require(
            receipt.authorization_id == command.authorization_id
            and effect is not None
            and effect.receipt_id == receipt.id,
            "manual receipt/command",
        )
    _require(
        all(
            receipt is not None
            and fill.receipt_id == receipt.id
            and fill.venue_source == "blofin_demo"
            and fill.unit == "CONTRACTS"
            for fill in fills
        ),
        "manual fill venue/receipt",
    )
    from app.services.manual_demo_history import current_native_receipt

    native = current_native_receipt(session, command)
    protection = "unverified"
    order_id = None
    if native:
        facts = dict(native.redacted_metadata)
        native_hash = facts.pop("receipt_hash", None)
        facts.pop("operation", None)
        _require(
            canonical_sha256(facts) == native_hash
            and facts.get("origin") == MANUAL_DEMO_ORIGIN
            and facts.get("plan_content_hash") == plan.content_hash
            and effect is not None
            and facts.get("client_order_id") == effect.client_order_id
            and facts.get("instrument") == plan.execution_instrument,
            "manual native receipt",
        )
        order_id = facts.get("venue_order_id")
        _require(
            bool(order_id)
            and set(facts.get("fill_identities", [])) == {f.source_fill_identity for f in fills}
            and all(f.source_fill_identity.startswith(f"{order_id}:") for f in fills),
            "manual native order/fills",
        )
        assert effect is not None
        protection = str(facts.get("protection_status", "unverified"))
        evidence.add(
            native.id,
            "Native demo receipt",
            f"order {order_id}; client {effect.client_order_id}; native state "
            f"{facts.get('native_state')}; "
            f"recorded protection {protection}; protection identities "
            f"{facts.get('protection_order_ids', [])}; observed {native.created_at}.",
        )
    elif fills:
        evidence.missing.append("matching native order/protection receipt")
    for fill in fills:
        evidence.add(
            fill.id,
            "BloFin demo fill",
            f"identity {fill.source_fill_identity}; {fill.quantity} contracts at {fill.price}; "
            f"occurred {fill.occurred_at}; venue_source blofin_demo.",
        )
    latest_failure = session.scalar(
        select(AuditLog)
        .where(
            AuditLog.organization_id == command.organization_id,
            AuditLog.user_id == command.user_id,
            AuditLog.resource_type == "manual_demo_test",
            AuditLog.resource_id == str(command.id),
            AuditLog.action == AuditEventType.TOOL_FAILED,
            AuditLog.redacted_metadata["operation"].as_string()
            == "manual_demo_reconciliation_failed",
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    if latest_failure and (native is None or latest_failure.created_at >= native.created_at):
        protection = "latest read unavailable"
        evidence.missing.append(
            "latest native reconciliation; previously recorded facts do not prove current "
            "protection"
        )
    if protection != "verified":
        evidence.missing.append("verified stop and target protection for this native order")
    total = sum((fill.quantity for fill in fills), Decimal("0"))
    average = (
        sum((fill.quantity * fill.price for fill in fills), Decimal("0")) / total if total else None
    )
    if not fills:
        evidence.missing.append(
            "actual BloFin demo fills; the user's position report is not recorded fill proof"
        )
    from app.schemas.common import MembershipRole
    from app.security.tenant import TenantContext
    from app.services.manual_demo_history import ManualDemoHistoryService

    durable = ManualDemoHistoryService(session).get(
        TenantContext(
            command.user_id, command.organization_id, "recorded-read", MembershipRole.OWNER
        ),
        command.id,
    )
    lifecycle = durable.evidence
    closed = (
        lifecycle.execution_status == "closed" or lifecycle.position_status == "closed_verified"
    )
    entry = (
        f"Entry filled: {readable_number(total)} contracts "
        f"({readable_number(total * plan.instrument_rules.contract_multiplier)} BTC) "
        f"at {_money(average)} USDT."
        if total
        else "No entry fill is verified."
    )
    if closed:
        entry += f" Closure verified; exit at {_money(lifecycle.exit_price)} USDT."
        if lifecycle.venue_reported_fill_pnl is not None:
            entry += (
                f" Reported closing PnL: {_money(lifecycle.venue_reported_fill_pnl)} "
                "(fee/funding basis unverified)."
            )
    elif lifecycle.exit_quantity:
        entry += f" Verified exits: {readable_number(lifecycle.exit_quantity)} contracts."
    if lifecycle.entry_fees is not None:
        entry += f" Entry fee: {_money(lifecycle.entry_fees)} USDT."
    if closed and lifecycle.funding is None:
        issue = "Gross PnL, funding and net PnL remain unverified."
    elif closed:
        issue = ""
    elif lifecycle.position_status == "flat_exit_unverified":
        issue = "The account was flat at the snapshot; this trade's exit remains unverified."
    elif lifecycle.reconciliation_freshness == "latest_read_failed":
        issue = "The latest venue read failed; recorded fills do not establish a current position."
    elif (
        lifecycle.position_status == "account_position_present"
        and lifecycle.protection != "verified"
    ):
        issue = "Open exposure was observed; active protection remains unverified."
    elif total:
        issue = "This historical entry fill does not establish a currently open position."
    else:
        issue = "Submission and current position state remain separate from verified fills."
    action = (
        "Open this attempt's Journal detail."
        if trade is not None
        else "Open this exact attempt to refresh native evidence."
    )
    reply = "\n\n".join(
        part
        for part in (
            f"{durable.symbol} {'long' if plan.side.value == 'BUY' else 'short'} · "
            "BloFin demo · manual test.",
            entry,
            issue,
            action,
        )
        if part
    )
    details = "\n".join(evidence.lines)
    details += (
        f"\nAttempt time: {durable.attempted_at}. "
        f"Historical protection: {lifecycle.historical_protection}; "
        f"active protection: {lifecycle.protection}; triggered protection: "
        f"{lifecycle.triggered_protection}. "
        f"Current account: {lifecycle.account_status}; lifecycle: {lifecycle.position_status}. "
        f"Entry fees: {lifecycle.entry_fees}; exit fees: {lifecycle.exit_fees}; "
        f"native fillPnl: {lifecycle.venue_reported_fill_pnl}; "
        f"gross PnL: {lifecycle.gross_pnl}; funding: {lifecycle.funding}; "
        f"net PnL: {lifecycle.net_pnl}; fee convention: {lifecycle.fee_convention}. "
        f"Recovery: {lifecycle.recovery_status}; {lifecycle.recovery_reason}. "
        "Manual connectivity tests are excluded from strategy performance. "
        "These historical facts authorize no new order."
    )
    gaps = list(dict.fromkeys([*evidence.missing, *lifecycle.missing_evidence]))
    if gaps:
        details += "\nMissing evidence: " + "; ".join(gaps)
    return RecordedTradeRead(reply, details, evidence.refs, allow_model=False)


def _money(value: Decimal | None) -> str:
    if value is None:
        return "unverified"
    if value and abs(value) < Decimal("1"):
        # Preserve a meaningful nonzero native fee/PnL even below eight decimals.
        return readable_number(value, places=max(8, min(20, 2 - value.adjusted())))
    return format(value, ",.2f")
