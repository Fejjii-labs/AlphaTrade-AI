"""Closed, typed tool registry and deterministic intent routing.

Permissions come from persisted membership, never from a prompt or model reply.
Canonical paper execution is proposed here and applied only by its confirmation gateway.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, ValidationAppError
from app.db.models import Membership
from app.interactive_agent.actions import (
    ActionDescriptor,
    ActionRequest,
    DailyReviewInput,
    EmptyInput,
    JournalCreateInput,
    JournalNoteInput,
    KnowledgeInput,
    LearningStatusInput,
    PaperExecutionExplanationInput,
    PaperExecutionInput,
    PaperTradeInput,
    RecordedTradeInput,
    StrategyInput,
    StrategyValidationInput,
    WatcherChangeInput,
)
from app.interactive_agent.contracts import (
    AgentCapability,
    AgentTurnRequest,
    ArtifactKind,
    ProposalDecisionRequest,
    StructuredActionKind,
)
from app.interactive_agent.daily_review import route_daily_review
from app.interactive_agent.parsing import extract_direction, extract_symbol, extract_timeframe
from app.schemas.common import MembershipRole, StrictModel
from app.schemas.watcher_assignments import WatcherAssignmentReplace


@dataclass(frozen=True)
class Tool:
    name: str
    input_model: type[StrictModel]
    authority: str
    kind: StructuredActionKind
    artifact: ArtifactKind
    capability: AgentCapability
    limitations: tuple[str, ...]
    behavior: Literal["read", "propose", "confirm"] = "propose"

    def descriptor(self) -> ActionDescriptor:
        return ActionDescriptor(
            name=self.name,
            input_contract=self.input_model.model_json_schema(),
            authority=self.authority,
            behavior=self.behavior,
            required_permissions=[f"membership:{self.behavior}"],
            explicit_confirmation_required=self.behavior != "read",
            result_record_identity=(
                "DailyReview/v1.review_id; source record_type/record_id in daily_review"
                if self.name == "daily_review.read"
                else "conversation_messages.id"
                if self.behavior == "read"
                else "conversation_messages.payload.interactive_agent.proposals[].proposal_id; "
                "linked_strategy_proposal_id/resulting_record_id when available"
            ),
            limitations=list(self.limitations),
        )


_JOURNAL = (
    "JournalService applies a complete draft only after explicit hash-protected confirmation.",
)
_JOURNAL_NOTE = (
    "A scoped journal entry is required to append; otherwise this remains an unapplied proposal.",
    "Confirmation refuses a journal entry changed since this proposal was drafted.",
)
_STRATEGY = (
    "No strategy version, compiled identity, Strategy Brain setup or activation is changed.",
    "Confirmation ingests research notes and links; refinements retain a canonical DRAFT.",
)
_PAPER = (
    "This is a handoff to pretrade or the existing proposal authority, never execution approval.",
    "No Candidate, size, risk override, executable plan or order is created by the Agent.",
    "Confirmation cannot override risk; execution requires the current canonical gateways.",
)


def _tool(
    name: str,
    model: type[StrictModel],
    authority: str,
    kind: StructuredActionKind,
    artifact: ArtifactKind,
    capability: AgentCapability,
    limitations: tuple[str, ...],
) -> Tool:
    return Tool(name, model, authority, kind, artifact, capability, limitations)


_TOOLS = [
    Tool(
        "paper_trade.read_recorded",
        RecordedTradeInput,
        "canonical_recorded_trade_reads",
        StructuredActionKind.NONE,
        ArtifactKind.JOURNAL_ENTRY,
        AgentCapability.TRADE_DISCUSSION,
        ("Historical owner/account-scoped evidence only; never approval or execution.",),
        behavior="read",
    ),
    Tool(
        "paper_trade.explain_execution",
        PaperExecutionExplanationInput,
        "canonical_paper_execution_records",
        StructuredActionKind.NONE,
        ArtifactKind.TRADE_DECISION,
        AgentCapability.PRE_TRADE_REASONING,
        ("Historical canonical facts only; no model calculation, approval or execution.",),
        behavior="read",
    ),
    Tool(
        "strategy.learning_status",
        LearningStatusInput,
        "StrategyPromotionService",
        StructuredActionKind.NONE,
        ArtifactKind.OBSERVATION,
        AgentCapability.GOVERNED_LEARNING,
        (
            "Bounded canonical status reads only. Agent prose cannot approve, promote, "
            "roll back or enable live execution.",
        ),
        behavior="read",
    ),
    _tool(
        "paper_trade.prepare_execution",
        PaperExecutionInput,
        "canonical_paper_execution",
        StructuredActionKind.PROPOSE_TRADE_DECISION,
        ArtifactKind.TRADE_DECISION,
        AgentCapability.PRE_TRADE_REASONING,
        (
            "An existing Candidate and current ActionEligibility are required.",
            "Risk and size come from deterministic authorities; risk BLOCK is final.",
            "Separate hash-protected confirmation rechecks current state before paper execution.",
            "Live trading is refused.",
        ),
    ),
    Tool(
        "daily_review.read",
        DailyReviewInput,
        "DailyReviewService",
        StructuredActionKind.NONE,
        ArtifactKind.OBSERVATION,
        AgentCapability.DAILY_REVIEW,
        (
            "Existing DailyReview/v1 sources only; facts, observations, inference and research "
            "remain separate. UTC calendar day by default; supply timezone explicitly.",
            "Today's review is a current snapshot. No forecast, scheduling or delivery.",
        ),
        behavior="read",
    ),
    Tool(
        "context.read",
        EmptyInput,
        "existing_agent_reads",
        StructuredActionKind.NONE,
        ArtifactKind.OBSERVATION,
        AgentCapability.PERSISTENT_CONTEXT,
        ("Read through existing tenant-scoped services only.",),
        behavior="read",
    ),
    _tool(
        "journal.create",
        JournalCreateInput,
        "journals",
        StructuredActionKind.PROPOSE_JOURNAL_ENTRY,
        ArtifactKind.JOURNAL_ENTRY,
        AgentCapability.JOURNAL_CAPTURE,
        _JOURNAL,
    ),
    *[
        _tool(
            f"journal.{name}",
            JournalNoteInput,
            "journals",
            StructuredActionKind.PROPOSE_JOURNAL_APPEND,
            artifact,
            AgentCapability.POST_TRADE_REFLECTION,
            _JOURNAL_NOTE,
        )
        for name, artifact in (
            ("append_reflection", ArtifactKind.JOURNAL_ENTRY),
            ("record_mistake", ArtifactKind.LESSON),
            ("record_lesson", ArtifactKind.LESSON),
            ("record_observation", ArtifactKind.OBSERVATION),
        )
    ],
    *[
        _tool(
            f"strategy.{name}",
            StrategyValidationInput if name == "request_validation" else StrategyInput,
            authority,
            kind,
            artifact,
            AgentCapability.STRATEGY_AUTHORING,
            (
                *_STRATEGY,
                "Complete backtest inputs create an observable canonical backtest request."
                if name == "request_validation"
                else "Explicit hash-protected confirmation is required for application.",
            ),
        )
        for name, authority, kind, artifact in (
            (
                "create",
                "strategy_conversation_proposals",
                StructuredActionKind.PROPOSE_STRATEGY,
                ArtifactKind.STRATEGY,
            ),
            (
                "observation",
                "documents_and_chunks",
                StructuredActionKind.PROPOSE_OBSERVATION,
                ArtifactKind.OBSERVATION,
            ),
            (
                "hypothesis",
                "documents_and_chunks",
                StructuredActionKind.PROPOSE_HYPOTHESIS,
                ArtifactKind.HYPOTHESIS,
            ),
            (
                "refinement",
                "strategy_conversation_proposals",
                StructuredActionKind.PROPOSE_STRATEGY,
                ArtifactKind.STRATEGY,
            ),
            (
                "associate_evidence",
                "documents_and_chunks",
                StructuredActionKind.PROPOSE_STRATEGY_EVIDENCE,
                ArtifactKind.OBSERVATION,
            ),
            (
                "request_validation",
                "backtest_runs",
                StructuredActionKind.PROPOSE_VALIDATION_REQUEST,
                ArtifactKind.HYPOTHESIS,
            ),
        )
    ],
    _tool(
        "knowledge.propose",
        KnowledgeInput,
        "documents_and_chunks",
        StructuredActionKind.PROPOSE_KNOWLEDGE,
        ArtifactKind.LESSON,
        AgentCapability.PATTERN_AND_RULE_CAPTURE,
        ("Confirmation uses canonical RagService ingestion, quota checks and vector linking.",),
    ),
    _tool(
        "watcher.change",
        WatcherChangeInput,
        "watcher_watchlists",
        StructuredActionKind.PROPOSE_WATCHER_CHANGE,
        ArtifactKind.OBSERVATION,
        AgentCapability.STRATEGY_BRAIN,
        (
            "Confirmation applies through the existing five-slot validation and revision fence.",
            "Does not activate the Watcher worker or change Telegram preferences.",
        ),
    ),
    _tool(
        "watcher.assignments.replace",
        WatcherAssignmentReplace,
        "WatcherAssignmentService",
        StructuredActionKind.PROPOSE_WATCHER_CHANGE,
        ArtifactKind.OBSERVATION,
        AgentCapability.STRATEGY_BRAIN,
        (
            "Explicit hash-protected confirmation applies the reviewed owned version snapshots.",
            "Monitoring only; no trading activation, approval, "
            "notification or execution authority.",
            "Timeframes and parameters must match the server-owned immutable version exactly.",
        ),
    ),
    _tool(
        "paper_trade.propose",
        PaperTradeInput,
        "pretrade_and_trade_proposals",
        StructuredActionKind.PROPOSE_TRADE_DECISION,
        ArtifactKind.TRADE_DECISION,
        AgentCapability.PRE_TRADE_REASONING,
        (
            *_PAPER,
            "Complete explicit account and risk inputs may run canonical pretrade analysis.",
        ),
    ),
    Tool(
        "proposal.confirm",
        ProposalDecisionRequest,
        "existing_proposal_confirmation",
        StructuredActionKind.NONE,
        ArtifactKind.OBSERVATION,
        AgentCapability.PERSISTENT_CONTEXT,
        ("Separate confirm endpoint; a turn never confirms a proposal.",),
        behavior="confirm",
    ),
]
TOOLS = MappingProxyType({tool.name: tool for tool in _TOOLS})


def resolve_action(request: ActionRequest) -> tuple[Tool, StrictModel]:
    tool = TOOLS.get(request.name)
    if tool is None:
        raise ValidationAppError("Unknown Agent action; no action was performed.")
    if tool.behavior == "confirm":
        raise ValidationAppError("Use the separate proposal confirmation endpoint.")
    try:
        return tool, tool.input_model.model_validate(request.arguments)
    except ValidationError as exc:
        raise ValidationAppError("Agent action input contract is invalid.") from exc


def require_action_permission(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    read: bool = False,
) -> None:
    role = session.scalar(
        select(Membership.role).where(
            Membership.organization_id == organization_id,
            Membership.user_id == user_id,
        )
    )
    allowed = {MembershipRole.OWNER, MembershipRole.TRADER}
    if read:
        allowed.add(MembershipRole.VIEWER)
    if role not in allowed:
        raise ForbiddenError("Persisted membership does not permit this Agent action.")


def route_action(request: AgentTurnRequest) -> ActionRequest | None:
    """Conservative grammar; an explicit typed request takes precedence over inference."""
    if request.action is not None:
        return request.action
    text = request.message.strip()
    explanation = re.fullmatch(
        r"Explain paper execution (?:command=)?([a-f0-9-]{36})", text, re.IGNORECASE
    )
    if explanation:
        return ActionRequest(
            name="paper_trade.explain_execution", arguments={"command_id": explanation.group(1)}
        )
    if text.lower().startswith("prepare paper trade "):
        from app.agents.paper_intent import parse_paper_intent

        try:
            trade = parse_paper_intent(text)
        except (ValidationError, ValueError) as exc:
            raise ValidationAppError("Paper trade details are invalid.") from exc
        return ActionRequest(
            name="paper_trade.prepare_execution",
            arguments={"trade": trade.model_dump(mode="json")},
        )
    from app.interactive_agent.recorded_trade import route_recorded_trade

    recorded = route_recorded_trade(text, symbol=request.symbol)
    if recorded is not None:
        return recorded
    lower = text.lower()
    if re.search(
        r"\b(?:strategy changes.*proposed|change.*proposed|been replayed|"
        r"outperform.*baseline|completed paper validation|version.*paper active|"
        r"roll ?back|governed learning|promotion status|approve.*promotion|promote.*strategy)\b",
        lower,
    ):
        return ActionRequest(
            name="strategy.learning_status", arguments={"strategy_id": request.strategy_id}
        )
    args: dict[str, Any] = {"text": text[:4000]}
    strategy_args = dict(args, strategy_id=request.strategy_id)
    if re.search(
        r"\b(?:journal this|add to (?:my )?journal|log this trade|"
        r"write (?:a )?journal|create (?:a )?journal entry)\b",
        lower,
    ):
        return ActionRequest(
            name="journal.create",
            arguments=dict(
                args,
                symbol=request.symbol,
                timeframe=request.timeframe,
            ),
        )
    for pattern, name in (
        (r"\bappend (?:a )?reflection\b", "append_reflection"),
        (r"\brecord (?:a |my |this )?mistake\b", "record_mistake"),
        (r"\brecord (?:a |my |this )?lesson\b", "record_lesson"),
        (r"\brecord (?:an? |my |this )?observation\b", "record_observation"),
    ):
        if re.search(pattern, lower) and not re.search(r"\bstrategy\b", lower):
            if re.search(r"\b(?:knowledge|durable|trading rule)\b", lower):
                return ActionRequest(name="knowledge.propose", arguments=strategy_args)
            entry = re.search(
                r"\bjournal(?: entry)?\s+([a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12})\b",
                lower,
            )
            return ActionRequest(
                name=f"journal.{name}",
                arguments=dict(
                    args,
                    journal_entry_id=entry.group(1) if entry else None,
                ),
            )
    if re.search(
        r"\b(?:save|create|record|capture|remember)\b.{0,60}"
        r"\b(?:knowledge|durable|trading rule)\b",
        lower,
    ):
        return ActionRequest(name="knowledge.propose", arguments=strategy_args)
    if re.search(
        r"\b(?:request|schedule|validate|replay)\b.{0,50}"
        r"\b(?:validation|replay|later|strategy)\b",
        lower,
    ):
        return ActionRequest(name="strategy.request_validation", arguments=strategy_args)
    if re.search(r"\b(?:associate|link|attach)\b.{0,50}\bevidence\b", lower):
        return ActionRequest(name="strategy.associate_evidence", arguments=strategy_args)
    for noun in ("observation", "hypothesis"):
        if re.search(rf"\b(?:create|record|add)\b.{{0,40}}\b{noun}\b", lower):
            return ActionRequest(name=f"strategy.{noun}", arguments=strategy_args)
    if re.search(r"\b(?:refine|update|change|create|draft|build)\b.{0,40}\bstrateg", lower):
        name = "refinement" if re.search(r"\b(?:refine|update|change)\b", lower) else "create"
        return ActionRequest(name=f"strategy.{name}", arguments=strategy_args)
    if re.search(r"\bwatcher\b|\bwatchlist\b", lower):
        operation = re.search(r"\b(enable|disable|replace|reorder)\b", lower)
        if operation or re.search(r"\b(?:propose|change|update)\b.{0,40}\buniverse\b", lower):
            op = operation.group(1) if operation else "universe"
            watcher: dict[str, Any] = {"operation": op}
            position = re.search(r"\b(?:slot|position)\s+([1-5])\b", lower)
            if position:
                watcher["position"] = int(position.group(1))
            symbols = re.findall(r"\b[A-Z0-9]{2,15}USDT\b", text.upper())
            if symbols:
                watcher["symbol"] = symbols[-1]
            if op == "reorder":
                watcher["positions"] = [int(n) for n in re.findall(r"\b[1-5]\b", text)]
            return ActionRequest(name="watcher.change", arguments=watcher)
    if re.search(
        r"\b(?:enter|open|buy|sell|place|execute|submit)\b.{0,60}"
        r"\b(?:paper|trade|order|long|short|[a-z0-9]+usdt)\b",
        lower,
    ):
        paper: dict[str, Any] = {
            "symbol": extract_symbol(text) or request.symbol,
            "timeframe": extract_timeframe(text) or request.timeframe,
            "direction": extract_direction(text),
        }
        for key, pattern in (
            ("entry", r"\bentry(?:\s+price)?\s*[:=@]?\s*(\d+(?:\.\d+)?)"),
            ("stop", r"\bstop(?:\s+loss)?\s*[:=@]?\s*(\d+(?:\.\d+)?)"),
        ):
            match = re.search(pattern, lower)
            if match:
                paper[key] = match.group(1)
        targets = re.search(r"\btargets?\s*[:=@]?\s*((?:\d+(?:\.\d+)?\s*[, ]?\s*)+)", lower)
        if targets:
            paper["targets"] = re.findall(r"\d+(?:\.\d+)?", targets.group(1))[:10]
        account = re.search(r"\baccount(?: size| balance| equity)\s*[:=]?\s*(\d+(?:\.\d+)?)", lower)
        risk = re.search(r"\b(?:max risk|risk per trade)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%", lower)
        if account and risk and all(paper.get(key) for key in ("symbol", "timeframe", "direction")):
            paper["pretrade"] = {
                "symbol": paper["symbol"],
                "timeframe": paper["timeframe"],
                "direction": paper["direction"],
                "account_size": account.group(1),
                "max_risk_per_trade": risk.group(1),
                "strategy_id": str(request.strategy_id) if request.strategy_id else None,
            }
        return ActionRequest(name="paper_trade.propose", arguments=paper)
    return route_daily_review(request.message)
