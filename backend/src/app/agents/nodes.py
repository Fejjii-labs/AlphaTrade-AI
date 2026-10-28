"""LangGraph node implementations (orchestration only).

Nodes call :class:`~app.agents.runtime.AgentRuntime` services and tools. They
never touch providers or the database directly.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import structlog

from app.agents.intent_classifier import classify_intent_decision, classify_message_class
from app.agents.response_builder import build_trading_analysis, format_reply_from_analysis
from app.agents.runtime import AgentRuntime
from app.agents.state_utils import dump_partial, parse_state, patch_state
from app.core.operation_policy import set_operation_decision
from app.guardrails.apply import build_guardrail_updates, merge_safety_verdict
from app.guardrails.testing import FORCE_INVALID_OUTPUT
from app.guardrails.types import GuardrailInput
from app.schemas.agent import AgentState, Intent, OperationClass
from app.schemas.common import (
    CostSource,
    DocumentSourceType,
    ProposalStatus,
    RiskAction,
    RiskSeverity,
    SafetyVerdict,
    StrategyId,
    Timeframe,
    TradeDirection,
)
from app.schemas.market import IndicatorContext, MarketSnapshot
from app.schemas.proposal import ExitCriteria, TakeProfitLevel, TradeProposal
from app.schemas.rag import Citation
from app.schemas.risk import RiskCheckRequest
from app.schemas.tools import ToolInput, ToolOutput
from app.schemas.usage import UsageEvent
from app.services.narrative_service import format_reply_with_narrative
from app.services.risk.rules import RiskEvaluationContext
from app.strategies.base import StrategyEvaluationInput

logger = structlog.get_logger(__name__)


def receive_request(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    if not agent.message.strip():
        return patch_state(state, {"final_answer": "Empty message received."})
    return patch_state(state, {})


def auth_context(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    if agent.user_id is None or agent.organization_id is None:
        return patch_state(
            state,
            {
                "final_answer": "Authentication context is required.",
                "safety_verdict": SafetyVerdict.BLOCK,
            },
        )
    return patch_state(state, {})


def quota_check(state: dict, runtime: AgentRuntime) -> dict:
    if runtime.quota_exceeded:
        return patch_state(
            state,
            {
                "safety_verdict": SafetyVerdict.BLOCK,
                "final_answer": "Quota exceeded for this organization.",
            },
        )
    return patch_state(state, {})


def rate_limit_check(state: dict, runtime: AgentRuntime) -> dict:
    if runtime.rate_limited:
        return patch_state(
            state,
            {
                "safety_verdict": SafetyVerdict.BLOCK,
                "final_answer": "Rate limit exceeded. Please retry later.",
            },
        )
    return patch_state(state, {})


def prompt_injection_check(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    result = runtime.guardrails.check_prompt_injection(GuardrailInput.from_agent_state(agent))
    if result.blocked:
        return patch_state(
            state,
            build_guardrail_updates(agent, result, runtime, audit_reason="prompt_injection"),
        )
    verdict = merge_safety_verdict(agent.safety_verdict, result)
    return patch_state(state, {"safety_verdict": verdict})


def moderation_check(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    if agent.safety_verdict is SafetyVerdict.BLOCK:
        return patch_state(state, {})
    result = runtime.guardrails.check_moderation(GuardrailInput.from_agent_state(agent))
    if result.blocked:
        return patch_state(
            state,
            build_guardrail_updates(agent, result, runtime, audit_reason="moderation"),
        )
    updates: dict = {"safety_verdict": merge_safety_verdict(agent.safety_verdict, result)}
    if result.triggered_rules and not result.blocked:
        updates["audit_events"] = [
            *agent.audit_events,
            runtime.observability.emit_guardrail_warned(agent, result, reason="moderation"),
        ]
    return patch_state(state, updates)


def trading_policy_check(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    if agent.safety_verdict is SafetyVerdict.BLOCK:
        return patch_state(state, {})
    result = runtime.guardrails.check_trading_policy(GuardrailInput.from_agent_state(agent))
    if result.blocked:
        return patch_state(
            state,
            build_guardrail_updates(agent, result, runtime, audit_reason="trading_policy"),
        )
    return patch_state(state, {})


def message_classification(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    msg_class = classify_message_class(agent.message)
    return patch_state(state, {"message_class": msg_class})


def intent_classification(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    decision = classify_intent_decision(
        agent.message,
        organization_id=agent.organization_id,
        user_id=agent.user_id,
    )
    set_operation_decision(decision)
    updates: dict = {
        "intent": decision.intent,
        "intent_decision": dump_partial(decision),
        "requires_clarification": decision.requires_clarification,
    }
    if decision.requires_clarification and not agent.final_answer:
        updates["final_answer"] = (
            "I need a more specific instruction before any plan, approval, or execution. "
            + ("; ".join(decision.ambiguity_reasons) if decision.ambiguity_reasons else "")
        )
    return patch_state(state, updates)


_SOURCE_OF_TRUTH = "SOURCE OF TRUTH (deterministic):"


def _format_tool_answer(tool_name: str, summary: str) -> str:
    return f"{_SOURCE_OF_TRUTH} [{tool_name}] {summary}"


_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)


def _message_uuid(message: str) -> str | None:
    match = _UUID_RE.search(message)
    return match.group(0) if match else None


def _bound_strategy_id(agent: AgentState) -> str | None:
    if agent.bound_strategy_id is not None:
        return str(agent.bound_strategy_id)
    return _message_uuid(agent.message)


def paper_action(state: dict, runtime: AgentRuntime) -> dict:
    """Validated conversation orchestration through canonical paper authorities only."""
    from pydantic import ValidationError

    from app.agents.paper_intent import parse_paper_confirmation, parse_paper_intent
    from app.core.errors import AppError

    agent = parse_state(state)
    try:
        if (
            runtime.paper_execution_service is None
            or runtime.session is None
            or agent.organization_id is None
            or agent.user_id is None
            or agent.conversation_id is None
        ):
            raise ValueError("Canonical paper runtime and authenticated conversation are required.")
        scope = {
            "organization_id": agent.organization_id,
            "user_id": agent.user_id,
            "conversation_id": agent.conversation_id,
        }
        with runtime.session.begin_nested():
            if agent.intent is Intent.PREPARE_PAPER_TRADE:
                result = runtime.paper_execution_service.prepare(
                    parse_paper_intent(agent.message), **scope
                )
            else:
                result = runtime.paper_execution_service.confirm(
                    parse_paper_confirmation(agent.message), **scope
                )
        if result.stage == "proposed":
            plan = result.plan
            answer = (
                f"Paper execution proposal: {plan.side.value} {plan.execution_instrument}, "
                f"entry {plan.basis_policy.execution_price.value}, "
                f"stop {plan.risk_and_exits.stop.value}, "
                f"targets {', '.join(str(t.price.value) for t in plan.risk_and_exits.targets)}, "
                f"size {plan.quantity.value} {plan.quantity.unit}, "
                f"maximum loss {plan.risk_and_exits.maximum_loss.value} USDT. "
                f"Internal paper policy: fees, funding and slippage are zero. "
                f"Valid until {plan.valid_until.isoformat()}.\n"
                f"Confirm this exact proposal with:\n{result.confirmation_message}"
            )
        else:
            answer = (
                f"Paper action {result.stage}: Candidate {result.candidate_id}, "
                f"plan {result.plan.plan_id}, revision {result.plan.revision_id}, "
                f"paper action {result.paper_action_id}, receipt {result.receipt_id}, "
                f"journal {result.journal_trade_id}."
            )
            if result.reason_code:
                answer += f" Reason: {result.reason_code}."
        return patch_state(
            state,
            {
                "paper_execution": dump_partial(result),
                "final_answer": answer,
                "proposal_id": result.plan.plan_id,
                "approval_id": result.approval_id,
                "approval_required": result.stage == "proposed",
                "risk_result": dump_partial(result.risk_result),
                "risk_level": result.risk_result.severity,
                "safety_verdict": SafetyVerdict.BLOCK
                if result.stage == "blocked"
                else agent.safety_verdict,
            },
        )
    except (AppError, ValidationError, ValueError) as exc:
        updates = {
            "final_answer": f"Paper execution refused: {exc}",
            "safety_verdict": SafetyVerdict.BLOCK,
            "approval_required": False,
        }
        if isinstance(exc, AppError) and exc.details and "risk_result" in exc.details:
            updates["risk_result"] = exc.details["risk_result"]
            updates["risk_level"] = exc.details["risk_result"]["severity"]
        return patch_state(state, updates)


def strategy_workflow_tools(state: dict, runtime: AgentRuntime) -> dict:
    """Route Slice 33 tools for strategy library, pre-trade, sizing, and comparison."""
    agent = parse_state(state)
    if agent.organization_id is None or agent.user_id is None:
        return patch_state(state, {"final_answer": "Authentication context is required."})

    org = str(agent.organization_id)
    user = str(agent.user_id)
    symbol = agent.symbol or _extract_symbol(agent.message) or "BTCUSDT"
    timeframe = agent.timeframe or Timeframe.H4
    tool_outputs: list = list(agent.tool_outputs)
    tool_calls: list = list(agent.tool_calls)
    audit_events: list = list(agent.audit_events)
    answer_lines: list[str] = []

    def _run_tool(name: str, args: dict) -> ToolOutput:
        output = runtime.tool_registry.execute(name, args)
        tool_audit = runtime.observability.emit_tool_called(
            agent,
            tool_name=name,
            success=output.success,
            latency_ms=output.latency_ms,
            used_fallback=output.used_fallback,
        )
        tool_outputs.append(dump_partial(output))
        tool_calls.append(dump_partial(ToolInput(tool_name=name, arguments=args)))
        audit_events.append(tool_audit)
        return output

    intent = agent.intent

    if intent is Intent.STRATEGY_CARD:
        from app.agents.mutation_policy import mutation_allowed

        if not mutation_allowed(agent.message):
            output = _run_tool(
                "strategy_library_tool",
                {"action": "list", "organization_id": org, "user_id": user},
            )
            if output.success and output.result:
                total = output.result.get("total", 0)
                answer_lines.append(
                    _format_tool_answer(
                        "strategy_library_tool",
                        f"Preview only — no strategy card created ({total} existing). "
                        "Reply with 'I confirm' plus the idea to persist a card.",
                    )
                )
            else:
                answer_lines.append(
                    "Preview only — no strategy card created. Confirm explicitly to persist."
                )
        else:
            idea = agent.message.strip()[:500] or "Workspace strategy idea"
            output = _run_tool(
                "strategy_library_tool",
                {
                    "action": "create",
                    "organization_id": org,
                    "user_id": user,
                    "user_message": agent.message,
                    "confirm": True,
                    "name": idea[:80],
                    "setup_type": StrategyId.HTF_TREND_PULLBACK.value,
                    "card": {
                        "strategy_name": idea[:120],
                        "entry_conditions": [idea],
                        "invalidation": ["Close below defined invalidation level"],
                        "stop_loss": ["Below invalidation swing"],
                        "confirmation_conditions": ["Await confirmation per playbook"],
                        "take_profit_plan": ["TP1 at prior resistance"],
                        "asset_universe": [symbol],
                        "timeframes": [timeframe.value],
                    },
                },
            )
            if output.success and output.result:
                answer_lines.append(
                    _format_tool_answer(
                        "strategy_library_tool",
                        f"Strategy card created: {output.result.get('name', 'strategy')}.",
                    )
                )
            else:
                answer_lines.append(f"Strategy card creation failed: {output.error}")

    elif intent in {Intent.PRE_TRADE, Intent.INVALIDATION_QUERY}:
        output = _run_tool(
            "pretrade_analysis_tool",
            {
                "organization_id": org,
                "user_id": user,
                "symbol": symbol,
                "exchange": "mock",
                "direction": "long",
                "timeframe": timeframe.value,
                "account_size": "10000",
                "max_risk_per_trade": "1",
            },
        )
        if output.success and output.result:
            rec = output.result.get("final_recommendation", "n/a")
            stop = output.result.get("suggested_stop_loss")
            inv = output.result.get("invalidation_summary") or output.result.get("invalidation")
            answer_lines.append(
                _format_tool_answer(
                    "pretrade_analysis_tool",
                    f"Recommendation: {rec}. Stop: {stop}. Invalidation: {inv}.",
                )
            )
            if intent is Intent.INVALIDATION_QUERY and stop:
                answer_lines.append(
                    _format_tool_answer(
                        "pretrade_analysis_tool",
                        f"Stop loss reference: {stop}.",
                    )
                )
        else:
            answer_lines.append(f"Pre-trade analysis failed: {output.error}")

    elif intent in {Intent.POSITION_SIZE, Intent.LOSS_ACCEPTANCE}:
        close = agent.market_context.close if agent.market_context else Decimal("60000")
        invalidation = close * Decimal("0.97")
        sizing_out = _run_tool(
            "position_sizing_tool",
            {
                "request": {
                    "entry_price": str(close),
                    "invalidation_level": str(invalidation),
                    "account_balance": "10000",
                    "max_risk_percent": "1",
                    "confidence_score": 70,
                },
            },
        )
        if sizing_out.success and sizing_out.result:
            planned = sizing_out.result.get("planned_loss_amount")
            stop_dist = sizing_out.result.get("stop_loss_distance")
            max_loss = sizing_out.result.get("maximum_acceptable_loss")
            answer_lines.append(
                _format_tool_answer(
                    "position_sizing_tool",
                    f"Planned loss {planned}; stop {stop_dist}; max loss {max_loss}.",
                )
            )
            if intent is Intent.LOSS_ACCEPTANCE:
                answer_lines.append(
                    _format_tool_answer(
                        "loss_acceptance",
                        "Confirm calmly whether planned loss is acceptable before paper execution.",
                    )
                )
        else:
            answer_lines.append(f"Position sizing failed: {sizing_out.error}")

    elif intent is Intent.MANUAL_LEVELS:
        output = _run_tool(
            "manual_levels_tool",
            {
                "action": "list",
                "organization_id": org,
                "user_id": user,
                "symbol": symbol,
            },
        )
        if output.success and output.result:
            items = output.result.get("items", [])
            total = output.result.get("total", 0)
            labels = [f"{i.get('level_type')} @ {i.get('price') or 'zone'}" for i in items[:5]]
            answer_lines.append(
                _format_tool_answer(
                    "manual_levels_tool",
                    f"{total} level(s) for {symbol}: {', '.join(labels) or 'none'}.",
                )
            )
        else:
            answer_lines.append(f"Manual levels lookup failed: {output.error}")

    elif intent is Intent.HUMAN_VS_SYSTEM:
        trade_match = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            agent.message,
            re.I,
        )
        if trade_match is None:
            answer_lines.append(
                "Provide a journal or proposal trade ID to compare human vs system plan."
            )
        else:
            output = _run_tool(
                "human_vs_system_tool",
                {
                    "trade_id": trade_match.group(0),
                    "organization_id": org,
                    "user_id": user,
                },
            )
            if output.success and output.result:
                score = output.result.get("plan_adherence_score")
                answer_lines.append(
                    _format_tool_answer(
                        "human_vs_system_tool",
                        f"Plan adherence score {score}/100.",
                    )
                )
            else:
                answer_lines.append(f"Comparison failed: {output.error}")

    elif intent in {Intent.EARLY_EXIT_QUERY, Intent.STOP_DISCIPLINE_QUERY}:
        trade_match = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            agent.message,
            re.I,
        )
        if trade_match is None:
            answer_lines.append("Provide a journal or proposal trade ID for discipline analysis.")
        else:
            output = _run_tool(
                "human_vs_system_tool",
                {
                    "trade_id": trade_match.group(0),
                    "organization_id": org,
                    "user_id": user,
                },
            )
            if output.success and output.result:
                if intent is Intent.EARLY_EXIT_QUERY:
                    runner = output.result.get("missed_runner") or {}
                    flag = runner.get("early_exit_flag")
                    lesson = runner.get("recommended_lesson", "No early exit lesson available.")
                    answer_lines.append(
                        _format_tool_answer(
                            "human_vs_system_tool",
                            f"Early exit flag: {flag}. {lesson}",
                        )
                    )
                else:
                    stop = output.result.get("stop_loss_analysis") or {}
                    answer_lines.append(
                        _format_tool_answer(
                            "human_vs_system_tool",
                            stop.get("lesson", "Stop discipline data unavailable."),
                        )
                    )
            else:
                answer_lines.append(f"Analysis failed: {output.error}")

    elif intent is Intent.STRATEGY_TESTABILITY:
        output = _run_tool(
            "strategy_testability_tool",
            {"action": "score_first", "organization_id": org, "user_id": user},
        )
        if output.success and output.result:
            score = output.result.get("score")
            band = output.result.get("band")
            missing = output.result.get("missing_fields", [])
            labels = ", ".join(m.get("label", "") for m in missing[:5]) or "none"
            answer_lines.append(
                _format_tool_answer(
                    "strategy_testability_tool",
                    f"Testability {score}/100 ({band}). Missing: {labels}.",
                )
            )
        else:
            answer_lines.append(f"Testability check failed: {output.error}")

    elif intent is Intent.STRUCTURE_STRATEGY:
        output = _run_tool(
            "structure_from_text_tool",
            {"text": agent.message, "organization_id": org, "user_id": user},
        )
        if output.success and output.result:
            valid = output.result.get("validation", {}).get("valid")
            challenges = output.result.get("challenge_notes") or []
            answer_lines.append(
                _format_tool_answer(
                    "structure_from_text_tool",
                    f"Draft structured rules generated (valid={valid}). "
                    "This remains a preview until you explicitly confirm.",
                )
            )
            for note in challenges[:3]:
                answer_lines.append(f"Challenge: {note}")
            spec_errors = output.result.get("pattern_spec_errors") or []
            if spec_errors:
                answer_lines.append(
                    "pattern_spec omitted (fail closed): " + "; ".join(spec_errors[:4])
                )
        else:
            answer_lines.append(f"Structure draft failed: {output.error}")

    elif intent in {
        Intent.LESSON_PENDING_QUERY,
        Intent.LESSON_ACCEPTED_QUERY,
        Intent.LESSON_ACCEPT,
        Intent.LESSON_REJECT,
        Intent.LESSON_RULE_SUGGEST,
        Intent.ADD_RUNNER_RULE,
    }:
        lesson_match = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            agent.message,
            re.I,
        )
        action = "list_pending"
        if intent is Intent.LESSON_ACCEPTED_QUERY:
            action = "list_accepted"
            if "early exit" in agent.message.lower():
                action = "list_early_exit"
            elif "stop" in agent.message.lower():
                action = "list_stop_refusal"
        elif intent is Intent.LESSON_ACCEPT:
            action = "accept"
        elif intent is Intent.LESSON_REJECT:
            action = "reject"
        elif intent is Intent.LESSON_RULE_SUGGEST:
            action = "rule_suggest"
        elif intent is Intent.ADD_RUNNER_RULE:
            action = "runner_rule_hint"
        tool_args: dict = {
            "action": action,
            "lesson_id": lesson_match.group(0) if lesson_match else None,
            "organization_id": org,
            "user_id": user,
            "user_message": agent.message,
        }
        from app.agents.mutation_policy import mutation_allowed

        preview_only = intent in {
            Intent.LESSON_ACCEPT,
            Intent.LESSON_REJECT,
        } and not mutation_allowed(agent.message)
        if preview_only:
            tool_args["action"] = "rule_suggest" if lesson_match else "list_pending"
            output = _run_tool("lesson_review_tool", tool_args)
            if output.success and output.result:
                answer_lines.append(
                    _format_tool_answer(
                        "lesson_review_tool",
                        "Preview only — no state changed. To mutate, reply with explicit "
                        "confirmation (e.g. 'I confirm accept lesson <id>').",
                    )
                )
                summary = output.result.get("summary", "")
                if summary:
                    answer_lines.append(summary)
            else:
                answer_lines.append(f"Lesson preview failed: {output.error}")
        else:
            output = _run_tool("lesson_review_tool", tool_args)
            if output.success and output.result:
                summary = output.result.get("summary", "Lesson data retrieved.")
                answer_lines.append(_format_tool_answer("lesson_review_tool", summary))
                if output.result.get("pending_observation"):
                    answer_lines.append(
                        "Note: pending observations are not permanent rules until you accept them."
                    )
            else:
                answer_lines.append(f"Lesson review failed: {output.error}")

    elif intent is Intent.PAPER_ELIGIBILITY_BLOCKERS:
        output = _run_tool(
            "paper_eligibility_tool",
            {
                "action": "blockers",
                "organization_id": org,
                "user_id": user,
            },
        )
        if output.success and output.result:
            blockers = output.result.get("blockers") or []
            answer_lines.append(
                _format_tool_answer(
                    "paper_eligibility_tool",
                    output.result.get("summary", "Paper eligibility blockers retrieved."),
                )
            )
            if blockers:
                answer_lines.append(f"Blockers: {'; '.join(blockers[:5])}")
        else:
            answer_lines.append(f"Paper eligibility check failed: {output.error}")

    elif intent is Intent.LESSON_STRATEGY_UPDATE:
        output = _run_tool(
            "lesson_review_tool",
            {"action": "lessons_to_update", "organization_id": org, "user_id": user},
        )
        if output.success and output.result:
            answer_lines.append(
                _format_tool_answer("lesson_review_tool", output.result.get("summary", ""))
            )
            answer_lines.append(
                "Pending observations are not permanent rules until you accept them in Lessons."
            )
        else:
            answer_lines.append(f"Lesson query failed: {output.error}")

    elif intent is Intent.LESSON_CREATE_VERSION:
        lesson_match = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            agent.message,
            re.I,
        )
        from app.agents.mutation_policy import mutation_allowed

        if not mutation_allowed(agent.message):
            output = _run_tool(
                "lesson_review_tool",
                {
                    "action": "rule_suggest",
                    "lesson_id": lesson_match.group(0) if lesson_match else None,
                    "organization_id": org,
                    "user_id": user,
                    "user_message": agent.message,
                },
            )
            if output.success and output.result:
                answer_lines.append(
                    _format_tool_answer(
                        "lesson_review_tool",
                        "Preview only — strategy version not created. "
                        "Confirm explicitly to proceed.",
                    )
                )
                answer_lines.append(output.result.get("summary", ""))
            else:
                answer_lines.append(f"Version preview failed: {output.error}")
        else:
            output = _run_tool(
                "lesson_review_tool",
                {
                    "action": "create_version_from_lesson",
                    "lesson_id": lesson_match.group(0) if lesson_match else None,
                    "organization_id": org,
                    "user_id": user,
                    "user_message": agent.message,
                    "confirm": True,
                },
            )
            if output.success and output.result:
                answer_lines.append(
                    _format_tool_answer(
                        "lesson_review_tool",
                        output.result.get("summary", "Strategy version created from lesson."),
                    )
                )
            else:
                answer_lines.append(f"Version creation failed: {output.error}")

    elif intent in {Intent.LESSON_STRATEGY_LINKED, Intent.LESSON_UNRESOLVED_BLOCKERS}:
        list_out = _run_tool(
            "strategy_library_tool",
            {"action": "list", "organization_id": org, "user_id": user},
        )
        strategy_id = None
        if list_out.success and list_out.result:
            items = list_out.result.get("items", [])
            strategy_id = items[0].get("id") if items else None
        action = (
            "list_unresolved_for_strategy"
            if intent is Intent.LESSON_UNRESOLVED_BLOCKERS
            else "list_for_strategy"
        )
        output = _run_tool(
            "lesson_review_tool",
            {
                "action": action,
                "strategy_id": strategy_id,
                "organization_id": org,
                "user_id": user,
            },
        )
        if output.success and output.result:
            answer_lines.append(
                _format_tool_answer("lesson_review_tool", output.result.get("summary", ""))
            )
            if output.result.get("pending_observation"):
                answer_lines.append("Pending observations differ from accepted trading memory.")
        else:
            answer_lines.append(f"Lesson query failed: {output.error}")

    elif intent is Intent.BACKTEST_PREP:
        test_out = _run_tool(
            "strategy_testability_tool",
            {"organization_id": org, "user_id": user},
        )
        elig_out = _run_tool(
            "paper_eligibility_tool",
            {"action": "blockers", "organization_id": org, "user_id": user},
        )
        if test_out.success and test_out.result:
            missing = test_out.result.get("missing_fields") or []
            labels = [m.get("label") for m in missing[:4] if m.get("label")]
            answer_lines.append(
                _format_tool_answer(
                    "strategy_testability_tool",
                    f"Score {test_out.result.get('score')}. "
                    f"Ready={test_out.result.get('ready_for_backtest')}. "
                    f"Missing: {', '.join(labels) or 'none'}.",
                )
            )
        if elig_out.success and elig_out.result:
            blockers = elig_out.result.get("blockers") or []
            if blockers:
                answer_lines.append(f"Also blocked: {'; '.join(blockers[:3])}")

    elif intent is Intent.STRATEGY_STATUS:
        output = _run_tool(
            "strategy_library_tool",
            {"action": "list", "organization_id": org, "user_id": user},
        )
        if output.success and output.result:
            items = output.result.get("items", [])
            validated = [
                i.get("name")
                for i in items
                if (i.get("validation_status") or i.get("latest_card", {}).get("validation_status"))
                == "validated"
            ]
            answer_lines.append(
                _format_tool_answer(
                    "strategy_library_tool",
                    f"Validated strategies: {', '.join(validated) or 'none yet'}.",
                )
            )
        else:
            answer_lines.append(f"Strategy list failed: {output.error}")

    elif intent is Intent.BACKTEST_QUEUE:
        output = _run_tool(
            "strategy_library_tool",
            {"action": "list", "organization_id": org, "user_id": user},
        )
        if output.success and output.result:
            items = output.result.get("items", [])
            pending = next(
                (
                    i
                    for i in items
                    if (i.get("backtest_status") or "not_run") not in {"complete", "completed"}
                ),
                None,
            )
            if pending:
                answer_lines.append(
                    _format_tool_answer(
                        "strategy_library_tool",
                        f"Next backtest: {pending.get('name')} ({pending.get('backtest_status')}).",
                    )
                )
            else:
                answer_lines.append(
                    _format_tool_answer(
                        "strategy_library_tool",
                        "All strategies have backtest runs or none exist.",
                    )
                )
        else:
            answer_lines.append(f"Strategy list failed: {output.error}")

    elif intent is Intent.BACKTEST_RUN:
        from app.agents.mutation_policy import mutation_allowed

        strategy_id = _bound_strategy_id(agent)
        if strategy_id is None:
            answer_lines.append(
                "Name the strategy UUID (or open this chat from Strategy Lab) "
                "before running a backtest. First-listed strategy fallback is disabled."
            )
        elif not mutation_allowed(agent.message):
            output = _run_tool(
                "backtest_tool",
                {
                    "action": "latest_result",
                    "organization_id": org,
                    "user_id": user,
                    "strategy_id": strategy_id,
                },
            )
            answer_lines.append(
                "Preview only — backtest not started. "
                "Reply with 'I confirm' and the strategy id to run."
            )
            if output.success and output.result:
                answer_lines.append(_format_tool_answer("backtest_tool", str(output.result)[:400]))
        else:
            tf = timeframe.value
            output = _run_tool(
                "backtest_tool",
                {
                    "action": "run",
                    "organization_id": org,
                    "user_id": user,
                    "strategy_id": strategy_id,
                    "user_message": agent.message,
                    "confirm": True,
                    "assumptions": {
                        "symbol": symbol,
                        "timeframe": tf,
                        "exchange": "mock",
                    },
                },
            )
            if output.success and output.result:
                res = output.result.get("result") or {}
                metrics = res.get("metrics") or {}
                answer_lines.append(
                    _format_tool_answer(
                        "backtest_tool",
                        f"Backtest {output.result.get('status')}: "
                        f"trades={metrics.get('trade_count')}, "
                        f"win_rate={metrics.get('win_rate')}, "
                        f"recommendation={res.get('recommendation')}. "
                        "Historical simulation only — not a profit guarantee.",
                    )
                )
            else:
                answer_lines.append(f"Backtest failed: {output.error}")

    elif intent in {Intent.BACKTEST_RESULTS, Intent.BACKTEST_ELIGIBILITY}:
        list_out = _run_tool(
            "strategy_library_tool",
            {"action": "list", "organization_id": org, "user_id": user},
        )
        strategy_id = _bound_strategy_id(agent)
        if strategy_id is None and list_out.success and list_out.result:
            items = list_out.result.get("items", [])
            strategy_id = items[0].get("id") if items else None
        if strategy_id is None:
            answer_lines.append("No strategy found.")
        elif intent is Intent.BACKTEST_ELIGIBILITY:
            output = _run_tool(
                "paper_eligibility_tool",
                {
                    "action": "evaluate",
                    "organization_id": org,
                    "user_id": user,
                    "strategy_id": strategy_id,
                },
            )
            if output.success and output.result:
                blockers = output.result.get("blockers") or []
                answer_lines.append(
                    _format_tool_answer(
                        "paper_eligibility_tool",
                        output.result.get("summary")
                        or f"Status: {output.result.get('status')}. "
                        f"Eligible: {output.result.get('paper_eligible')}.",
                    )
                )
                if blockers:
                    answer_lines.append(f"Blockers: {'; '.join(blockers[:3])}")
            else:
                answer_lines.append(f"Eligibility check failed: {output.error}")
        else:
            output = _run_tool(
                "backtest_tool",
                {
                    "action": "latest_result",
                    "organization_id": org,
                    "user_id": user,
                    "strategy_id": strategy_id,
                },
            )
            if output.success and output.result:
                res = output.result.get("result") or {}
                metrics = res.get("metrics") or {}
                limitations = res.get("limitations") or []
                answer_lines.append(
                    _format_tool_answer(
                        "backtest_tool",
                        f"Latest backtest: trades={metrics.get('trade_count')}, "
                        f"PF={metrics.get('profit_factor')}, "
                        f"max_dd={metrics.get('max_drawdown_pct')}%, "
                        f"recommendation={res.get('recommendation')}.",
                    )
                )
                if limitations:
                    answer_lines.append(f"Limitations: {'; '.join(limitations[:3])}")
            else:
                answer_lines.append(f"Backtest results failed: {output.error}")

    elif intent in {
        Intent.PAPER_VALIDATION_START,
        Intent.PAPER_VALIDATION_SCAN,
        Intent.PAPER_VALIDATION_QUERY,
        Intent.PAPER_VALIDATION_RECOMMEND,
    }:
        list_out = _run_tool(
            "strategy_library_tool",
            {"action": "list", "organization_id": org, "user_id": user},
        )
        from app.agents.mutation_policy import mutation_allowed

        strategy_id = _bound_strategy_id(agent)
        if (
            strategy_id is None
            and intent
            in {
                Intent.PAPER_VALIDATION_QUERY,
                Intent.PAPER_VALIDATION_RECOMMEND,
            }
            and list_out.success
            and list_out.result
        ):
            items = list_out.result.get("items", [])
            strategy_id = items[0].get("id") if items else None
        if strategy_id is None:
            answer_lines.append(
                "Name the strategy UUID (or open this chat from Strategy Lab). "
                "First-listed strategy fallback is disabled for paper mutations."
            )
        elif intent in {Intent.PAPER_VALIDATION_START, Intent.PAPER_VALIDATION_SCAN} and (
            not mutation_allowed(agent.message)
        ):
            action = "status"
            output = _run_tool(
                "paper_validation_tool",
                {
                    "action": action,
                    "organization_id": org,
                    "user_id": user,
                    "strategy_id": strategy_id,
                    "user_message": agent.message,
                },
            )
            answer_lines.append(
                "Preview only — paper validation was not started or scanned. "
                "Reply with 'I confirm' and the strategy id to mutate."
            )
            if output.success and output.result:
                answer_lines.append(
                    _format_tool_answer(
                        "paper_validation_tool",
                        output.result.get("summary", "Paper validation data retrieved."),
                    )
                )
        else:
            action = "status"
            if intent is Intent.PAPER_VALIDATION_START:
                action = "start"
            elif intent is Intent.PAPER_VALIDATION_SCAN:
                action = "scan"
            elif intent is Intent.PAPER_VALIDATION_RECOMMEND:
                action = "recommend"
            elif "signal" in agent.message.lower():
                action = "signals"
            elif "open" in agent.message.lower():
                action = "open_trades"
            elif "metric" in agent.message.lower():
                action = "metrics"
            elif "paper bot" in agent.message.lower() or "what did" in agent.message.lower():
                action = "activity"
            elif "restricted" in agent.message.lower():
                action = "restricted"
            elif "skipped" in agent.message.lower():
                action = "skip_reason"
            elif "stale" in agent.message.lower():
                action = "data_stale"
            elif "blocker" in agent.message.lower():
                action = "blockers"
            elif "last" in agent.message.lower() and "run" in agent.message.lower():
                action = "last_run"
            elif "ready" in agent.message.lower() and "paper" in agent.message.lower():
                action = "ready"
            output = _run_tool(
                "paper_validation_tool",
                {
                    "action": action,
                    "organization_id": org,
                    "user_id": user,
                    "strategy_id": strategy_id,
                    "user_message": agent.message,
                    "confirm": True,
                },
            )
            if output.success and output.result:
                answer_lines.append(
                    _format_tool_answer(
                        "paper_validation_tool",
                        output.result.get("summary", "Paper validation data retrieved."),
                    )
                )
                metrics = output.result.get("metrics")
                if metrics:
                    answer_lines.append(
                        f"Metrics: trades={metrics.get('paper_trades_count')}, "
                        f"win_rate={metrics.get('win_rate')}, "
                        f"max_dd={metrics.get('max_drawdown_pct')}%."
                    )
                blockers = output.result.get("blockers") or []
                if blockers:
                    answer_lines.append(f"Blockers: {'; '.join(blockers[:3])}")
            else:
                answer_lines.append(f"Paper validation failed: {output.error}")

    elif intent is Intent.PAPER_SCHEDULER_QUERY:
        sched_action = "scheduler_status"
        if "tick" in agent.message.lower() or "run scheduler" in agent.message.lower():
            sched_action = "scheduler_tick"
        output = _run_tool(
            "paper_validation_tool",
            {
                "action": sched_action,
                "organization_id": org,
                "user_id": user,
                "strategy_id": org,
                "user_message": agent.message,
            },
        )
        if output.success and output.result:
            answer_lines.append(
                _format_tool_answer(
                    "paper_validation_tool",
                    output.result.get("summary", "Scheduler status retrieved."),
                )
            )
        else:
            answer_lines.append(f"Scheduler query failed: {output.error}")

    elif intent is Intent.PAPER_ALERTS_QUERY:
        output = _run_tool(
            "paper_validation_tool",
            {
                "action": "alerts",
                "organization_id": org,
                "user_id": user,
                "strategy_id": org,
            },
        )
        if output.success and output.result:
            answer_lines.append(
                _format_tool_answer(
                    "paper_validation_tool",
                    output.result.get("summary", "Alerts retrieved."),
                )
            )
            alerts = output.result.get("alerts") or []
            for alert in alerts[:3]:
                answer_lines.append(f"- [{alert.get('severity')}] {alert.get('message')}")
        else:
            answer_lines.append(f"Alerts query failed: {output.error}")

    elif intent is Intent.ALERT_DELIVERY_QUERY:
        action = "alert_delivery_status"
        lowered = agent.message.lower()
        if "deliver pending" in lowered or "deliver alert" in lowered:
            action = "deliver_pending"
        elif "why" in lowered and "not delivered" in lowered:
            action = "alert_delivery_reason"
        elif "external" in lowered and "enabled" in lowered:
            action = "alert_delivery_status"
        output = _run_tool(
            "paper_validation_tool",
            {
                "action": action,
                "organization_id": org,
                "user_id": user,
                "strategy_id": org,
                "user_message": agent.message,
            },
        )
        if output.success and output.result:
            answer_lines.append(
                _format_tool_answer(
                    "paper_validation_tool",
                    output.result.get("summary", "Alert delivery status retrieved."),
                )
            )
            delivery = output.result.get("delivery_status")
            if delivery:
                answer_lines.append(
                    f"External delivery enabled: {delivery.get('effective_external_enabled')}."
                )
        else:
            answer_lines.append(f"Alert delivery query failed: {output.error}")

    elif intent is Intent.MARKET_WATCHER_QUERY:
        action = "market_watcher_status"
        lowered = agent.message.lower()
        if "scan" in lowered and "watcher" in lowered:
            action = "market_watcher_scan"
        elif "fresh" in lowered or "observation" in lowered or "setup signal" in lowered:
            action = "market_watcher_observations"
        elif "watched" in lowered or "watching" in lowered:
            action = "market_watcher_status"
        output = _run_tool(
            "paper_validation_tool",
            {
                "action": action,
                "organization_id": org,
                "user_id": user,
                "strategy_id": org,
                "user_message": agent.message,
            },
        )
        if output.success and output.result:
            answer_lines.append(
                _format_tool_answer(
                    "paper_validation_tool",
                    output.result.get("summary", "Market watcher status retrieved."),
                )
            )
            observations = output.result.get("observations") or []
            for obs in observations[:3]:
                answer_lines.append(
                    f"- {obs.get('symbol')}: {obs.get('status')} ({obs.get('data_freshness')})"
                )
        else:
            answer_lines.append(f"Market watcher query failed: {output.error}")

    elif intent is Intent.MARKET_WATCHER_BRIDGE_QUERY:
        action = "bridge_status"
        lowered = agent.message.lower()
        if "tick" in lowered and "bridge" in lowered:
            action = "bridge_tick"
        elif "skip" in lowered and ("bridge" in lowered or "strateg" in lowered):
            action = "bridge_skip_reason"
        elif "trigger" in lowered and "scan" in lowered:
            action = "bridge_triggered_scans"
        elif "linked" in lowered or "observation" in lowered:
            action = "bridge_linked_runs"
        elif "history" in lowered or "decision" in lowered:
            action = "bridge_history"
        elif "enabled" in lowered:
            action = "bridge_status"
        output = _run_tool(
            "paper_validation_tool",
            {
                "action": action,
                "organization_id": org,
                "user_id": user,
                "strategy_id": org,
                "user_message": agent.message,
            },
        )
        if output.success and output.result:
            answer_lines.append(
                _format_tool_answer(
                    "paper_validation_tool",
                    output.result.get("summary", "Bridge status retrieved."),
                )
            )
            for item in (output.result.get("bridge_decisions") or [])[:3]:
                answer_lines.append(
                    f"- {item.get('symbol')}: {item.get('decision')} — {item.get('reason')}"
                )
        else:
            answer_lines.append(f"Market watcher bridge query failed: {output.error}")

    elif intent is Intent.STRATEGY_DISCUSSION:
        output = _run_tool(
            "strategy_discussion_context_tool",
            {
                "organization_id": org,
                "user_id": user,
                "strategy_id": _bound_strategy_id(agent),
                "query": agent.message,
                "user_message": agent.message,
            },
        )
        if output.success and output.result:
            answer_lines.append(
                _format_tool_answer(
                    "strategy_discussion_context_tool",
                    "Read-only context loaded from existing authorities "
                    "(strategy, versions, journal, lessons, learning stats, RAG).",
                )
            )
            strategy = output.result.get("strategy") or {}
            if strategy:
                answer_lines.append(
                    f"Bound strategy: {strategy.get('name')} v{strategy.get('current_version')}."
                )
            limitations = output.result.get("limitations") or []
            answer_lines.extend(limitations[:2])
        else:
            answer_lines.append(f"Strategy context failed: {output.error}")

    elif intent is Intent.STRATEGY_PROPOSAL_CONFIRM:
        from app.agents.confirmation_identity import (
            identity_matches_caller,
            presented_confirmation_identity_from_history,
        )
        from app.agents.mutation_policy import (
            confirmation_authorizes_mutation,
            confirmed_proposal_id,
            is_confirmation_only_message,
        )

        authorized_confirm = confirmation_authorizes_mutation(agent.message)
        presented = presented_confirmation_identity_from_history(agent.conversation_history)
        named_id = confirmed_proposal_id(agent.message) if authorized_confirm else None
        proposal_id = named_id
        if (
            authorized_confirm
            and proposal_id is None
            and is_confirmation_only_message(agent.message)
            and presented is not None
        ):
            proposal_id = str(presented.proposal_id)
        if not authorized_confirm:
            answer_lines.append(
                "Quoted, retrieved, or mixed instructions do not confirm strategy drafts."
            )
        elif agent.conversation_id is None or proposal_id is None or presented is None:
            answer_lines.append(
                "Confirm the proposal that was presented. Bare confirmation cannot "
                "authorize whichever draft is currently pending."
            )
        elif not identity_matches_caller(
            presented,
            conversation_id=agent.conversation_id,
            organization_id=agent.organization_id or uuid.UUID(org),
            user_id=agent.user_id or uuid.UUID(user),
        ):
            answer_lines.append(
                "Presented confirmation identity does not match this conversation, "
                "organization, or user."
            )
        elif named_id is not None and named_id != str(presented.proposal_id):
            answer_lines.append(
                "Confirmation proposal id does not match the proposal that was presented."
            )
        else:
            output = _run_tool(
                "strategy_proposal_tool",
                {
                    "action": "confirm",
                    "organization_id": org,
                    "user_id": user,
                    "conversation_id": str(agent.conversation_id),
                    "proposal_id": str(presented.proposal_id),
                    "user_message": agent.message,
                    "expected_content_hash": presented.content_hash,
                    "expected_parent_version_id": (
                        str(presented.parent_version_id)
                        if presented.parent_version_id is not None
                        else None
                    ),
                    "expected_target_strategy_id": (
                        str(presented.target_strategy_id)
                        if presented.target_strategy_id is not None
                        else None
                    ),
                    "expected_organization_id": str(presented.organization_id),
                    "expected_user_id": str(presented.user_id),
                    "expected_conversation_id": str(presented.conversation_id),
                },
            )
            if output.success and output.result:
                answer_lines.append(
                    _format_tool_answer(
                        "strategy_proposal_tool",
                        "Proposal confirmed. A new strategy version was stored. "
                        "It is not compiled or activated.",
                    )
                )
                if output.result.get("resulting_version_id"):
                    answer_lines.append(f"Version id: {output.result.get('resulting_version_id')}")
            else:
                answer_lines.append(f"Proposal confirmation failed: {output.error}")

    elif intent is Intent.STRATEGY_PROPOSAL_REJECT:
        from app.agents.confirmation_identity import (
            identity_matches_caller,
            presented_confirmation_identity_from_history,
        )
        from app.agents.mutation_policy import (
            is_rejection_only_message,
            rejected_proposal_id,
            rejection_authorizes_mutation,
        )

        authorized_reject = rejection_authorizes_mutation(agent.message)
        presented = presented_confirmation_identity_from_history(agent.conversation_history)
        named_id = rejected_proposal_id(agent.message) if authorized_reject else None
        proposal_id = named_id
        if (
            authorized_reject
            and proposal_id is None
            and is_rejection_only_message(agent.message)
            and presented is not None
        ):
            proposal_id = str(presented.proposal_id)
        if not authorized_reject:
            answer_lines.append(
                "Quoted, retrieved, or mixed instructions do not reject strategy drafts."
            )
        elif agent.conversation_id is None or proposal_id is None or presented is None:
            answer_lines.append(
                "Reject the proposal that was presented. Bare rejection cannot "
                "target whichever draft is currently pending."
            )
        elif not identity_matches_caller(
            presented,
            conversation_id=agent.conversation_id,
            organization_id=agent.organization_id or uuid.UUID(org),
            user_id=agent.user_id or uuid.UUID(user),
        ):
            answer_lines.append(
                "Presented confirmation identity does not match this conversation, "
                "organization, or user."
            )
        else:
            output = _run_tool(
                "strategy_proposal_tool",
                {
                    "action": "reject",
                    "organization_id": org,
                    "user_id": user,
                    "conversation_id": str(agent.conversation_id),
                    "proposal_id": str(presented.proposal_id),
                    "user_message": agent.message,
                },
            )
            if output.success and output.result:
                answer_lines.append(
                    _format_tool_answer(
                        "strategy_proposal_tool",
                        "Proposal rejected. Strategy versions were not changed.",
                    )
                )
            else:
                answer_lines.append(f"Proposal rejection failed: {output.error}")

    pending_proposal_id = agent.pending_proposal_id
    if intent in {Intent.STRATEGY_PROPOSAL_CONFIRM, Intent.STRATEGY_PROPOSAL_REJECT}:
        from app.agents.confirmation_identity import presented_confirmation_identity_from_history

        presented = presented_confirmation_identity_from_history(agent.conversation_history)
        if presented is not None:
            pending_proposal_id = presented.proposal_id

    final_answer = "\n".join(answer_lines) if answer_lines else "No strategy workflow result."
    final_answer += "\nLLM narrative cannot override deterministic risk, sizing, or approval facts."

    return patch_state(
        state,
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "tool_calls": tool_calls,
            "tool_outputs": tool_outputs,
            "audit_events": audit_events,
            "final_answer": final_answer,
            "pending_proposal_id": pending_proposal_id,
        },
    )


def trading_analytics_retrieval(state: dict, runtime: AgentRuntime) -> dict:
    """Load deterministic analytics via tool boundary (no direct DB in nodes)."""
    agent = parse_state(state)
    if runtime.session is None or agent.organization_id is None or agent.user_id is None:
        return patch_state(state, {})

    output = runtime.tool_registry.execute(
        "analytics_summary_tool",
        {
            "organization_id": str(agent.organization_id),
            "user_id": str(agent.user_id),
        },
    )
    tool_audit = runtime.observability.emit_tool_called(
        agent,
        tool_name="analytics_summary_tool",
        success=output.success,
        latency_ms=output.latency_ms,
    )
    updates: dict = {
        "tool_outputs": [*agent.tool_outputs, dump_partial(output)],
        "audit_events": [*agent.audit_events, tool_audit],
    }
    if output.success and output.result:
        review = output.result.get("trade_review") or {}
        discipline = output.result.get("discipline_summary") or {}
        setups = output.result.get("setup_statistics") or []
        mistakes = output.result.get("repeated_mistakes") or []
        summary_parts = [
            f"Discipline score {discipline.get('score', 'n/a')} ({discipline.get('grade', '?')}).",
            f"Journaled trades: {review.get('total_journaled_trades', 0)}.",
        ]
        if setups:
            top = max(setups, key=lambda s: s.get("paper_trade_count", 0))
            if top:
                summary_parts.append(
                    f"Most active setup: {top.get('setup_type')} "
                    f"({top.get('paper_trade_count', 0)} paper trades)."
                )
        if mistakes:
            summary_parts.append(f"Repeated mistakes: {', '.join(mistakes[:5])}.")
        updates["final_answer"] = " ".join(summary_parts)
    return patch_state(state, updates)


def context_retrieval(state: dict, runtime: AgentRuntime) -> dict:
    """RAG via tool boundary — rules, lessons, and policy context only."""
    agent = parse_state(state)
    from app.schemas.common import DocumentSourceType

    source_types = [
        DocumentSourceType.TRADING_PLAYBOOK,
        DocumentSourceType.RISK_POLICY,
        DocumentSourceType.TRADE_JOURNAL,
        DocumentSourceType.REVIEW_NOTE,
    ]
    lowered = agent.message.lower()
    if any(w in lowered for w in ("mistake", "lesson", "improve", "emotion", "journal")):
        source_types = [
            DocumentSourceType.TRADE_JOURNAL,
            DocumentSourceType.MISTAKES_DATABASE,
            DocumentSourceType.REVIEW_NOTE,
            DocumentSourceType.TRADING_PLAYBOOK,
        ]
    wants_strategy_template = agent.bound_strategy_id is not None or any(
        token in lowered for token in ("strateg", "pattern spec", "structured rule")
    )
    if wants_strategy_template and DocumentSourceType.STRATEGY_TEMPLATE not in source_types:
        source_types.append(DocumentSourceType.STRATEGY_TEMPLATE)
    tool_input = ToolInput(
        tool_name="rag_retriever",
        arguments={
            "query": agent.message[:500],
            "organization_id": str(agent.organization_id) if agent.organization_id else None,
            "user_id": str(agent.user_id) if agent.user_id else None,
            "source_types": [st.value for st in source_types],
        },
    )
    output = runtime.tool_registry.execute("rag_retriever", tool_input.arguments)
    tool_audit = runtime.observability.emit_tool_called(
        agent,
        tool_name="rag_retriever",
        success=output.success,
        latency_ms=output.latency_ms,
        used_fallback=output.used_fallback,
    )
    citations: list[dict] = list(agent.citations)
    retrieved: list[dict] = list(agent.retrieved_context)
    if output.success and output.result:
        raw_citations = output.result.get("citations", [])
        for raw in raw_citations:
            citation = Citation.model_validate(raw)
            dumped = dump_partial(citation)
            citations.append(dumped)
            retrieved.append(dumped)
    return patch_state(
        state,
        {
            "tool_calls": [*agent.tool_calls, dump_partial(tool_input)],
            "tool_outputs": [*agent.tool_outputs, dump_partial(output)],
            "retrieved_context": retrieved,
            "citations": citations,
            "audit_events": [*agent.audit_events, tool_audit],
        },
    )


def _snapshot_gaps(snap: object) -> list[str]:
    gaps: list[str] = []
    if not getattr(snap, "funding_rate_available", True):
        gaps.append("funding_rate")
    if not getattr(snap, "open_interest_available", True):
        gaps.append("open_interest")
    return gaps


def _merge_gaps(existing: list[str], new: list[str]) -> list[str]:
    return [*existing, *[g for g in new if g not in existing]]


def data_gap_note(agent: AgentState) -> str:
    if not agent.market_data_gaps:
        return ""
    fields = ", ".join(g.replace("_", " ") for g in agent.market_data_gaps)
    return (
        f"\nData gap: {fields} unavailable (futures market data provider is rate limited); "
        "analysis proceeds without it."
    )


def _with_gap_note(answer: str, agent: AgentState) -> str:
    note = data_gap_note(agent)
    if not note or note.strip() in answer:
        return answer
    return answer + note


def market_context_retrieval(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    symbol = agent.symbol or _extract_symbol(agent.message) or "BTCUSDT"
    timeframe = agent.timeframe or Timeframe.H4
    output = runtime.tool_registry.execute(
        "market_data",
        {"symbol": symbol, "timeframe": timeframe.value, "exchange": "binance"},
    )
    quality = "missing"
    snapshot = None
    gaps: list[str] = list(agent.market_data_gaps)
    if runtime.market_data_service is not None:
        snap = runtime.market_data_service.get_snapshot(
            symbol, timeframe, exchange="binance", degrade_on_rate_limit=True
        )
        gaps = _merge_gaps(gaps, _snapshot_gaps(snap))
        if snap.meta.fallback_used or not snap.meta.is_live:
            quality = "mock"
        elif snap.meta.is_stale:
            quality = "stale"
        else:
            quality = "live"
        latest = snap.latest_bar
        if latest is not None:
            snapshot = MarketSnapshot(
                symbol=symbol,
                timeframe=timeframe,
                open=latest.open,
                high=latest.high,
                low=latest.low,
                close=latest.close,
                volume=latest.volume,
                funding_rate=snap.funding_rate,
                timestamp=latest.timestamp,
            )
    if snapshot is None:
        close = Decimal("60000")
        if output.success and output.result and "close" in output.result:
            close = Decimal(str(output.result["close"]))
        snapshot = MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            open=close * Decimal("0.99"),
            high=close * Decimal("1.01"),
            low=close * Decimal("0.98"),
            close=close,
            volume=Decimal("50000000"),
            funding_rate=Decimal("0.0001"),
            timestamp=datetime.now(UTC),
        )
        quality = "mock"
    return patch_state(
        state,
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "market_context": dump_partial(snapshot),
            "market_data_quality": quality,
            "market_data_gaps": gaps,
            "tool_outputs": [*agent.tool_outputs, dump_partial(output)],
        },
    )


def indicator_calculation(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    symbol = agent.symbol or "BTCUSDT"
    timeframe = agent.timeframe or Timeframe.H4
    output = runtime.tool_registry.execute(
        "indicator",
        {"symbol": symbol, "timeframe": timeframe.value, "exchange": "binance"},
    )
    indicators = None
    gaps: list[str] = list(agent.market_data_gaps)
    if runtime.market_data_service is not None:
        snap = runtime.market_data_service.get_snapshot(
            symbol, timeframe, exchange="binance", degrade_on_rate_limit=True
        )
        gaps = _merge_gaps(gaps, _snapshot_gaps(snap))
        ohlcv = runtime.market_data_service.get_ohlcv(symbol, timeframe, exchange="binance")
        funding_rate = snap.funding_rate
        from app.providers.market_data import OHLCVBar

        bars = [
            OHLCVBar(
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                timestamp=b.timestamp,
            )
            for b in ohlcv.bars
        ]
        if bars:
            from app.services.indicator_service import IndicatorService

            indicators = IndicatorService().calculate(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars,
                funding_rate=funding_rate,
            )
    if indicators is None:
        now = datetime.now(UTC)
        close = agent.market_context.close if agent.market_context else Decimal("60000")
        indicators = IndicatorContext(
            symbol=symbol,
            timeframe=timeframe,
            rsi=55.0,
            ema_fast=close * Decimal("0.995"),
            ema_slow=close * Decimal("0.98"),
            funding_rate=Decimal("0.0001"),
            timestamp=now,
        )
    return patch_state(
        state,
        {
            "indicator_context": dump_partial(indicators),
            "market_data_gaps": gaps,
            "tool_outputs": [*agent.tool_outputs, dump_partial(output)],
        },
    )


def strategy_module_execution(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    symbol = agent.symbol or "BTCUSDT"
    timeframe = agent.timeframe or Timeframe.H4
    close = agent.market_context.close if agent.market_context else Decimal("60000")
    ema_fast = agent.indicator_context.ema_fast if agent.indicator_context else None
    ema_slow = agent.indicator_context.ema_slow if agent.indicator_context else None
    data = StrategyEvaluationInput(
        symbol=symbol,
        timeframe=timeframe,
        close=close,
        volume=Decimal("1000000"),
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        htf_trend=TradeDirection.LONG,
        liquidity_sweep_detected="sweep" in agent.message.lower(),
        data_is_live=agent.market_data_quality == "live",
        data_is_stale=agent.market_data_quality == "stale",
        data_fallback_used=agent.market_data_quality in {"mock", "missing", None},
    )
    signals = []
    from app.strategies.confidence import adjust_confidence_for_data_quality

    for strategy_id in (StrategyId.HTF_TREND_PULLBACK, StrategyId.LIQUIDITY_SWEEP_REVERSAL):
        signal = runtime.strategy_service.evaluate(strategy_id, data)
        if signal is not None:
            adjusted = signal.model_copy(
                update={"confidence": adjust_confidence_for_data_quality(signal.confidence, data)}
            )
            evidence = list(adjusted.evidence)
            if agent.market_data_quality != "live":
                evidence.append(f"Market data quality: {agent.market_data_quality or 'unknown'}")
            adjusted = adjusted.model_copy(update={"evidence": evidence})
            signals.append(dump_partial(adjusted))
    tool_out = runtime.tool_registry.execute(
        "strategy_evaluator",
        {
            "strategy_id": StrategyId.HTF_TREND_PULLBACK.value,
            "symbol": symbol,
            "timeframe": timeframe.value,
            "close": str(close),
            "volume": "1000000",
            "htf_trend": "long",
        },
    )
    return patch_state(
        state,
        {
            "strategy_signals": signals,
            "tool_outputs": [*agent.tool_outputs, dump_partial(tool_out)],
        },
    )


def trade_proposal_generation(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    decision = agent.intent_decision
    if decision is not None and decision.operation_class is not OperationClass.PLAN:
        return patch_state(state, {})
    if agent.intent is not Intent.PLAN_TRADE:
        return patch_state(state, {})
    if agent.user_id is None or agent.organization_id is None:
        return patch_state(state, {})

    symbol = agent.symbol or "BTCUSDT"
    timeframe = agent.timeframe or Timeframe.H4
    close = agent.market_context.close if agent.market_context else Decimal("60000")
    no_stop = "[test_no_stop]" in agent.message.lower()
    stop = None if no_stop else close * Decimal("0.97")
    strategy_id = StrategyId.HTF_TREND_PULLBACK
    if agent.strategy_signals:
        strategy_id = agent.strategy_signals[0].strategy_id

    confidence = 0.45 if "[test_low_confidence]" in agent.message.lower() else 0.72
    if agent.market_data_quality in {"mock", "stale", "missing", None}:
        confidence = max(0.35, confidence - 0.12)
    data_note = ""
    if agent.market_data_quality and agent.market_data_quality != "live":
        data_note = f" Evidence based on {agent.market_data_quality} market data."
    exit = ExitCriteria(
        invalidation="Close below stop or HTF structure breaks.",
        stop_loss=stop if stop is not None else close * Decimal("0.97"),
        take_profits=[TakeProfitLevel(price=close * Decimal("1.02"), size_fraction=0.5)],
    )
    if no_stop:
        exit = ExitCriteria(
            invalidation="N/A",
            stop_loss=close,
            take_profits=[TakeProfitLevel(price=close * Decimal("1.01"), size_fraction=1.0)],
        )
        # Risk engine will block missing stop on check — pass stop_loss anyway for schema;
        # RiskCheckRequest uses separate stop_loss field.

    proposal = TradeProposal(
        organization_id=agent.organization_id,
        user_id=agent.user_id,
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        direction=TradeDirection.LONG,
        entry_price=close,
        position_size=Decimal("0.005"),
        leverage=Decimal("50") if "[test_high_risk]" in agent.message.lower() else Decimal("3"),
        exit=exit,
        confidence=confidence,
        risk_level=RiskSeverity.MEDIUM,
        rationale=f"Deterministic scaffold proposal from strategy context.{data_note}",
        status=ProposalStatus.PENDING_APPROVAL,
        created_at=datetime.now(UTC),
    )
    proposal_audit = runtime.observability.emit_trade_proposal_created(agent, symbol=symbol)
    return patch_state(
        state,
        {
            "trade_proposal": dump_partial(proposal),
            "confidence": confidence,
            "risk_level": RiskSeverity.MEDIUM,
            "audit_events": [*agent.audit_events, proposal_audit],
        },
    )


def deterministic_risk_gate(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    if agent.trade_proposal is None:
        return patch_state(state, {})

    p = agent.trade_proposal
    no_stop = "[test_no_stop]" in agent.message.lower()
    request = RiskCheckRequest(
        symbol=p.symbol,
        direction=p.direction,
        strategy_id=p.strategy_id,
        entry_price=p.entry_price,
        stop_loss=None if no_stop else p.exit.stop_loss,
        position_size=p.position_size,
        leverage=p.leverage,
        account_equity=Decimal("10000"),
        volume_24h=Decimal("50000000"),
        is_countertrend=False,
    )
    tool_out = runtime.tool_registry.execute(
        "risk_checker", {"request": request.model_dump(mode="json")}
    )
    kill_switch_active = _resolve_kill_switch_active(runtime, agent.organization_id)
    result = runtime.risk_service.check(
        request,
        context=RiskEvaluationContext(
            is_weekend=False,
            kill_switch_active=kill_switch_active,
        ),
    )
    severity = result.severity
    risk_audit = runtime.observability.emit_risk_checked(
        agent,
        action=result.action,
        rules=len(result.triggered_rules),
    )
    return patch_state(
        state,
        {
            "risk_result": dump_partial(result),
            "risk_level": severity,
            "tool_outputs": [*agent.tool_outputs, dump_partial(tool_out)],
            "audit_events": [*agent.audit_events, risk_audit],
        },
    )


def approval_decision(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    if agent.risk_result and agent.risk_result.action is RiskAction.BLOCK:
        return patch_state(
            state,
            {
                "approval_required": False,
                "approval_reason": "Blocked by risk engine; approval not applicable.",
            },
        )

    needs_approval = False
    reason = None
    if agent.intent is Intent.EXECUTE_PAPER_PLAN:
        needs_approval = True
        reason = "Paper execution requires an explicit consumable authorization."
    if agent.intent in {Intent.APPROVE, Intent.REJECT, Intent.SKIP}:
        # Approval transitions never execute and never infer execution.
        return patch_state(
            state,
            {
                "approval_required": agent.intent is Intent.APPROVE,
                "approval_reason": (
                    "Approve creates authorization only; it does not execute."
                    if agent.intent is Intent.APPROVE
                    else "Reject/skip cannot authorize or execute."
                ),
            },
        )
    if agent.trade_proposal is not None:
        if agent.confidence is not None and agent.confidence < 0.55:
            needs_approval = True
            reason = "Low confidence high-impact trade proposal."
        if agent.risk_result and agent.risk_result.approval_required:
            needs_approval = True
            reason = reason or "Risk engine requires human approval."
    if agent.risk_result and agent.risk_result.action is RiskAction.WARN:
        needs_approval = True
        reason = reason or "Risk warnings present."

    updates: dict = {"approval_required": needs_approval, "approval_reason": reason}
    if needs_approval:
        updates["audit_events"] = [
            *agent.audit_events,
            runtime.observability.emit_approval_required(agent, reason=reason),
        ]
    return patch_state(state, updates)


def tool_execution_if_allowed(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    bypass = runtime.guardrails.check_tool_bypass_attempt(GuardrailInput.from_agent_state(agent))
    if bypass.blocked:
        return patch_state(
            state,
            build_guardrail_updates(agent, bypass, runtime, audit_reason="tool_bypass"),
        )

    if runtime.real_trading_allowed:
        return patch_state(
            state,
            {
                "final_answer": "Real exchange execution is disabled.",
                "safety_verdict": SafetyVerdict.BLOCK,
            },
        )

    if agent.risk_result and agent.risk_result.action is RiskAction.BLOCK:
        return patch_state(state, {})

    if agent.intent is not Intent.EXECUTE_PAPER_PLAN:
        return patch_state(state, {})

    paper_args = {
        "mode": "paper",
        "symbol": agent.symbol or "BTCUSDT",
        "note": "Phase 1 fail-closed agent execution tool",
    }
    output = runtime.tool_registry.execute("paper_execution", paper_args)
    paper_audit = runtime.observability.emit_paper_execution_attempted(
        agent,
        success=output.success,
        error=output.error,
    )
    return patch_state(
        state,
        {
            "tool_outputs": [*agent.tool_outputs, dump_partial(output)],
            "audit_events": [*agent.audit_events, paper_audit],
        },
    )


def memory_update(state: dict, runtime: AgentRuntime) -> dict:
    """NON_DOMAIN_MEMORY only: in-process citation note, no domain persistence."""
    agent = parse_state(state)
    summary = f"intent={agent.intent.value}; class={agent.message_class.value}"
    if agent.intent_decision is not None:
        summary = (
            f"intent={agent.intent_decision.intent.value}; "
            f"op={agent.intent_decision.operation_class.value}"
        )
    citation = dump_partial(
        Citation(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            source_type=DocumentSourceType.TRADING_PLAYBOOK,
            snippet=f"Session memory: {summary}",
        )
    )
    return patch_state(state, {"citations": [*agent.citations, citation]})


def usage_tracking(state: dict, runtime: AgentRuntime) -> dict:
    """Record graph-level usage from actual model calls only.

    No completion is made merely to meter a request. If no model ran, the
    envelope is a labelled deterministic estimate (not provider usage).
    """
    agent = parse_state(state)
    attempts = (
        runtime.model_router.attempts_for(agent.request_id)
        if runtime.model_router is not None
        else []
    )
    actual = [row for row in attempts if row.telemetry_durable]
    if actual:
        input_tokens = sum(row.input_tokens for row in actual)
        output_tokens = sum(row.output_tokens for row in actual)
        fallback_used = any(row.fallback_used for row in attempts) or any(
            o.used_fallback for o in agent.tool_outputs
        )
        latency_ms = sum(row.latency_ms or 0 for row in actual) or None
        model = actual[-1].resolved_model
        cost_source = actual[-1].cost_source
        provider_name = actual[-1].provider
        feature = "agent_chat"
    else:
        # Labelled capacity estimate only. Never persist as provider usage.
        input_tokens = max(len(agent.message) // 4, 1)
        output_tokens = 0
        fallback_used = any(o.used_fallback for o in agent.tool_outputs)
        latency_ms = sum(o.latency_ms or 0 for o in agent.tool_outputs) or None
        model = "none"
        provider_name = "none"
        cost_source = CostSource.UNAVAILABLE
        feature = "capacity_estimate"

    usage = UsageEvent(
        organization_id=agent.organization_id,
        user_id=agent.user_id,
        request_id=agent.request_id,
        feature=feature,
        model=model,
        provider=provider_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        tool_calls=len(agent.tool_calls),
        fallback_used=fallback_used,
        latency_ms=latency_ms,
        timestamp=datetime.now(UTC),
        cost_source=cost_source,
        cost_is_placeholder=cost_source is not CostSource.PROVIDER_REPORTED,
    )
    return patch_state(state, {"usage_metadata": dump_partial(usage)})


def final_response(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    if agent.final_answer and agent.analysis_detail is not None:
        return patch_state(state, {})

    if agent.final_answer and agent.analysis_detail is None:
        # Preserve guardrail-set answers without overwriting structured detail.
        detail = build_trading_analysis(agent, runtime)
        return patch_state(
            state,
            {
                "analysis_detail": dump_partial(detail),
            },
        )

    detail = build_trading_analysis(agent, runtime)
    answer = format_reply_from_analysis(detail)
    if agent.citations:
        answer += f"\nCitations: {len(agent.citations)} reference(s) attached."
    answer = _with_gap_note(answer, agent)
    if FORCE_INVALID_OUTPUT in agent.message.lower():
        answer = "Incomplete trading reply."
    return patch_state(
        state,
        {
            "analysis_detail": dump_partial(detail),
            "final_answer": answer,
        },
    )


def narrative_enhancement(state: dict, runtime: AgentRuntime) -> dict:
    """Optional LLM narrative polish — deterministic analysis remains authoritative."""
    agent = parse_state(state)
    if FORCE_INVALID_OUTPUT in agent.message.lower():
        return patch_state(state, {})
    if agent.intent in {
        Intent.STRATEGY_PROPOSAL_CONFIRM,
        Intent.STRATEGY_PROPOSAL_REJECT,
        Intent.PREPARE_PAPER_TRADE,
        Intent.CONFIRM_PAPER_EXECUTION,
    }:
        return patch_state(state, {})
    if agent.analysis_detail is None or runtime.narrative_service is None:
        return patch_state(state, {})

    analysis = agent.analysis_detail

    # AT-015: enforce agent_narrative quota before any polish LLM call.
    if runtime.quota_service is not None and agent.organization_id is not None:
        narrative_quota = runtime.quota_service.check_feature(
            agent.organization_id,
            "agent_narrative",
            request_id=agent.request_id,
            user_id=agent.user_id,
        )
        if narrative_quota.hard_blocked:
            from app.schemas.narrative import NarrativeMetadata

            fallback = runtime.narrative_service.narrative_from_deterministic(analysis, agent)
            meta = NarrativeMetadata(
                source="deterministic_fallback",
                provider="quota",
                model="none",
                fallback_used=True,
                validation_passed=True,
                latency_ms=None,
            )
            answer = _with_gap_note(format_reply_with_narrative(analysis, fallback, meta), agent)
            return patch_state(
                state,
                {
                    "narrative_detail": dump_partial(fallback),
                    "narrative_metadata": dump_partial(meta),
                    "final_answer": answer,
                },
            )

    def _persist(agent_state: AgentState, *, llm_result: object, feature: str) -> None:
        from app.providers.llm import LLMCompletionResult

        if not isinstance(llm_result, LLMCompletionResult):
            return
        runtime.observability.persist_usage(
            agent_state,
            model=llm_result.model,
            provider=llm_result.provider,
            input_tokens=llm_result.input_tokens,
            output_tokens=llm_result.output_tokens,
            fallback_used=llm_result.fallback_used,
            latency_ms=llm_result.latency_ms,
            feature=feature,
        )

    result = runtime.narrative_service.enhance(
        agent,
        analysis,
        persist_usage=_persist,
    )
    answer = _with_gap_note(
        format_reply_with_narrative(analysis, result.narrative, result.metadata), agent
    )

    updates: dict = {
        "narrative_detail": dump_partial(result.narrative),
        "narrative_metadata": dump_partial(result.metadata),
        "final_answer": answer,
    }
    if result.used_llm and not result.metadata.validation_passed:
        fallback_audit = runtime.observability.emit_narrative_fallback(
            agent,
            provider=result.metadata.provider,
            rules=["narrative_validation_failed"],
        )
        updates["audit_events"] = [*agent.audit_events, fallback_audit]

    if result.usage is not None and agent.usage_metadata is None:
        updates["usage_metadata"] = dump_partial(result.usage)
    return patch_state(state, updates)


def output_validation(state: dict, runtime: AgentRuntime) -> dict:
    agent = parse_state(state)
    if agent.safety_verdict is SafetyVerdict.BLOCK and agent.final_answer:
        return patch_state(state, {})
    if agent.intent in {
        Intent.STRATEGY_PROPOSAL_CONFIRM,
        Intent.STRATEGY_PROPOSAL_REJECT,
        Intent.PREPARE_PAPER_TRADE,
        Intent.CONFIRM_PAPER_EXECUTION,
    }:
        return patch_state(state, {})

    result = runtime.guardrails.validate_output(GuardrailInput.from_agent_state(agent))
    if result.allowed:
        return patch_state(state, {})

    updates: dict = {"final_answer": result.safe_message}
    if result.audit_required:
        updates = build_guardrail_updates(
            agent,
            result,
            runtime,
            audit_reason="output_validation",
            set_final_answer_on_block=True,
        )
    return patch_state(state, updates)


def _extract_symbol(message: str) -> str | None:
    match = re.search(r"\b(BTC|ETH|SOL)[A-Z]*\b", message.upper())
    if match:
        token = match.group(1)
        return f"{token}USDT"
    return None


def _resolve_kill_switch_active(
    runtime: AgentRuntime,
    organization_id: uuid.UUID | None,
) -> bool:
    """Resolve kill-switch for agent risk context (AT-014).

    Global env override applies without a DB session. When organization is known
    but session is absent (unit tests / analysis-only graphs), treat as inactive
    here — paper execution remains fail-closed in ExecutionService. When a
    session exists and storage cannot be evaluated, fail closed.
    """
    if runtime.settings.global_kill_switch_active:
        return True
    if organization_id is None:
        return False
    if runtime.session is None:
        return False
    from app.services.risk.kill_switch import KillSwitchService

    try:
        evaluation = KillSwitchService(
            runtime.session,
            runtime.audit_service,
            runtime.settings,
        ).evaluate(organization_id=organization_id)
    except Exception:
        logger.exception("agent_kill_switch_lookup_failed")
        return True
    return evaluation.blocked
