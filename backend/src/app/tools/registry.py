"""Central tool registry for the LangGraph agent (Slice 7)."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from app.core.config import Settings, get_settings
from app.schemas.common import ToolRiskLevel
from app.schemas.tools import ToolOutput, ToolSpec
from app.services.market_data_service import MarketDataService
from app.services.rag_service import RagService
from app.services.risk_service import RiskService
from app.strategies.registry import get_strategy_registry
from app.tools.base import ToolDefinition
from app.tools.fail_closed import ToolFailureCode, fail_closed


def _unavailable(name: str, reason: str) -> ToolOutput:
    return fail_closed(name, ToolFailureCode.UNAVAILABLE, reason)


_MUTATING_TOOL_ACTIONS: dict[str, frozenset[str]] = {
    "strategy_library_tool": frozenset({"create"}),
    "backtest_tool": frozenset({"run"}),
    "lesson_review_tool": frozenset({"accept", "reject", "create_version_from_lesson"}),
    "risk_settings_tool": frozenset({"update"}),
    "notification_preferences_tool": frozenset({"update", "test"}),
    "strategy_proposal_tool": frozenset({"confirm", "reject"}),
}

_ALWAYS_MUTATING_TOOLS = frozenset(
    {
        "paper_execution",
        "journal_writer",
    }
)


def _funding_execute(args: dict[str, Any], market_data_service: MarketDataService) -> ToolOutput:
    """Read-only funding context from the market-data service. Never a trading signal."""
    from app.schemas.common import Timeframe

    start = time.perf_counter()
    try:
        symbol = str(args.get("symbol", "BTCUSDT"))
        tf = Timeframe(str(args.get("timeframe", "4h")))
        snapshot = market_data_service.get_snapshot(symbol, tf)
        latency = (time.perf_counter() - start) * 1000
        funding = snapshot.funding_rate
        return ToolOutput(
            tool_name="funding",
            success=True,
            result={
                "symbol": symbol,
                "funding_rate": str(funding) if funding is not None else None,
                "source": snapshot.meta.source,
                "is_live": snapshot.meta.is_live,
                "fallback_used": snapshot.meta.fallback_used,
                "not_trading_signal": True,
            },
            latency_ms=latency,
            used_fallback=snapshot.meta.fallback_used,
        )
    except Exception as exc:
        return ToolOutput(tool_name="funding", success=False, error=str(exc))


def _position_reader_execute(args: dict[str, Any], session: Any | None) -> ToolOutput:
    import uuid as _uuid

    from app.services.audit_service import AuditService
    from app.services.position_service import PositionService

    if session is None:
        return _unavailable("position_reader", "Database session required to read positions.")
    try:
        org = args.get("organization_id")
        user = args.get("user_id")
        if org is None or user is None:
            return ToolOutput(
                tool_name="position_reader",
                success=False,
                error="organization_id and user_id are required.",
            )
        service = PositionService(session, AuditService(session))
        items, total = service.list_positions(
            organization_id=_uuid.UUID(str(org)),
            user_id=_uuid.UUID(str(user)),
            limit=20,
        )
        return ToolOutput(
            tool_name="position_reader",
            success=True,
            result={
                "items": [i.model_dump(mode="json") for i in items],
                "total": total,
                "read_only": True,
            },
        )
    except Exception as exc:
        return ToolOutput(tool_name="position_reader", success=False, error=str(exc))


def _paper_execution_execute(_args: dict[str, Any]) -> ToolOutput:
    """Agent paper execution is not an authority path in Phase 1 slices 1-3."""
    return fail_closed(
        "paper_execution",
        ToolFailureCode.NOT_IMPLEMENTED,
        "Agent paper_execution is not wired to ExecutionService. "
        "EXECUTE_PAPER_PLAN persistence is owned by a later Phase 1 slice.",
    )


def _journal_writer_execute(_args: dict[str, Any]) -> ToolOutput:
    return fail_closed(
        "journal_writer",
        ToolFailureCode.NOT_IMPLEMENTED,
        "journal_writer is not an authoritative journal adapter. "
        "Journal writes require confirmed JOURNAL operation class via JournalTradeService.",
    )


def _scenario_simulator_execute(_args: dict[str, Any]) -> ToolOutput:
    return fail_closed(
        "scenario_simulator",
        ToolFailureCode.UNAVAILABLE,
        "scenario_simulator has no authoritative domain implementation.",
    )


def _risk_checker_execute(args: dict[str, Any]) -> ToolOutput:
    from app.schemas.risk import RiskCheckRequest

    start = time.perf_counter()
    try:
        request = RiskCheckRequest.model_validate(args.get("request", args))
        result = RiskService().check(request)
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="risk_checker",
            success=True,
            result=result.model_dump(mode="json"),
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="risk_checker", success=False, error=str(exc))


def _optional_decimal(args: dict[str, Any], key: str) -> Decimal | None:
    from decimal import Decimal

    raw = args.get(key)
    return Decimal(str(raw)) if raw is not None else None


def _strategy_evaluator_execute(args: dict[str, Any]) -> ToolOutput:
    from decimal import Decimal

    from app.schemas.common import StrategyId, Timeframe, TradeDirection
    from app.strategies.base import StrategyEvaluationInput

    start = time.perf_counter()
    try:
        strategy_id = StrategyId(args["strategy_id"])
        module = get_strategy_registry().get(strategy_id)
        if module is None:
            return ToolOutput(
                tool_name="strategy_evaluator",
                success=False,
                error="Unknown strategy",
            )
        htf = args.get("htf_trend")
        stress = args.get("stress_score")
        data = StrategyEvaluationInput(
            symbol=str(args.get("symbol", "BTCUSDT")),
            timeframe=Timeframe(str(args.get("timeframe", "4h"))),
            close=Decimal(str(args.get("close", "60000"))),
            volume=Decimal(str(args.get("volume", "1000000"))),
            funding_rate=_optional_decimal(args, "funding_rate"),
            ema_fast=_optional_decimal(args, "ema_fast"),
            ema_slow=_optional_decimal(args, "ema_slow"),
            htf_trend=TradeDirection(htf) if htf else None,
            liquidity_sweep_detected=bool(args.get("liquidity_sweep_detected", False)),
            momentum_exhausted=bool(args.get("momentum_exhausted", False)),
            at_confluence_level=bool(args.get("at_confluence_level", False)),
            green_day_active=bool(args.get("green_day_active", False)),
            stress_score=int(stress) if stress is not None else None,
            tags=dict(args.get("tags", {})),
        )
        signal = module.evaluate(data)
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="strategy_evaluator",
            success=True,
            result={"signal": signal.model_dump(mode="json") if signal else None},
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="strategy_evaluator", success=False, error=str(exc))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=t.name,
                description=t.description,
                risk_level=t.risk_level,
                requires_approval=t.requires_approval,
                provider_dependencies=list(t.provider_dependencies),
                has_fallback=t.has_fallback,
                enabled=t.enabled,
            )
            for t in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutput:
        from app.core.operation_policy import get_operation_decision
        from app.schemas.agent import OperationClass
        from app.tools.fail_closed import ToolFailureCode, fail_closed

        tool = self._tools.get(name)
        if tool is None:
            return ToolOutput(tool_name=name, success=False, error=f"Unknown tool: {name}")
        if not tool.enabled:
            return ToolOutput(tool_name=name, success=False, error="Tool is disabled.")
        decision = get_operation_decision()
        if decision is not None and decision.operation_class is OperationClass.READ_ONLY:
            action = str(arguments.get("action", "")).strip().lower()
            mutating = name in _ALWAYS_MUTATING_TOOLS or (
                action in _MUTATING_TOOL_ACTIONS.get(name, frozenset())
            )
            if mutating:
                return fail_closed(
                    name,
                    ToolFailureCode.BLOCKED,
                    "READ_ONLY forbids mutation/execution tools.",
                )
        return tool.execute(arguments)


def _rag_retriever_execute(args: dict[str, Any], rag_service: RagService) -> ToolOutput:
    from app.schemas.rag import RagQuery

    start = time.perf_counter()
    try:
        payload = dict(args)
        query_text = str(payload.pop("query", ""))
        query = RagQuery.model_validate({"query": query_text, **payload})
        result = rag_service.search(query)
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="rag_retriever",
            success=True,
            result={
                "chunks": [chunk.model_dump(mode="json") for chunk in result.chunks],
                "citations": [cite.model_dump(mode="json") for cite in result.citations],
                "no_relevant_source": result.no_relevant_source,
                "min_score": result.min_score,
                "context_only": True,
                "not_trading_signal": True,
            },
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="rag_retriever", success=False, error=str(exc))


def _market_data_execute(
    args: dict[str, Any], market_data_service: MarketDataService
) -> ToolOutput:
    from app.schemas.common import Timeframe

    start = time.perf_counter()
    try:
        symbol = str(args.get("symbol", "BTCUSDT"))
        tf = Timeframe(str(args.get("timeframe", "4h")))
        exchange = str(args.get("exchange", "binance"))
        snapshot = market_data_service.get_snapshot(symbol, tf, exchange=exchange)
        latest = snapshot.latest_bar
        latency = (time.perf_counter() - start) * 1000
        close_val = "0"
        if latest is not None:
            close_val = str(latest.close)
        elif snapshot.ticker is not None:
            close_val = str(snapshot.ticker.last_price)
        return ToolOutput(
            tool_name="market_data",
            success=True,
            result={
                "symbol": symbol,
                "timeframe": tf.value,
                "close": close_val,
                "source": snapshot.meta.source,
                "is_live": snapshot.meta.is_live,
                "is_stale": snapshot.meta.is_stale,
                "fallback_used": snapshot.meta.fallback_used,
                "provider_name": snapshot.meta.provider_name,
                "retrieved_at": snapshot.meta.retrieved_at.isoformat(),
            },
            latency_ms=latency,
            used_fallback=snapshot.meta.fallback_used,
        )
    except Exception as exc:
        return ToolOutput(tool_name="market_data", success=False, error=str(exc))


def _indicator_execute(args: dict[str, Any], market_data_service: MarketDataService) -> ToolOutput:
    from app.schemas.common import Timeframe

    start = time.perf_counter()
    try:
        symbol = str(args.get("symbol", "BTCUSDT"))
        tf = Timeframe(str(args.get("timeframe", "4h")))
        exchange = str(args.get("exchange", "binance"))
        ohlcv = market_data_service.get_ohlcv(symbol, tf, exchange=exchange)
        snap = market_data_service.get_snapshot(symbol, tf, exchange=exchange)
        from app.providers.market_data import OHLCVBar
        from app.services.indicator_service import IndicatorService

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
        indicators = IndicatorService().calculate(
            symbol=symbol,
            timeframe=tf,
            bars=bars,
            funding_rate=snap.funding_rate,
        )
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="indicator",
            success=True,
            result={
                **indicators.model_dump(mode="json"),
                "source": ohlcv.meta.source,
                "is_live": ohlcv.meta.is_live,
                "fallback_used": ohlcv.meta.fallback_used,
            },
            latency_ms=latency,
            used_fallback=ohlcv.meta.fallback_used,
        )
    except Exception as exc:
        return ToolOutput(tool_name="indicator", success=False, error=str(exc))


def _strategy_library_execute(args: dict[str, Any], session: Any | None) -> ToolOutput:
    import uuid as _uuid

    from app.schemas.strategy_library import UserStrategyCreate
    from app.services.strategy_library_service import StrategyLibraryService

    start = time.perf_counter()
    if session is None:
        return ToolOutput(
            tool_name="strategy_library_tool", success=False, error="DB session required."
        )
    try:
        action = str(args.get("action", "list"))
        org = _uuid.UUID(str(args["organization_id"]))
        user = _uuid.UUID(str(args["user_id"]))
        service = StrategyLibraryService(session)
        if action == "list":
            items, total = service.list_strategies(organization_id=org, user_id=user, limit=20)
            result = {"items": [i.model_dump(mode="json") for i in items], "total": total}
        elif action == "get":
            sid = _uuid.UUID(str(args["strategy_id"]))
            result = service.get(sid, organization_id=org, user_id=user).model_dump(mode="json")
        elif action == "create":
            blocked = _require_mutation_confirmation(
                "strategy_library_tool", args, action="create a strategy card"
            )
            if blocked is not None:
                return blocked
            create_args = {k: v for k, v in args.items() if k != "action"}
            payload = UserStrategyCreate.model_validate(
                {**create_args, "organization_id": org, "user_id": user}
            )
            result = service.create(payload).model_dump(mode="json")
        else:
            return ToolOutput(
                tool_name="strategy_library_tool", success=False, error="Unknown action."
            )
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="strategy_library_tool", success=True, result=result, latency_ms=latency
        )
    except Exception as exc:
        return ToolOutput(tool_name="strategy_library_tool", success=False, error=str(exc))


def _backtest_tool_execute(args: dict[str, Any], session: Any | None, settings: Any) -> ToolOutput:
    import uuid as _uuid

    from app.schemas.backtest import BacktestAssumptions, BacktestRunCreate
    from app.services.backtest_service import BacktestService

    start = time.perf_counter()
    if session is None:
        return ToolOutput(tool_name="backtest_tool", success=False, error="DB session required.")
    try:
        action = str(args.get("action", "latest_result"))
        org = _uuid.UUID(str(args["organization_id"]))
        user = _uuid.UUID(str(args["user_id"]))
        service = BacktestService(session, settings)

        if action == "run":
            blocked = _require_mutation_confirmation("backtest_tool", args, action="run a backtest")
            if blocked is not None:
                return blocked
            if not args.get("strategy_id"):
                return ToolOutput(
                    tool_name="backtest_tool",
                    success=False,
                    error=(
                        "Explicit strategy_id is required. "
                        "First-listed strategy fallback is disabled."
                    ),
                )
            sid = _uuid.UUID(str(args["strategy_id"]))
            assumptions = BacktestAssumptions.model_validate(args.get("assumptions") or {})
            run = service.create(
                sid,
                BacktestRunCreate(assumptions=assumptions),
                organization_id=org,
                user_id=user,
            )
            result = run.model_dump(mode="json")
        elif action == "latest_result":
            sid = _uuid.UUID(str(args["strategy_id"]))
            items, _ = service.list_for_strategy(sid, organization_id=org, user_id=user, limit=1)
            if not items:
                result = {"message": "No backtest runs found for this strategy."}
            else:
                result = items[0].model_dump(mode="json")
        elif action == "eligibility":
            from app.services.paper_eligibility_service import PaperEligibilityService

            sid = _uuid.UUID(str(args["strategy_id"]))
            report = PaperEligibilityService(session).evaluate(
                sid, organization_id=org, user_id=user
            )
            result = report.model_dump(mode="json")
        else:
            return ToolOutput(tool_name="backtest_tool", success=False, error="Unknown action.")
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="backtest_tool", success=True, result=result, latency_ms=latency
        )
    except Exception as exc:
        return ToolOutput(tool_name="backtest_tool", success=False, error=str(exc))


def _pretrade_analysis_execute(
    args: dict[str, Any], session: Any | None, mds: MarketDataService
) -> ToolOutput:
    import uuid as _uuid

    from app.schemas.pretrade import PreTradeAnalyzeRequest
    from app.services.pretrade_analysis_service import PreTradeAnalysisService

    start = time.perf_counter()
    if session is None:
        return ToolOutput(
            tool_name="pretrade_analysis_tool", success=False, error="DB session required."
        )
    try:
        payload = dict(args)
        payload["organization_id"] = _uuid.UUID(str(payload["organization_id"]))
        payload["user_id"] = _uuid.UUID(str(payload["user_id"]))
        request = PreTradeAnalyzeRequest.model_validate(payload)
        result = PreTradeAnalysisService(session, mds).analyze(request)
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="pretrade_analysis_tool",
            success=True,
            result=result.model_dump(mode="json"),
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="pretrade_analysis_tool", success=False, error=str(exc))


def _position_sizing_execute(args: dict[str, Any]) -> ToolOutput:
    from app.schemas.position_sizing import PositionSizingRequest
    from app.services.position_sizing_service import PositionSizingService

    start = time.perf_counter()
    try:
        request = PositionSizingRequest.model_validate(args.get("request", args))
        result = PositionSizingService().calculate(request)
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="position_sizing_tool",
            success=True,
            result=result.model_dump(mode="json"),
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="position_sizing_tool", success=False, error=str(exc))


def _manual_levels_execute(args: dict[str, Any], session: Any | None) -> ToolOutput:
    import uuid as _uuid

    from app.services.manual_level_service import ManualLevelService

    start = time.perf_counter()
    if session is None:
        return ToolOutput(
            tool_name="manual_levels_tool", success=False, error="DB session required."
        )
    try:
        action = str(args.get("action", "list"))
        org = _uuid.UUID(str(args["organization_id"]))
        user = _uuid.UUID(str(args["user_id"]))
        service = ManualLevelService(session)
        if action == "list":
            items, total = service.list_levels(
                organization_id=org,
                user_id=user,
                symbol=args.get("symbol"),
                exchange=args.get("exchange"),
            )
            result = {"items": [i.model_dump(mode="json") for i in items], "total": total}
        else:
            lid = _uuid.UUID(str(args["level_id"]))
            result = service.get(lid, organization_id=org, user_id=user).model_dump(mode="json")
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="manual_levels_tool", success=True, result=result, latency_ms=latency
        )
    except Exception as exc:
        return ToolOutput(tool_name="manual_levels_tool", success=False, error=str(exc))


def _strategy_testability_execute(args: dict[str, Any], session: Any | None) -> ToolOutput:
    import uuid as _uuid

    from app.services.strategy_testability_service import StrategyTestabilityService

    start = time.perf_counter()
    if session is None:
        return ToolOutput(
            tool_name="strategy_testability_tool", success=False, error="DB session required."
        )
    try:
        org = _uuid.UUID(str(args["organization_id"]))
        user = _uuid.UUID(str(args["user_id"]))
        service = StrategyTestabilityService(session)
        if args.get("strategy_id"):
            result = service.score(
                _uuid.UUID(str(args["strategy_id"])),
                organization_id=org,
                user_id=user,
            )
        else:
            from app.repositories.strategy_library import UserStrategyRepository

            rows, _ = UserStrategyRepository(session).list_scoped(
                organization_id=org, user_id=user, limit=1, offset=0
            )
            if not rows:
                return ToolOutput(
                    tool_name="strategy_testability_tool",
                    success=False,
                    error="No strategies found.",
                )
            result = service.score(rows[0].id, organization_id=org, user_id=user)
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="strategy_testability_tool",
            success=True,
            result=result.model_dump(mode="json"),
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="strategy_testability_tool", success=False, error=str(exc))


def _require_mutation_confirmation(
    tool_name: str, args: dict[str, Any], *, action: str
) -> ToolOutput | None:
    from app.agents.mutation_policy import mutation_allowed

    user_message = str(args.get("user_message", ""))
    if not mutation_allowed(user_message, confirm_arg=bool(args.get("confirm"))):
        return ToolOutput(
            tool_name=tool_name,
            success=False,
            error=(
                f"Explicit confirmation required to {action}. "
                "Questions do not mutate state. Reply with 'I confirm' or confirm=true."
            ),
        )
    return None


def _lesson_review_execute(args: dict[str, Any], session: Any | None) -> ToolOutput:
    import uuid as _uuid

    from app.schemas.common import LessonCandidateStatus
    from app.schemas.lesson import LessonCandidateAccept, LessonCandidateReject
    from app.services.lesson_candidate_service import LessonCandidateService

    start = time.perf_counter()
    if session is None:
        return ToolOutput(
            tool_name="lesson_review_tool", success=False, error="DB session required."
        )
    try:
        org = _uuid.UUID(str(args["organization_id"]))
        user = _uuid.UUID(str(args["user_id"]))
        action = str(args.get("action", "list_pending"))
        service = LessonCandidateService(session)
        summary = ""
        pending_observation = False
        result_payload: dict[str, Any] = {}

        if action == "list_pending":
            items, total = service.list_candidates(
                organization_id=org,
                user_id=user,
                status=LessonCandidateStatus.PENDING_REVIEW,
                limit=10,
            )
            summary = f"{total} lesson(s) pending review."
            result_payload = {
                "items": [i.model_dump(mode="json") for i in items],
                "pending_observation": True,
            }
            pending_observation = True
        elif action == "list_accepted":
            items, total = service.list_accepted(organization_id=org, user_id=user, limit=10)
            summary = f"{total} accepted lesson(s)."
            result_payload = {"items": [i.model_dump(mode="json") for i in items]}
        elif action == "list_early_exit":
            items, total = service.list_accepted(
                organization_id=org, user_id=user, mistake_type="early_exit", limit=10
            )
            summary = f"{total} accepted lesson(s) related to early exits."
            result_payload = {"items": [i.model_dump(mode="json") for i in items]}
        elif action == "list_stop_refusal":
            items, total = service.list_accepted(
                organization_id=org, user_id=user, mistake_type="stop_violation", limit=10
            )
            summary = f"{total} accepted lesson(s) related to stop discipline."
            result_payload = {"items": [i.model_dump(mode="json") for i in items]}
        elif action == "accept":
            blocked = _require_mutation_confirmation(
                "lesson_review_tool", args, action="accept this lesson"
            )
            if blocked is not None:
                return blocked
            lesson_id = args.get("lesson_id")
            if not lesson_id:
                return ToolOutput(
                    tool_name="lesson_review_tool",
                    success=False,
                    error="Provide lesson_id to accept.",
                )
            accepted = service.accept(
                _uuid.UUID(str(lesson_id)),
                LessonCandidateAccept(reviewer_notes="Accepted via agent."),
                organization_id=org,
                user_id=user,
            )
            session.commit()
            summary = f"Lesson accepted: {accepted.lesson_text[:120]}"
            result_payload = accepted.model_dump(mode="json")
        elif action == "reject":
            blocked = _require_mutation_confirmation(
                "lesson_review_tool", args, action="reject this lesson"
            )
            if blocked is not None:
                return blocked
            lesson_id = args.get("lesson_id")
            if not lesson_id:
                return ToolOutput(
                    tool_name="lesson_review_tool",
                    success=False,
                    error="Provide lesson_id to reject.",
                )
            rejected = service.reject(
                _uuid.UUID(str(lesson_id)),
                LessonCandidateReject(reviewer_notes="Rejected via agent."),
                organization_id=org,
                user_id=user,
            )
            session.commit()
            summary = "Lesson rejected — kept for audit trail."
            result_payload = rejected.model_dump(mode="json")
            pending_observation = True
        elif action == "rule_suggest":
            lesson_id = args.get("lesson_id")
            if lesson_id:
                lesson = service.get(_uuid.UUID(str(lesson_id)), organization_id=org, user_id=user)
                proposed = lesson.proposed_rule_update
                if proposed:
                    summary = f"Proposed rule update: {proposed.summary}"
                else:
                    summary = (
                        "No proposed rule on this lesson — review lesson text and Strategy Lab."
                    )
                result_payload = lesson.model_dump(mode="json")
                pending_observation = lesson.status != LessonCandidateStatus.ACCEPTED
            else:
                summary = "Provide a lesson_id to see proposed rule updates."
        elif action == "runner_rule_hint":
            summary = (
                "Add a runner_structure_break exit block in Strategy Lab after partial TP. "
                "Real trading remains disabled — paper only."
            )
        elif action == "list_for_strategy":
            strategy_id = args.get("strategy_id")
            if not strategy_id:
                return ToolOutput(
                    tool_name="lesson_review_tool",
                    success=False,
                    error="Provide strategy_id.",
                )
            items = service.list_for_strategy(
                _uuid.UUID(str(strategy_id)),
                organization_id=org,
                user_id=user,
                status=LessonCandidateStatus.ACCEPTED,
            )
            summary = f"{len(items)} accepted lesson(s) linked to strategy."
            result_payload = {"items": [i.model_dump(mode="json") for i in items]}
        elif action == "list_unresolved_for_strategy":
            strategy_id = args.get("strategy_id")
            if not strategy_id:
                return ToolOutput(
                    tool_name="lesson_review_tool",
                    success=False,
                    error="Provide strategy_id.",
                )
            items = service.list_for_strategy(
                _uuid.UUID(str(strategy_id)),
                organization_id=org,
                user_id=user,
                status=LessonCandidateStatus.PENDING_REVIEW,
            )
            summary = f"{len(items)} unresolved lesson candidate(s) for strategy."
            result_payload = {
                "items": [i.model_dump(mode="json") for i in items],
                "pending_observation": True,
            }
            pending_observation = True
        elif action == "lessons_to_update":
            pending, _ = service.list_candidates(
                organization_id=org,
                user_id=user,
                status=LessonCandidateStatus.PENDING_REVIEW,
                limit=20,
            )
            with_proposals = [p for p in pending if p.proposed_rule_update]
            summary = f"{len(with_proposals)} pending lesson(s) propose strategy rule updates."
            result_payload = {
                "items": [i.model_dump(mode="json") for i in with_proposals],
                "pending_observation": True,
            }
            pending_observation = True
        elif action == "create_version_from_lesson":
            blocked = _require_mutation_confirmation(
                "lesson_review_tool", args, action="create a strategy version from this lesson"
            )
            if blocked is not None:
                return blocked
            lesson_id = args.get("lesson_id")
            if not lesson_id:
                return ToolOutput(
                    tool_name="lesson_review_tool",
                    success=False,
                    error="Provide lesson_id.",
                )
            accepted = service.accept(
                _uuid.UUID(str(lesson_id)),
                LessonCandidateAccept(
                    reviewer_notes="Strategy version created via agent.",
                    create_strategy_version=True,
                ),
                organization_id=org,
                user_id=user,
            )
            session.commit()
            summary = "Accepted lesson and created new strategy version (explicit action)."
            result_payload = accepted.model_dump(mode="json")
        else:
            return ToolOutput(
                tool_name="lesson_review_tool", success=False, error=f"Unknown action: {action}"
            )

        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="lesson_review_tool",
            success=True,
            result={
                "summary": summary,
                "pending_observation": pending_observation,
                **result_payload,
            },
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="lesson_review_tool", success=False, error=str(exc))


def _paper_eligibility_execute(args: dict[str, Any], session: Any | None) -> ToolOutput:
    import uuid as _uuid

    from app.services.paper_eligibility_service import PaperEligibilityService

    start = time.perf_counter()
    if session is None:
        return ToolOutput(
            tool_name="paper_eligibility_tool", success=False, error="DB session required."
        )
    try:
        org = _uuid.UUID(str(args["organization_id"]))
        user = _uuid.UUID(str(args["user_id"]))
        action = str(args.get("action", "evaluate"))
        service = PaperEligibilityService(session)
        strategy_id = args.get("strategy_id")
        if strategy_id is None:
            from app.repositories.strategy_library import UserStrategyRepository

            rows, _ = UserStrategyRepository(session).list_scoped(
                organization_id=org, user_id=user, limit=1, offset=0
            )
            if not rows:
                return ToolOutput(
                    tool_name="paper_eligibility_tool",
                    success=False,
                    error="No strategies found.",
                )
            strategy_id = rows[0].id

        sid = _uuid.UUID(str(strategy_id))
        if action == "blockers":
            report = service.evaluate(sid, organization_id=org, user_id=user)
            summary = (
                f"Status: {report.status.value}. "
                f"{len(report.blockers)} blocker(s). Paper only — no live trading."
            )
            result = {
                "summary": summary,
                "blockers": report.blockers,
                "status": report.status.value,
                "paper_eligible": report.paper_eligible,
            }
        else:
            report = service.evaluate(sid, organization_id=org, user_id=user)
            summary = (
                f"Paper eligibility: {report.status.value}. "
                f"Eligible={report.paper_eligible}. "
                f"Recommendation: {report.recommendation}."
            )
            result = {"summary": summary, **report.model_dump(mode="json")}
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="paper_eligibility_tool",
            success=True,
            result=result,
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="paper_eligibility_tool", success=False, error=str(exc))


def _structure_from_text_execute(args: dict[str, Any]) -> ToolOutput:
    from app.schemas.structured_rules import StructureFromTextRequest
    from app.services.structure_from_text_service import StructureFromTextService

    start = time.perf_counter()
    try:
        result = StructureFromTextService().draft(
            StructureFromTextRequest(text=str(args.get("text", "")))
        )
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="structure_from_text_tool",
            success=True,
            result=result.model_dump(mode="json"),
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="structure_from_text_tool", success=False, error=str(exc))


def _strategy_discussion_context_execute(args: dict[str, Any], session: Any | None) -> ToolOutput:
    import uuid as _uuid

    from app.services.rag_service import build_rag_service
    from app.services.strategy_discussion_context_service import StrategyDiscussionContextService

    start = time.perf_counter()
    if session is None:
        return ToolOutput(
            tool_name="strategy_discussion_context_tool",
            success=False,
            error="DB session required.",
        )
    try:
        org = _uuid.UUID(str(args["organization_id"]))
        user = _uuid.UUID(str(args["user_id"]))
        strategy_raw = args.get("strategy_id")
        strategy_id = _uuid.UUID(str(strategy_raw)) if strategy_raw else None
        from app.core.config import get_settings

        rag = build_rag_service(get_settings(), session)
        result = StrategyDiscussionContextService(session, rag_service=rag).gather(
            organization_id=org,
            user_id=user,
            strategy_id=strategy_id,
            query=str(args.get("query") or args.get("user_message") or ""),
        )
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="strategy_discussion_context_tool",
            success=True,
            result=result.model_dump(mode="json"),
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(
            tool_name="strategy_discussion_context_tool", success=False, error=str(exc)
        )


def _optional_uuid(value: object) -> Any:
    import uuid as _uuid

    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    return _uuid.UUID(text)


def _strategy_proposal_execute(args: dict[str, Any], session: Any | None) -> ToolOutput:
    import uuid as _uuid

    from app.services.conversation_service import ConversationService
    from app.services.strategy_proposal_service import StrategyProposalService

    start = time.perf_counter()
    if session is None:
        return ToolOutput(
            tool_name="strategy_proposal_tool",
            success=False,
            error="DB session required.",
        )
    try:
        org = _uuid.UUID(str(args["organization_id"]))
        user = _uuid.UUID(str(args["user_id"]))
        action = str(args.get("action", "draft"))
        service = StrategyProposalService(session)
        user_message = str(args.get("user_message") or "")
        if action == "confirm":
            proposal_id = _uuid.UUID(str(args["proposal_id"]))
            conversation_id = args.get("conversation_id")
            expected_hash = str(args.get("expected_content_hash") or "").strip().lower()
            expected_org = _optional_uuid(args.get("expected_organization_id"))
            expected_user = _optional_uuid(args.get("expected_user_id"))
            expected_conversation = _optional_uuid(args.get("expected_conversation_id"))
            if conversation_id is None:
                return ToolOutput(
                    tool_name="strategy_proposal_tool",
                    success=False,
                    error="Conversation id is required to confirm a proposal.",
                )
            if (
                len(expected_hash) != 64
                or expected_org is None
                or expected_user is None
                or expected_conversation is None
            ):
                return ToolOutput(
                    tool_name="strategy_proposal_tool",
                    success=False,
                    error=(
                        "Presented confirmation identity is required. Server lookup of "
                        "the pending draft cannot authorize confirmation."
                    ),
                )
            result = service.confirm(
                proposal_id,
                organization_id=org,
                user_id=user,
                confirm_message=user_message,
                confirm_arg=bool(args.get("confirm")),
                conversation_id=_uuid.UUID(str(conversation_id)),
                expected_content_hash=expected_hash,
                expected_parent_version_id=_optional_uuid(args.get("expected_parent_version_id")),
                expected_target_strategy_id=_optional_uuid(args.get("expected_target_strategy_id")),
                expected_organization_id=expected_org,
                expected_user_id=expected_user,
                expected_conversation_id=expected_conversation,
            )
        elif action == "reject":
            proposal_id = _uuid.UUID(str(args["proposal_id"]))
            result = service.reject(
                proposal_id,
                organization_id=org,
                user_id=user,
                confirm_message=user_message,
                confirm_arg=bool(args.get("confirm")),
                conversation_id=_uuid.UUID(str(args["conversation_id"]))
                if args.get("conversation_id")
                else None,
            )
        else:
            conversation_id = _uuid.UUID(str(args["conversation_id"]))
            conversation = ConversationService(session).require(
                conversation_id, organization_id=org, user_id=user
            )
            strategy_raw = args.get("strategy_id")
            result = service.create_draft_from_text(
                conversation,
                text=str(args.get("text") or user_message),
                strategy_id=_uuid.UUID(str(strategy_raw)) if strategy_raw else None,
            )
        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="strategy_proposal_tool",
            success=True,
            result=result.model_dump(mode="json"),
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="strategy_proposal_tool", success=False, error=str(exc))


def _risk_settings_execute(args: dict[str, Any], session: Any | None) -> ToolOutput:
    import uuid as _uuid

    from app.schemas.risk import UserRiskSettingsUpdate
    from app.services.audit_service import AuditService
    from app.services.dashboard.daily_discipline import build_daily_discipline_snapshot
    from app.services.dashboard_summary_service import DashboardSummaryService
    from app.services.risk.settings_service import RiskSettingsService

    start = time.perf_counter()
    if session is None:
        return ToolOutput(
            tool_name="risk_settings_tool",
            success=False,
            error="Database session required for risk settings.",
        )
    try:
        org = _uuid.UUID(str(args["organization_id"]))
        user = _uuid.UUID(str(args["user_id"]))
        action = str(args.get("action", "get"))
        service = RiskSettingsService(session, AuditService(session))

        if action == "get":
            result = service.get(organization_id=org, user_id=user).model_dump(mode="json")
            summary = "Current risk settings loaded from persisted configuration or defaults."
        elif action == "update":
            blocked = _require_mutation_confirmation(
                "risk_settings_tool", args, action="update risk settings"
            )
            if blocked is not None:
                return blocked
            skip_keys = {"action", "organization_id", "user_id", "confirm", "user_message"}
            payload = UserRiskSettingsUpdate.model_validate(
                {k: v for k, v in args.items() if k not in skip_keys}
            )
            updated = service.update(payload, organization_id=org, user_id=user)
            session.commit()
            result = updated.model_dump(mode="json")
            summary = "Risk settings updated. Paper-only discipline guidance applies."
        elif action == "discipline":
            snapshot = build_daily_discipline_snapshot(
                session,
                organization_id=org,
                user_id=user,
                risk_settings=service,
            )
            result = snapshot.model_dump(mode="json")
            summary = (
                f"Discipline status={snapshot.discipline_status}. "
                f"Paper PnL today={snapshot.net_pnl_today_paper}."
            )
        elif action == "discipline_score":
            from app.services.analytics.discipline_score import DisciplineScoreService

            score = DisciplineScoreService(session).compute(
                organization_id=org,
                user_id=user,
            )
            result = score.model_dump(mode="json")
            summary = f"Discipline score={score.score} ({score.grade})."
        elif action == "open_trades":
            summary_service = DashboardSummaryService(session, get_settings())
            open_summary = summary_service._open_paper_trades_summary(org, user)
            result = open_summary.model_dump(mode="json")
            summary = f"{open_summary.total_count} open paper trade(s)."
        elif action == "paper_pnl":
            snapshot = build_daily_discipline_snapshot(
                session,
                organization_id=org,
                user_id=user,
                risk_settings=service,
            )
            result = {
                "realized_pnl_paper": str(snapshot.realized_pnl_today_paper),
                "unrealized_pnl_paper": str(snapshot.unrealized_pnl_paper),
                "net_pnl_today_paper": str(snapshot.net_pnl_today_paper),
                "pnl_sources": {k: str(v) for k, v in snapshot.pnl_sources.items()},
                "limitations": snapshot.limitations,
            }
            summary = f"Paper PnL today={snapshot.net_pnl_today_paper}."
        elif action == "loss_lock_reason":
            snapshot = build_daily_discipline_snapshot(
                session,
                organization_id=org,
                user_id=user,
                risk_settings=service,
            )
            result = {
                "loss_lock_active": snapshot.loss_lock_active,
                "reasons": snapshot.reasons,
                "risk_settings_source": snapshot.risk_settings_source,
            }
            summary = (
                "Daily lock is active."
                if snapshot.loss_lock_active
                else "Daily lock is not active."
            )
        else:
            return ToolOutput(
                tool_name="risk_settings_tool",
                success=False,
                error=f"Unknown action: {action}",
            )

        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="risk_settings_tool",
            success=True,
            result={"summary": summary, **result},
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="risk_settings_tool", success=False, error=str(exc))


def _notification_preferences_execute(args: dict[str, Any], session: Any | None) -> ToolOutput:
    import uuid as _uuid

    from app.schemas.notifications import NotificationPreferencesUpdate
    from app.services.alert_delivery_service import AlertDeliveryService
    from app.services.audit_service import AuditService
    from app.services.notifications.preferences_service import NotificationPreferencesService

    start = time.perf_counter()
    if session is None:
        return ToolOutput(
            tool_name="notification_preferences_tool",
            success=False,
            error="Database session required for notification preferences.",
        )
    try:
        org = _uuid.UUID(str(args["organization_id"]))
        user = _uuid.UUID(str(args["user_id"]))
        action = str(args.get("action", "get"))
        prefs_service = NotificationPreferencesService(session, AuditService(session))
        delivery = AlertDeliveryService(session, preferences_service=prefs_service)

        if action == "get":
            prefs = prefs_service.get(organization_id=org, user_id=user)
            status = delivery.get_status(organization_id=org, user_id=user)
            result = {
                "preferences": prefs.model_dump(mode="json"),
                "provider_status": status.model_dump(mode="json"),
            }
            summary = (
                f"In-app={prefs.in_app_enabled}. "
                f"Webhook user={prefs.webhook_enabled}, telegram user={prefs.telegram_enabled}. "
                f"Min severity={prefs.min_severity.value}."
            )
        elif action == "test":
            blocked = _require_mutation_confirmation(
                "notification_preferences_tool", args, action="send test notification"
            )
            if blocked is not None:
                return blocked
            test = delivery.send_test_notification(organization_id=org, user_id=user)
            session.commit()
            result = test.model_dump(mode="json")
            summary = test.message
        elif action == "update":
            blocked = _require_mutation_confirmation(
                "notification_preferences_tool", args, action="update notification preferences"
            )
            if blocked is not None:
                return blocked
            skip_keys = {"action", "organization_id", "user_id", "confirm", "user_message"}
            payload = NotificationPreferencesUpdate.model_validate(
                {k: v for k, v in args.items() if k not in skip_keys}
            )
            updated = prefs_service.update(payload, organization_id=org, user_id=user)
            session.commit()
            result = updated.model_dump(mode="json")
            summary = "Notification preferences updated. Alerts never execute trades."
        elif action == "telegram_enabled":
            prefs = prefs_service.get(organization_id=org, user_id=user)
            status = delivery.get_status(organization_id=org, user_id=user)
            enabled = prefs.telegram_enabled and status.telegram_enabled
            result = {
                "user_enabled": prefs.telegram_enabled,
                "env_enabled": status.telegram_enabled,
                "configured": status.telegram_configured,
                "effective": enabled and status.telegram_configured,
            }
            summary = (
                f"Telegram alerts user={prefs.telegram_enabled}, env={status.telegram_enabled}."
            )
        elif action == "webhook_enabled":
            prefs = prefs_service.get(organization_id=org, user_id=user)
            status = delivery.get_status(organization_id=org, user_id=user)
            enabled = prefs.webhook_enabled and status.webhook_enabled
            result = {
                "user_enabled": prefs.webhook_enabled,
                "env_enabled": status.webhook_enabled,
                "configured": status.webhook_configured,
                "effective": enabled and status.webhook_configured,
            }
            summary = f"Webhook alerts user={prefs.webhook_enabled}, env={status.webhook_enabled}."
        else:
            return ToolOutput(
                tool_name="notification_preferences_tool",
                success=False,
                error=f"Unknown action: {action}",
            )

        latency = (time.perf_counter() - start) * 1000
        return ToolOutput(
            tool_name="notification_preferences_tool",
            success=True,
            result={"summary": summary, **result},
            latency_ms=latency,
        )
    except Exception as exc:
        return ToolOutput(tool_name="notification_preferences_tool", success=False, error=str(exc))


def build_default_registry(
    _settings: Settings | None = None,
    rag_service: RagService | None = None,
    market_data_service: MarketDataService | None = None,
    db_session: Any | None = None,
) -> ToolRegistry:
    from app.services.rag_service import build_rag_service

    settings = _settings or get_settings()
    rag = rag_service or build_rag_service(settings)
    if market_data_service is None:
        from app.providers.factory import resolve_market_data_provider
        from app.services.indicator_service import IndicatorService
        from app.services.market_cache import MarketDataCache
        from app.services.market_data_service import MarketDataService
        from app.services.strategy_service import StrategyService
        from app.strategies.registry import get_strategy_registry

        market_data_service = MarketDataService(
            resolve_market_data_provider(settings),
            cache=MarketDataCache(settings),
            indicator_service=IndicatorService(),
            strategy_service=StrategyService(registry=get_strategy_registry()),
        )
    mds = market_data_service
    registry = ToolRegistry()
    tools = [
        ToolDefinition(
            name="rag_retriever",
            description="Retrieve playbook and journal context from the knowledge base.",
            risk_level=ToolRiskLevel.READ,
            requires_approval=False,
            provider_dependencies=("mock-embeddings", "qdrant"),
            has_fallback=True,
            enabled=True,
            execute=lambda args: _rag_retriever_execute(args, rag),
        ),
        ToolDefinition(
            name="market_data",
            description="Fetch candles, price, and volume for a symbol.",
            risk_level=ToolRiskLevel.READ,
            requires_approval=False,
            provider_dependencies=("binance-public", "mock-market-data"),
            has_fallback=True,
            enabled=True,
            execute=lambda args: _market_data_execute(args, mds),
        ),
        ToolDefinition(
            name="indicator",
            description="Compute RSI, EMA, MACD, and related indicators.",
            risk_level=ToolRiskLevel.READ,
            requires_approval=False,
            provider_dependencies=("binance-public", "mock-market-data"),
            has_fallback=True,
            enabled=True,
            execute=lambda args: _indicator_execute(args, mds),
        ),
        ToolDefinition(
            name="funding",
            description="Fetch funding rate and crowd imbalance context.",
            risk_level=ToolRiskLevel.READ,
            requires_approval=False,
            provider_dependencies=("mock-market-data",),
            has_fallback=True,
            enabled=True,
            execute=lambda args: _funding_execute(args, mds),
        ),
        ToolDefinition(
            name="risk_checker",
            description="Run the deterministic risk engine on a candidate trade.",
            risk_level=ToolRiskLevel.MEDIUM,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=_risk_checker_execute,
        ),
        ToolDefinition(
            name="strategy_evaluator",
            description="Evaluate a deterministic strategy module for a setup signal.",
            risk_level=ToolRiskLevel.LOW,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=_strategy_evaluator_execute,
        ),
        ToolDefinition(
            name="scenario_simulator",
            description="Simulate outcomes for a proposed plan (no execution).",
            risk_level=ToolRiskLevel.LOW,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=_scenario_simulator_execute,
        ),
        ToolDefinition(
            name="journal_writer",
            description="Persist a trade journal entry.",
            risk_level=ToolRiskLevel.MEDIUM,
            requires_approval=True,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=_journal_writer_execute,
        ),
        ToolDefinition(
            name="position_reader",
            description="Read open positions and exposure for the user.",
            risk_level=ToolRiskLevel.READ,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=lambda args: _position_reader_execute(args, db_session),
        ),
        ToolDefinition(
            name="paper_execution",
            description="Place a paper trade order (no real exchange).",
            risk_level=ToolRiskLevel.SENSITIVE,
            requires_approval=True,
            provider_dependencies=("mock-exchange",),
            has_fallback=False,
            enabled=True,
            execute=_paper_execution_execute,
        ),
        ToolDefinition(
            name="strategy_library_tool",
            description="List, get, or create user strategy cards in the strategy library.",
            risk_level=ToolRiskLevel.LOW,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=lambda args: _strategy_library_execute(args, db_session),
        ),
        ToolDefinition(
            name="pretrade_analysis_tool",
            description="Run deterministic pre-trade analysis for a symbol and setup.",
            risk_level=ToolRiskLevel.LOW,
            requires_approval=False,
            provider_dependencies=("mock-market-data",),
            has_fallback=True,
            enabled=True,
            execute=lambda args: _pretrade_analysis_execute(args, db_session, mds),
        ),
        ToolDefinition(
            name="position_sizing_tool",
            description="Calculate position size, risk/reward, and confidence-adjusted sizing.",
            risk_level=ToolRiskLevel.LOW,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=_position_sizing_execute,
        ),
        ToolDefinition(
            name="manual_levels_tool",
            description="List or get manual chart levels for a symbol.",
            risk_level=ToolRiskLevel.READ,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=lambda args: _manual_levels_execute(args, db_session),
        ),
        ToolDefinition(
            name="strategy_testability_tool",
            description="Score strategy testability and list missing rule fields.",
            risk_level=ToolRiskLevel.READ,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=lambda args: _strategy_testability_execute(args, db_session),
        ),
        ToolDefinition(
            name="structure_from_text_tool",
            description="Draft structured rule blocks from plain English (requires validation).",
            risk_level=ToolRiskLevel.LOW,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=_structure_from_text_execute,
        ),
        ToolDefinition(
            name="strategy_discussion_context_tool",
            description=(
                "Read-only strategy discussion context from existing strategies, versions, "
                "journal, lessons, learning stats, and RAG. Not a memory authority."
            ),
            risk_level=ToolRiskLevel.READ,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=lambda args: _strategy_discussion_context_execute(args, db_session),
        ),
        ToolDefinition(
            name="strategy_proposal_tool",
            description=(
                "Preview or confirm structured strategy proposals. Confirmation is required "
                "to fork a version. Drafts never compile or activate."
            ),
            risk_level=ToolRiskLevel.MEDIUM,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=lambda args: _strategy_proposal_execute(args, db_session),
        ),
        ToolDefinition(
            name="lesson_review_tool",
            description=(
                "List, accept, or reject lesson candidates; "
                "query accepted lessons and rule updates."
            ),
            risk_level=ToolRiskLevel.MEDIUM,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=lambda args: _lesson_review_execute(args, db_session),
        ),
        ToolDefinition(
            name="paper_eligibility_tool",
            description=(
                "Deterministic paper eligibility gates, blockers, and promotion status "
                "(paper only — no live trading)."
            ),
            risk_level=ToolRiskLevel.READ,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=lambda args: _paper_eligibility_execute(args, db_session),
        ),
        ToolDefinition(
            name="backtest_tool",
            description=(
                "Run deterministic backtest v1, fetch latest results, or check paper eligibility."
            ),
            risk_level=ToolRiskLevel.LOW,
            requires_approval=False,
            provider_dependencies=("mock-market-data",),
            has_fallback=True,
            enabled=True,
            execute=lambda args: _backtest_tool_execute(args, db_session, settings),
        ),
        ToolDefinition(
            name="risk_settings_tool",
            description=(
                "Read or update paper risk settings, discipline score, open paper trades, "
                "and daily paper PnL. Updates require explicit confirmation."
            ),
            risk_level=ToolRiskLevel.MEDIUM,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=lambda args: _risk_settings_execute(args, db_session),
        ),
        ToolDefinition(
            name="notification_preferences_tool",
            description=(
                "Read or update notification preferences, check webhook/Telegram status, "
                "or send a safe test notification. Mutations require explicit confirmation."
            ),
            risk_level=ToolRiskLevel.MEDIUM,
            requires_approval=False,
            provider_dependencies=(),
            has_fallback=False,
            enabled=True,
            execute=lambda args: _notification_preferences_execute(args, db_session),
        ),
    ]
    for tool in tools:
        registry.register(tool)
    return registry


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = build_default_registry(get_settings())
    return _registry
