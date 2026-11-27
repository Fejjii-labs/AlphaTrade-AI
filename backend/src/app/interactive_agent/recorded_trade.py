"""Bounded owner/account-scoped historical trade reads, never execution authority."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ValidationAppError
from app.db.canonical_candidates import CanonicalCandidateRow
from app.db.models import (
    ApprovalAuthorization,
    ConversationMessage,
    ExecutionAccount,
    ExecutionCommand,
    ExecutionFillFact,
    ExecutionReceipt,
    JournalTrade,
    RiskReservation,
    TradePlanRevision,
    UserStrategy,
    UserStrategyVersion,
)
from app.interactive_agent.actions import ActionRequest, RecordedTradeInput
from app.interactive_agent.contracts import ArtifactKind, ConnectionRef, ProvenanceSource
from app.interactive_agent.manual_demo_selection import manual_selectors
from app.interactive_agent.parsing import extract_direction, extract_symbol
from app.interactive_agent.presentation import readable_number, readable_percentage, readable_price
from app.interactive_agent.recorded_setup_evidence import read_setup_evidence
from app.persistence.eligibility_postgres import PostgresActionEligibilityStore
from app.persistence.trade_plan_postgres import PostgresCanonicalTradePlanStore
from app.schemas.agent_paper import AgentPaperResult
from app.schemas.canonical_trade_plan import CanonicalTradePlanRevision
from app.schemas.common import ConversationMessageRole, JournalTradeSource
from app.schemas.journal_trades import PlannedTarget
from app.schemas.trade_plan import AuthorizationState, TradePlanRevisionSemantic
from app.services import planned_reward_risk as reward_risk_policy
from app.services.canonical_execution_journal import journal_planned_targets
from app.services.canonical_serialization import canonical_sha256
from app.signal_fusion.types import hashed_model

_READ = re.compile(
    r"\b(?:latest|last|most recent|recorded|existing|executed|explain|describe)\b", re.I
)
_MUTATE = re.compile(r"\b(?:prepare|execute|submit|activate|approve|place|create|log|save)\b", re.I)
_UUID = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
_FILL_LIMIT = 10
_TARGET_DISPLAY_LIMIT = 10
_TARGET_COMPARE_LIMIT = 100
_HISTORICAL_POSITION = re.compile(
    r"\b(?:latest|last|most recent|recorded|executed)\s+"
    r"(?:([a-z0-9]{2,16})\s+)?(?:short|long)\b",
    re.I,
)
_CURRENT_SETUP = re.compile(
    r"\b(?:current(?!\s+(?:minimum|reward|risk|policy|rule)\b)|forming|developing)\b"
    r"[^.!?\n]{0,70}\b(?:setups?|signals?)\b|"
    r"\b(?:setups?|signals?)\s+(?:(?:is|are|that is|which is)\s+)?"
    r"(?:current|currently forming|forming|developing|right now)\b",
    re.I,
)


def is_current_setup_request(message: str) -> bool:
    return bool(_CURRENT_SETUP.search(message))


def trade_scope(message: str) -> dict[str, str | None]:
    demo = bool(re.search(r"\bdemo\b", message, re.I))
    blofin = bool(re.search(r"\bblo\s*fin\b", message, re.I))
    manual = bool(re.search(r"\bmanual\b", message, re.I))
    simulator = bool(re.search(r"\b(?:simulator|internal paper)\b", message, re.I))
    real = bool(re.search(r"\b(?:real|live)\b", message, re.I))
    return {
        "execution_venue": "BLOFIN_DEMO"
        if demo and (blofin or manual)
        else "BLOFIN_REAL"
        if blofin and real
        else "BLOFIN"
        if blofin
        else "PAPER_INTERNAL"
        if simulator
        else None,
        "trade_origin": "manual_demo_test" if manual and demo else "manual" if manual else None,
    }


def route_recorded_trade(message: str, *, symbol: str | None) -> ActionRequest | None:
    if is_current_setup_request(message):
        return None
    position = _HISTORICAL_POSITION.search(message)
    historical_position = position is not None and bool(
        re.search(r"\b(?:qualified|executed|filled|taken|traded)\b", message, re.I)
    )
    precise = manual_selectors(message)
    scoped_demo = trade_scope(message)["trade_origin"] == "manual_demo_test" or bool(
        precise.get("command_id")
    )
    has_record = bool(re.search(r"\btrades?\b", message, re.I)) or historical_position
    if scoped_demo and re.search(r"\b(?:orders?|positions?|attempts?|commands?)\b", message, re.I):
        has_record = True
    has_read = bool(_READ.search(message)) or (
        scoped_demo and bool(re.search(r"\b(?:what|why|how|show|summari[sz]e)\b", message, re.I))
    )
    if not has_record or not has_read:
        return None
    if _MUTATE.search(message):
        return None
    account = re.search(rf"\baccount\s*[=:]?\s*({_UUID})\b", message, re.I)
    trade = re.search(rf"\btrade\s*[=:]?\s*({_UUID})\b", message, re.I)
    named = re.search(
        r"\b(?:latest|last|recent|that|this|same)\s+([a-z0-9]{2,16})\s+"
        r"(?:(?:short|long|paper)\s+)*trade\b",
        message,
        re.I,
    )
    market_name = (
        named.group(1).upper()
        if named
        else position.group(1).upper()
        if historical_position and position and position.group(1)
        else None
    )
    if market_name in {"SHORT", "LONG", "PAPER", "TRADE"}:
        market_name = None
    if market_name in {"MANUAL", "DEMO", "BLOFIN", "SIMULATOR", "INTERNAL"}:
        market_name = None
    return ActionRequest(
        name="paper_trade.read_recorded",
        arguments={
            **trade_scope(message),
            **(precise if scoped_demo else {}),
            "symbol": extract_symbol(message) or (None if market_name else symbol),
            "market_name": market_name if extract_symbol(message) is None else None,
            "direction": extract_direction(message),
            "account_id": account.group(1) if account else None,
            "journal_trade_id": trade.group(1) if trade else None,
            "latest": bool(re.search(r"\b(?:latest|last|most recent)\b", message, re.I)),
            "paper_only": bool(re.search(r"\bpaper\b", message, re.I)),
        },
    )


@dataclass
class RecordedTradeRead:
    reply: str
    recorded_evidence: str
    connections: list[ConnectionRef] = field(default_factory=list)
    warnings: tuple[str, ...] = ()
    allow_model: bool = True


@dataclass
class _Evidence:
    lines: list[str] = field(default_factory=list)
    refs: list[ConnectionRef] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    allowed: list[str] = field(default_factory=list)
    required: list[str] = field(default_factory=list)

    def add(self, identity: UUID, title: str, detail: str, *, journal: bool = False) -> None:
        self.refs.append(
            ConnectionRef(
                artifact_kind=ArtifactKind.JOURNAL_ENTRY
                if journal
                else ArtifactKind.TRADE_DECISION,
                record_id=str(identity),
                title=title,
                relation="recorded trade lineage",
                provenance=ProvenanceSource.TRADE_OUTCOME
                if journal
                else ProvenanceSource.SYSTEM_GENERATED,
            )
        )
        self.lines.append(f"[{title}] {identity}: {detail}")


def read_recorded_trade(
    session: Session,
    inputs: RecordedTradeInput,
    *,
    organization_id: UUID,
    user_id: UUID,
) -> RecordedTradeRead:
    with session.no_autoflush:
        if inputs.unsupported_filters:
            reason = (
                "Unsupported selection filters: "
                + " ".join(inputs.unsupported_filters)
                + " No record was selected."
            )
            return RecordedTradeRead(reason, reason, allow_model=False)
        if (
            (
                inputs.since
                or inputs.until
                or inputs.requested_quantity is not None
                or inputs.submission_status != "attempt"
            )
            and inputs.trade_origin != "manual_demo_test"
            and inputs.command_id is None
        ):
            reason = (
                "Time, contract quantity and submission-status filters currently "
                "require manual BloFin demo scope. No filter was silently ignored."
            )
            return RecordedTradeRead(reason, reason, allow_model=False)
        if inputs.execution_venue == "BLOFIN":
            reason = (
                "BloFin venue selection is incomplete. Specify BloFin demo or real; no other "
                "trade is substituted."
            )
            return RecordedTradeRead(reason, reason)
        if inputs.command_id is not None or inputs.trade_origin == "manual_demo_test":
            from app.interactive_agent.manual_demo_evidence import select_manual_demo

            return select_manual_demo(
                session, inputs, organization_id=organization_id, user_id=user_id
            )
        selected = _select_trade(session, inputs, organization_id=organization_id, user_id=user_id)
        if isinstance(selected, RecordedTradeRead):
            return selected
        return _read_lineage(session, selected)


def _select_trade(
    session: Session,
    inputs: RecordedTradeInput,
    *,
    organization_id: UUID,
    user_id: UUID,
) -> JournalTrade | RecordedTradeRead:
    owned_account = (
        select(ExecutionAccount.id)
        .where(
            ExecutionAccount.id == JournalTrade.account_id,
            ExecutionAccount.organization_id == organization_id,
            ExecutionAccount.user_id == user_id,
        )
        .exists()
    )
    filters = [
        JournalTrade.organization_id == organization_id,
        JournalTrade.user_id == user_id,
        or_(JournalTrade.account_id.is_(None), owned_account),
    ]
    if inputs.account_id is not None:
        filters.append(JournalTrade.account_id == inputs.account_id)
    if inputs.journal_trade_id is not None:
        filters.append(JournalTrade.id == inputs.journal_trade_id)
    if inputs.symbol:
        filters.append(
            func.replace(func.replace(func.upper(JournalTrade.symbol), "-", ""), "/", "")
            == inputs.symbol.upper().replace("-", "").replace("/", "")
        )
    if inputs.market_name:
        filters.append(
            select(TradePlanRevision.id)
            .where(
                TradePlanRevision.id == JournalTrade.trade_plan_revision_id,
                TradePlanRevision.organization_id == organization_id,
                TradePlanRevision.user_id == user_id,
                TradePlanRevision.account_id == JournalTrade.account_id,
                TradePlanRevision.semantic_payload["instrument_rules"]["base_currency"].as_string()
                == inputs.market_name.upper(),
            )
            .exists()
        )
    if inputs.direction is not None:
        filters.append(JournalTrade.direction == inputs.direction)
    if inputs.paper_only:
        filters.append(JournalTrade.source == JournalTradeSource.PAPER_EXECUTION)
    if inputs.trade_origin:
        filters.append(JournalTrade.source == JournalTradeSource(inputs.trade_origin))
    if inputs.execution_venue:
        # Require the immutable plan venue too when linked; never infer venue from
        # a same-market trade, account execution_mode=PAPER, or narrative text.
        matching_plan = (
            select(TradePlanRevision.id)
            .where(
                TradePlanRevision.id == JournalTrade.trade_plan_revision_id,
                TradePlanRevision.organization_id == organization_id,
                TradePlanRevision.user_id == user_id,
                TradePlanRevision.account_id == JournalTrade.account_id,
                TradePlanRevision.execution_venue == inputs.execution_venue,
            )
            .exists()
        )
        legacy_manual = (
            (JournalTrade.source == JournalTradeSource.MANUAL_DEMO_TEST)
            & (JournalTrade.exchange == "BLOFIN_DEMO")
            & JournalTrade.trade_plan_revision_id.is_(None)
        )
        filters.append(
            matching_plan | (legacy_manual if inputs.execution_venue == "BLOFIN_DEMO" else False)
        )
    accounts = list(
        session.scalars(select(JournalTrade.account_id).where(*filters).distinct().limit(2))
    )
    if len(accounts) > 1:
        names = {
            row.id: row.name
            for row in session.scalars(
                select(ExecutionAccount).where(
                    ExecutionAccount.id.in_(
                        [identity for identity in accounts if identity is not None]
                    ),
                    ExecutionAccount.organization_id == organization_id,
                    ExecutionAccount.user_id == user_id,
                )
            )
        }
        options = [
            (
                names.get(identity, "Unnamed account")
                if identity is not None
                else "Unassigned Journal",
                identity,
            )
            for identity in accounts
        ]
        question = (
            "Matching trades exist in multiple accounts. Please select an account: "
            + ", ".join(name[:80] for name, _ in options)
            + ". Account references are in Stored evidence."
        )
        return RecordedTradeRead(
            question, "\n".join(f"Account {name}: {identity}" for name, identity in options)
        )
    time = func.coalesce(JournalTrade.entry_time, JournalTrade.created_at)
    trades = list(
        session.scalars(
            select(JournalTrade)
            .where(*filters)
            .order_by(time.desc(), JournalTrade.id.desc())
            .limit(2)
        )
    )
    if not trades:
        reason = (
            "No recorded Journal trade matches your selection in your authenticated scope. "
            f"Requested venue: {inputs.execution_venue or 'unspecified'}; "
            f"origin: {inputs.trade_origin or 'unspecified'}. "
            "Matching fill evidence is unavailable; no trade from another venue or origin is "
            "substituted."
        )
        return RecordedTradeRead(reason, reason)
    if len(trades) > 1 and (
        not inputs.latest
        or (trades[0].entry_time or trades[0].created_at)
        == (trades[1].entry_time or trades[1].created_at)
    ):
        question = (
            "Multiple matching trades remain. Please select a trade "
            "or ask for the latest matching trade. "
            "Trade references are in Stored evidence."
        )
        return RecordedTradeRead(
            question,
            "\n".join(
                f"Journal {trade.id}: {trade.symbol} {trade.direction.value}; "
                f"entry {trade.entry_time or trade.created_at}; account {trade.account_id}."
                for trade in trades
            ),
        )
    return trades[0]


def _journal_target_evidence(
    raw_targets: list[dict[str, object]], evidence: _Evidence
) -> tuple[str, list[PlannedTarget] | None]:
    """Bounded projection facts; normalization never writes or implies protection."""
    targets: list[PlannedTarget] = []
    details: list[str] = []
    complete = len(raw_targets) <= _TARGET_COMPARE_LIMIT
    for index, raw in enumerate(raw_targets[:_TARGET_COMPARE_LIMIT]):
        try:
            # Require the recorded allocation, not PlannedTarget's create-time default.
            for key in ("price", "size_fraction"):
                if key not in raw or isinstance(raw[key], bool) or len(str(raw[key])) > 64:
                    raise ValueError("Unreadable target terms")
            target = PlannedTarget.model_validate(raw)
            price = target.price
            if (
                not price.is_finite()
                or price <= 0
                or len(price.as_tuple().digits) > 24
                or abs(int(price.as_tuple().exponent)) > 18
            ):
                raise ValueError("Unbounded target price")
        except (ValidationError, ValueError, TypeError):
            complete = False
            if index < _TARGET_DISPLAY_LIMIT:
                details.append(f"target {index + 1}: unreadable price or allocation")
            continue
        targets.append(target)
        if index < _TARGET_DISPLAY_LIMIT:
            label = " ".join((target.label or f"target {index + 1}").split())[:40]
            details.append(
                f"{label}: price {price}, allocation {Decimal(str(target.size_fraction))}"
            )
    if len(raw_targets) > _TARGET_DISPLAY_LIMIT:
        details.append(
            f"{len(raw_targets) - _TARGET_DISPLAY_LIMIT} additional Journal targets not displayed"
        )
    if not complete:
        evidence.missing.append(
            "complete readable Journal planned targets within the 100-target comparison bound"
        )
    return "; ".join(details) or "none recorded", targets if complete else None


def _read_lineage(session: Session, trade: JournalTrade) -> RecordedTradeRead:
    if trade.source == JournalTradeSource.MANUAL_DEMO_TEST:
        from app.interactive_agent.manual_demo_evidence import read_manual_journal

        return read_manual_journal(session, trade)
    evidence = _Evidence()
    journal_targets, normalized_targets = _journal_target_evidence(trade.planned_targets, evidence)
    evidence.add(
        trade.id,
        "Journal",
        f"{trade.symbol} {trade.direction.value}; status {trade.status.value}; "
        f"account {trade.account_id}; projected entry {trade.entry_price}; "
        f"planned entry {trade.planned_entry_price}; planned stop {trade.planned_stop_price}; "
        f"planned target count {len(trade.planned_targets)}; "
        f"Journal planned targets in stored order: {journal_targets}. "
        "These are planned values, not verified protection or exit fills.",
        journal=True,
    )
    envelope = _plan(session, trade)
    if envelope is None:
        evidence.missing.append(
            "linked immutable canonical plan and its authorization/eligibility/risk/fill lineage"
        )
        summary = (
            f"{trade.symbol} {trade.direction.value} — Journal {trade.status.value}. "
            "Projected entry: "
            f"{trade.entry_price if trade.entry_price is not None else 'unavailable'}. "
            "Execution venue and authorization are unavailable; "
            "Journal values alone do not prove a fill."
        )
        evidence.lines.append(
            "Journal target comparison: unverified; linked immutable plan unavailable."
        )
        return _finish(summary, evidence)
    plan, lineage = envelope.plan, envelope.lineage
    _require(
        plan.account_id == trade.account_id
        and plan.revision_id == trade.trade_plan_revision_id
        and plan.strategy_version_id == trade.strategy_version_id
        and lineage.candidate_id == trade.candidate_id,
        "Journal/plan",
    )
    targets = (
        "; ".join(
            f"{target.price.value} ({target.quantity_fraction} allocation)"
            for target in plan.risk_and_exits.targets[:10]
        )
        or "unavailable"
    )
    if len(plan.risk_and_exits.targets) > 10:
        evidence.missing.append("additional planned targets beyond the first ten")
    evidence.add(
        plan.revision_id,
        "TradePlan",
        f"hash {plan.content_hash}; strategy version {plan.strategy_version_id}; "
        f"planned entry zone {plan.entry_zone.lower} to {plan.entry_zone.upper}; "
        f"execution reference price {plan.basis_policy.execution_price.value}; "
        f"planned stop {plan.risk_and_exits.stop.value}; targets in plan order: {targets}; "
        f"maximum loss {plan.risk_and_exits.maximum_loss.value}; "
        f"planned venue {plan.execution_venue}.",
    )
    strategy = session.scalar(
        select(UserStrategyVersion)
        .join(UserStrategy)
        .where(
            UserStrategyVersion.id == plan.strategy_version_id,
            UserStrategy.organization_id == trade.organization_id,
            UserStrategy.user_id == trade.user_id,
        )
    )
    strategy_name = "unavailable"
    if strategy:
        strategy_name = str(strategy.card.get("strategy_name") or "unnamed strategy")
        evidence.add(
            strategy.id,
            "Strategy version",
            f"{strategy_name}; version {strategy.version}; immutable recorded version, "
            "not the current selected version or a new approval.",
        )
    else:
        evidence.missing.append("stored strategy version")
    candidate = session.scalar(
        select(CanonicalCandidateRow).where(
            CanonicalCandidateRow.organization_id == trade.organization_id,
            CanonicalCandidateRow.candidate_id == lineage.candidate_id,
        )
    )
    if candidate:
        _require(
            candidate.strategy_version_id == plan.strategy_version_id
            and candidate.setup_definition_id == plan.setup_definition_id
            and candidate.evidence_window_hash == lineage.evidence_window_hash
            and candidate.assessment_id == lineage.assessment_id,
            "Candidate/plan",
        )
        evidence.add(
            candidate.candidate_id,
            "Candidate",
            f"bound assessment {lineage.assessment_id}; "
            f"plan-bound revision {lineage.candidate_revision}; "
            f"evidence window {lineage.evidence_window_hash}. "
            "Current Candidate state is not historical execution authorization.",
        )
    else:
        evidence.missing.append("linked Candidate")
    assessment_hash = _eligibility(session, trade, envelope, evidence)
    read_setup_evidence(
        session,
        envelope,
        add=evidence.add,
        missing=evidence.missing,
        assessment_content_hash=assessment_hash,
    )
    command = session.scalar(
        select(ExecutionCommand).where(
            ExecutionCommand.id == trade.execution_lifecycle_id,
            ExecutionCommand.organization_id == trade.organization_id,
            ExecutionCommand.user_id == trade.user_id,
            ExecutionCommand.account_id == trade.account_id,
        )
    )
    fills: list[ExecutionFillFact] = []
    if command:
        _require(
            command.revision_id == plan.revision_id
            and command.plan_id == plan.plan_id
            and command.plan_content_hash == plan.content_hash,
            "command/plan",
        )
        fills = _execution(session, trade, envelope, command, evidence)
    else:
        evidence.missing.extend(
            [
                "linked execution command",
                "authorization and execution receipts",
                "immutable fill evidence",
            ]
        )
    target_comparison = ""
    if not trade.planned_targets and plan.risk_and_exits.targets:
        evidence.lines.append(
            "Journal targets are empty; linked TradePlan contains targets. "
            "This is a projection discrepancy. "
            "Targets below come from the immutable plan; this read does not repair the Journal."
        )
        evidence.lines.append("Journal target comparison: missing; no match can be confirmed.")
    elif normalized_targets is None or len(plan.risk_and_exits.targets) > _TARGET_COMPARE_LIMIT:
        target_comparison = (
            "Journal target comparison is unverified because targets are unreadable "
            "or exceed the comparison bound."
        )
        evidence.lines.append(target_comparison)
        evidence.required.append(target_comparison)
        if (
            len(trade.planned_targets) <= _TARGET_COMPARE_LIMIT
            and len(plan.risk_and_exits.targets) <= _TARGET_COMPARE_LIMIT
            and trade.planned_targets != journal_planned_targets(plan)
        ):
            evidence.lines.append(
                "Journal targets differ from the expected plan projection representation; "
                "complete price/allocation agreement is unverified."
            )
    elif normalized_targets != [
        PlannedTarget.model_validate(target) for target in journal_planned_targets(plan)
    ]:
        evidence.lines.append(
            "Journal target comparison: mismatch in ordered prices, allocations or labels."
        )
        evidence.lines.append(
            "Journal targets differ from the immutable plan; "
            "use the plan values as planned targets, not filled exits."
        )
    else:
        target_comparison = (
            "Journal planned target prices, allocations and order match the linked immutable plan. "
            "This confirms planned values only, not verified protection or exit fills."
        )
        evidence.lines.append("Journal target comparison: match. " + target_comparison)
        evidence.required.append(target_comparison)
    fill_text = (
        "; ".join(
            f"{readable_number(fill.quantity)} {fill.unit.lower().replace('_', ' ')} at "
            f"{readable_price(fill.price, plan.instrument_rules.tick_size)}"
            for fill in fills
        )
        or "unavailable"
    )
    venue = _venue(plan.execution_venue, fills, evidence)
    prices = plan.instrument_rules
    display_targets = (
        "; ".join(
            f"{readable_price(target.price.value, prices.tick_size)} "
            f"({readable_percentage(target.quantity_fraction)} allocation)"
            for target in plan.risk_and_exits.targets[:10]
        )
        or "unavailable"
    )
    summary = (
        f"{prices.base_currency} {trade.direction.value} — {trade.status.value.replace('_', ' ')}. "
        f"Strategy: {strategy_name}. "
        f"Planned entry: {readable_price(plan.entry_zone.lower, prices.tick_size)} "
        f"to {readable_price(plan.entry_zone.upper, prices.tick_size)} {prices.quote_currency}; "
        f"recorded fill: {fill_text}. "
        f"Planned stop: {readable_price(plan.risk_and_exits.stop.value, prices.tick_size)}; "
        f"target(s): {display_targets} (immutable plan). "
        f"Execution venue: {venue}."
    )
    try:
        measured = reward_risk_policy.measure_planned_reward_risk(plan)
        minimum = str(reward_risk_policy.MINIMUM_REWARD_RISK)
        policy = (
            f"Current application entry policy requires gross allocation-weighted reward/risk "
            f"of at least {minimum}:1. This recorded plan is "
            f"{readable_number(measured.ratio, places=2)}R and "
            f"{'passes' if measured.meets_minimum else 'fails'} that minimum. "
            "This comparison does not change historical authorization "
            "or establish current eligibility."
        )
        evidence.refs.append(
            ConnectionRef(
                artifact_kind=ArtifactKind.RULE,
                record_id=reward_risk_policy.POLICY_VERSION,
                title="Application entry policy",
                relation="current deterministic entry authority",
                provenance=ProvenanceSource.SYSTEM_GENERATED,
            )
        )
        evidence.lines.insert(
            0,
            f"[Application entry policy] {reward_risk_policy.POLICY_VERSION}: "
            f"minimum_gross_allocation_weighted_R={minimum}; "
            "authority=app.services.planned_reward_risk, not Knowledge or strategy prose. "
            "Worst allowed entry-zone boundary; unpriced runners contribute zero reward; "
            "fees/funding/slippage remain in separate loss checks, not a net 1R promise.",
        )
        evidence.lines.append(
            f"Historical planned gross allocation-weighted reward/risk: {measured.ratio}; "
            "worst entry-zone boundary; unpriced runner reward zero; costs excluded. "
            "This current policy comparison does not change historical authorization."
        )
        summary += " " + policy
        evidence.required.append(policy)
    except reward_risk_policy.PlannedRewardRiskError:
        evidence.missing.append("planned reward/risk cannot be measured from these stored terms")
    if not trade.planned_targets and plan.risk_and_exits.targets:
        summary += (
            " Journal targets are empty; the target source is the linked plan, "
            "a projection discrepancy."
        )
    if target_comparison:
        summary += " " + target_comparison
    if trade.excursion_source == "trade_review" and trade.excursion_freshness_note:
        # The automatic post-trade review (app.services.trade_review), from BloFin candles.
        summary += f" Trade review: {trade.excursion_freshness_note}"
        evidence.lines.append(
            f"[Trade review] {trade.excursion_freshness_note} "
            f"(best price while open {trade.mfe_price}, worst {trade.mae_price}; "
            "BloFin candles; analysis, not a recommendation)."
        )
    return _finish(summary, evidence)


def _plan(session: Session, trade: JournalTrade) -> CanonicalTradePlanRevision | None:
    if trade.trade_plan_revision_id is None:
        return None
    store = PostgresCanonicalTradePlanStore(
        sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    )
    with store.bind_session(session):
        envelope = store.get_by_revision(
            organization_id=trade.organization_id,
            user_id=trade.user_id,
            revision_id=trade.trade_plan_revision_id,
        )
    if envelope:
        semantic = TradePlanRevisionSemantic.model_validate(
            envelope.plan.model_dump(
                mode="python", include=set(TradePlanRevisionSemantic.model_fields)
            )
        )
        _require(canonical_sha256(semantic) == envelope.plan.content_hash, "plan content hash")
    return envelope


def _eligibility(
    session: Session, trade: JournalTrade, envelope: CanonicalTradePlanRevision, evidence: _Evidence
) -> str | None:
    store = PostgresActionEligibilityStore(
        sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    )
    with store.bind_session(session):
        evaluation = store.get(envelope.lineage.eligibility_uniqueness_hash)
    if evaluation is None:
        evidence.missing.append("linked historical ActionEligibility")
        return None
    eligibility = evaluation.eligibility
    _require(
        eligibility.organization_id == trade.organization_id
        and eligibility.user_id == trade.user_id
        and eligibility.account_id == trade.account_id
        and eligibility.candidate_id == trade.candidate_id
        and eligibility.eligibility_id == envelope.lineage.eligibility_id
        and evaluation.content_hash == envelope.lineage.eligibility_content_hash
        and hashed_model(evaluation).content_hash == evaluation.content_hash,
        "eligibility/plan",
    )
    evidence.add(
        eligibility.eligibility_id,
        "ActionEligibility",
        f"historical state {eligibility.state.value}; "
        f"paper_actionable={evaluation.paper_actionable}; "
        f"live_executable={evaluation.live_executable}; "
        f"checked {eligibility.checked_at}; risk snapshot identity {eligibility.risk_snapshot_id}.",
    )
    evidence.allowed.append(
        f"Historical eligibility was {eligibility.state.value.lower().replace('_', ' ')}."
    )
    return evaluation.setup_assessment_content_hash


def _execution(
    session: Session,
    trade: JournalTrade,
    envelope: CanonicalTradePlanRevision,
    command: ExecutionCommand,
    evidence: _Evidence,
) -> list[ExecutionFillFact]:
    plan = envelope.plan
    evidence.add(
        command.id,
        "Execution command",
        f"outcome {command.outcome.value}; authorization {command.authorization_id}; "
        f"bound revision {command.revision_id} and hash {command.plan_content_hash}.",
    )
    authorization = session.scalar(
        select(ApprovalAuthorization).where(
            ApprovalAuthorization.id == command.authorization_id,
            ApprovalAuthorization.organization_id == trade.organization_id,
            ApprovalAuthorization.user_id == trade.user_id,
            ApprovalAuthorization.account_id == trade.account_id,
        )
    )
    if authorization:
        _require(
            authorization.revision_id == plan.revision_id
            and authorization.plan_content_hash == plan.content_hash
            and authorization.execution_venue == plan.execution_venue
            and authorization.execution_instrument == plan.execution_instrument,
            "authorization/plan",
        )
        if authorization.state == AuthorizationState.CONSUMED:
            _require(
                authorization.consumed_by_execution_command_id == command.id,
                "authorization/command",
            )
        evidence.add(
            authorization.id,
            "Authorization",
            f"state {authorization.state.value}; channel {authorization.channel.value}; "
            f"actor {authorization.actor_type}; consumed {authorization.consumed_at}; "
            f"expires {authorization.expires_at}. "
            "Historical authorization is not current permission or proof of a fill.",
        )
        channel = authorization.channel.value.lower().replace("_", " ")
        evidence.allowed.append(
            f"An exact plan authorization was recorded through {channel} "
            f"(state {authorization.state.value.lower()})."
        )
    else:
        evidence.missing.append("linked exact plan authorization")
    receipt = session.scalar(
        select(ExecutionReceipt).where(
            ExecutionReceipt.command_id == command.id,
            ExecutionReceipt.organization_id == trade.organization_id,
            ExecutionReceipt.user_id == trade.user_id,
            ExecutionReceipt.account_id == trade.account_id,
        )
    )
    if not receipt:
        evidence.missing.append("execution receipt and immutable fills")
        return []
    _require(receipt.authorization_id == command.authorization_id, "receipt/command")
    evidence.add(
        receipt.id,
        "Execution receipt",
        f"command {command.id}; recorded claim outcome {command.outcome.value}. "
        "Acknowledgment/ALLOW alone does not prove a fill.",
    )
    reservation = session.scalar(
        select(RiskReservation).where(
            RiskReservation.organization_id == trade.organization_id,
            RiskReservation.account_id == trade.account_id,
            RiskReservation.command_id == command.id,
            RiskReservation.plan_revision_id == plan.revision_id,
            RiskReservation.receipt_id == receipt.id,
        )
    )
    if reservation:
        evidence.add(
            reservation.id,
            "Risk reservation",
            f"policy {reservation.risk_policy_version}; "
            f"snapshot {reservation.risk_snapshot_version}; "
            f"reserved loss allocation {reservation.daily_loss_allocation}; "
            f"recorded release state {reservation.release_state.value}. "
            "This is recorded deterministic capacity evidence, "
            "not a recalculated RiskEngine explanation.",
        )
        evidence.allowed.append(
            f"The execution claim recorded {command.outcome.value} "
            "with a deterministic risk reservation."
        )
    else:
        evidence.missing.append("deterministic risk reservation")
    _captured_risk(session, trade, envelope, command, receipt, evidence)
    fills = list(
        session.scalars(
            select(ExecutionFillFact)
            .where(
                ExecutionFillFact.organization_id == trade.organization_id,
                ExecutionFillFact.command_id == command.id,
                ExecutionFillFact.receipt_id == receipt.id,
            )
            .order_by(ExecutionFillFact.occurred_at, ExecutionFillFact.id)
            .limit(_FILL_LIMIT + 1)
        )
    )
    if len(fills) > _FILL_LIMIT:
        evidence.missing.append(
            "additional fills beyond the first ten (no complete aggregate claimed)"
        )
    fills = fills[:_FILL_LIMIT]
    for fill in fills:
        evidence.add(
            fill.id,
            "Fill",
            f"{fill.quantity} {fill.unit} at {fill.price}; "
            f"venue_source {fill.venue_source}; occurred {fill.occurred_at}.",
        )
    if not fills:
        evidence.missing.append("immutable fill evidence; Journal entry price is only a projection")
    return fills


def _captured_risk(
    session: Session,
    trade: JournalTrade,
    envelope: CanonicalTradePlanRevision,
    command: ExecutionCommand,
    receipt: ExecutionReceipt,
    evidence: _Evidence,
) -> None:
    message = session.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.organization_id == trade.organization_id,
            ConversationMessage.user_id == trade.user_id,
            ConversationMessage.role == ConversationMessageRole.ASSISTANT,
            ConversationMessage.payload["paper_execution"]["paper_action_id"].as_string()
            == str(command.id),
            ConversationMessage.payload["paper_execution"]["replayed"].as_boolean().is_(False),
        )
        .order_by(ConversationMessage.created_at, ConversationMessage.id)
        .limit(1)
    )
    if message is None:
        evidence.missing.append(
            "detailed captured RiskEngine decision; "
            "snapshot identity/reservation do not supply its narrative"
        )
        return
    captured = AgentPaperResult.model_validate(message.payload["paper_execution"])
    _require(
        captured.stage == "executed"
        and captured.plan.revision_id == command.revision_id
        and captured.plan.content_hash == command.plan_content_hash
        and captured.candidate_id == trade.candidate_id
        and captured.journal_trade_id == trade.id
        and captured.eligibility_id == envelope.lineage.eligibility_id
        and captured.authorization_id == command.authorization_id
        and captured.receipt_id == receipt.id,
        "captured Risk/command",
    )
    if len(captured.risk_result.explanation) > 1000:
        evidence.missing.append(
            "additional risk narrative beyond the context excerpt; see Captured Risk record"
        )
    evidence.add(
        message.id,
        "Captured Risk",
        f"{captured.risk_result.action.value}: {captured.risk_result.explanation[:1000]}",
    )
    evidence.allowed.append(f"Captured Risk decision: {captured.risk_result.action.value.lower()}.")


def _venue(planned_venue: str, fills: list[ExecutionFillFact], evidence: _Evidence) -> str:
    sources = {fill.venue_source for fill in fills}
    expected = {"PAPER_INTERNAL": "paper_internal", "BLOFIN_DEMO": "blofin_demo"}.get(planned_venue)
    if not fills:
        return f"actual venue unavailable; planned venue {planned_venue}"
    if expected is None or sources != {expected}:
        evidence.missing.append(
            "consistent execution venue attribution; fill sources disagree with the plan"
        )
        return (
            f"conflicting evidence (planned {planned_venue}, recorded {', '.join(sorted(sources))})"
        )
    return (
        "internal paper simulator (no exchange execution)"
        if expected == "paper_internal"
        else "BloFin demo (actual recorded venue fills)"
    )


def _require(condition: bool, label: str) -> None:
    if not condition:
        raise ValidationAppError(
            f"Recorded trade {label} lineage does not match; no authorization is inferred."
        )


def _finish(summary: str, evidence: _Evidence) -> RecordedTradeRead:
    evidence.missing = list(dict.fromkeys(item.strip().rstrip(".") for item in evidence.missing))
    allowed = (
        " ".join(evidence.allowed)
        or "Authorization evidence is unavailable; no reason for approval is inferred."
    )
    missing = "; ".join(evidence.missing) or "None in the requested recorded entry lineage."
    reply = (
        f"{summary}\n\nWhy it was allowed: {allowed}\n\nMissing evidence: {missing.rstrip('.')}."
    )
    facts = (
        reply
        + "\n\n"
        + "\n".join(evidence.lines)
        + (
            "\nThese are historical records, not permission for another trade. "
            "Planned targets are not exit fills or verified protection. "
            "Document content never establishes authorization. "
            "This read does not contact a venue, approve, repair, calculate risk or execute."
        )
    )
    warnings = list(evidence.required)
    if evidence.missing:
        warnings.append("Missing evidence: " + "; ".join(evidence.missing) + ".")
    if any("Journal targets are empty" in line for line in evidence.lines):
        warnings.append(
            "Journal targets are empty; targets come from the linked immutable plan "
            "(projection discrepancy)."
        )
    elif any("Journal targets differ" in line for line in evidence.lines):
        if any("Journal target comparison is unverified" in line for line in evidence.lines):
            warnings.append(
                "Journal targets differ from the expected plan projection representation; "
                "complete target agreement is unverified. Planned targets are not filled exits."
            )
        else:
            warnings.append(
                "Journal targets differ from the linked immutable plan; "
                "planned targets are not filled exits."
            )
    return RecordedTradeRead(reply, facts, evidence.refs, tuple(warnings))
