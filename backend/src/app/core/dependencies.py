"""FastAPI dependency-injection providers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.evidence_pipeline.service import CanonicalEvidenceService
from app.market_monitor.factory import build_perpetual_market_monitor
from app.market_monitor.monitor import PerpetualMarketMonitor
from app.market_monitor.service import PerpetualMarketMonitorService
from app.providers.exchange.factory import resolve_exchange_execution_provider
from app.providers.factory import resolve_market_data_provider
from app.providers.registry import ProviderRegistry, get_provider_registry
from app.runtime.canonical import ProductionCanonicalRuntime
from app.services.alert_delivery_service import AlertDeliveryService
from app.services.analytics.facade import TradingAnalyticsFacade
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.backtest_journal_service import BacktestJournalService
from app.services.backtest_service import BacktestService
from app.services.blofin_sync_service import BloFinSyncService
from app.services.coaching import CoachingService
from app.services.dashboard_summary_service import DashboardSummaryService
from app.services.execution_service import ExecutionService
from app.services.historical_candle_service import HistoricalCandleService
from app.services.human_vs_system_service import HumanVsSystemService
from app.services.indicator_service import IndicatorService
from app.services.journal_attachment_service import JournalAttachmentService
from app.services.journal_attachment_storage import get_attachment_storage
from app.services.journal_excursion_replay_service import JournalExcursionReplayService
from app.services.journal_import_service import JournalImportService
from app.services.journal_rag_sync_service import JournalRagSyncService
from app.services.journal_service import JournalService
from app.services.journal_statistics_service import JournalStatisticsService
from app.services.journal_trade_service import JournalTradeService
from app.services.learning_analytics import LearningAnalyticsService
from app.services.lesson_candidate_service import LessonCandidateService
from app.services.loss_acceptance_service import LossAcceptanceService
from app.services.manual_level_service import ManualLevelService
from app.services.market_cache import MarketDataCache
from app.services.market_data_service import MarketDataService
from app.services.market_service import MarketService
from app.services.market_watcher_bridge_service import MarketWatcherBridgeService
from app.services.market_watcher_service import MarketWatcherService
from app.services.notifications.preferences_service import NotificationPreferencesService
from app.services.paper_alert_service import PaperAlertService
from app.services.paper_eligibility_service import PaperEligibilityService
from app.services.paper_portfolio_service import PaperPortfolioService
from app.services.paper_scheduler_service import PaperSchedulerService
from app.services.paper_signal_orchestration_service import PaperSignalOrchestrationService
from app.services.paper_validation_candidate_service import PaperValidationCandidateService
from app.services.paper_validation_draft_service import PaperValidationDraftService
from app.services.paper_validation_run_plan_service import PaperValidationRunPlanService
from app.services.paper_validation_run_session_service import PaperValidationRunSessionService
from app.services.paper_validation_runtime_service import PaperValidationRuntimeService
from app.services.paper_validation_service import PaperValidationService
from app.services.paper_validation_session_observation_service import (
    PaperValidationSessionObservationService,
)
from app.services.paper_validation_session_result_service import PaperValidationSessionResultService
from app.services.performance_service import PerformanceService
from app.services.position_service import PositionService
from app.services.position_sizing_service import PositionSizingService
from app.services.pretrade_analysis_service import PreTradeAnalysisService
from app.services.proposal_service import ProposalService
from app.services.quota_service import QuotaService
from app.services.rag_service import RagService, build_rag_service
from app.services.research_validation_service import ResearchValidationService
from app.services.risk.kill_switch import KillSwitchService
from app.services.autonomous_trading_settings_service import AutonomousTradingSettingsService
from app.services.risk.settings_service import RiskSettingsService
from app.services.risk_service import RiskService
from app.services.setup_evidence_service import SetupEvidenceService
from app.services.strategy_analytics_service import StrategyAnalyticsService
from app.services.strategy_library_service import StrategyLibraryService
from app.services.strategy_quality import StrategyQualityService
from app.services.strategy_service import StrategyService
from app.services.strategy_testability_service import StrategyTestabilityService
from app.services.structure_from_text_service import StructureFromTextService
from app.services.structured_rules_service import StructuredRulesService
from app.services.tradingview_signal_service import TradingViewSignalService
from app.services.usage_service import UsageService
from app.services.validation_priority import ValidationPriorityService
from app.services.watcher_monitoring_service import WatcherMonitoringService
from app.services.workflow_service import WorkflowService
from app.strategies.registry import StrategyRegistry, get_strategy_registry
from app.tools.registry import ToolRegistry, get_tool_registry

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]
ProviderRegistryDep = Annotated[ProviderRegistry, Depends(get_provider_registry)]
ToolRegistryDep = Annotated[ToolRegistry, Depends(get_tool_registry)]
StrategyRegistryDep = Annotated[StrategyRegistry, Depends(get_strategy_registry)]


def get_risk_service() -> RiskService:
    return RiskService()


def get_strategy_service(
    registry: StrategyRegistryDep,
) -> StrategyService:
    return StrategyService(registry=registry)


RiskServiceDep = Annotated[RiskService, Depends(get_risk_service)]
StrategyServiceDep = Annotated[StrategyService, Depends(get_strategy_service)]


def get_audit_service(session: SessionDep, settings: SettingsDep) -> AuditService:
    from app.db.session import get_session_factory

    return AuditService(
        session,
        strict_mode=settings.observability_strict_mode,
        session_factory=get_session_factory(),
    )


def get_usage_service(session: SessionDep, settings: SettingsDep) -> UsageService:
    return UsageService(session, strict_mode=settings.observability_strict_mode)


def get_quota_service(session: SessionDep) -> QuotaService:
    audit = AuditService(session)
    return QuotaService(session, audit_service=audit)


AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]
UsageServiceDep = Annotated[UsageService, Depends(get_usage_service)]
QuotaServiceDep = Annotated[QuotaService, Depends(get_quota_service)]


def get_rag_service(session: SessionDep, settings: SettingsDep) -> RagService:
    audit = AuditService(session, strict_mode=settings.observability_strict_mode)
    usage = UsageService(session, strict_mode=settings.observability_strict_mode)
    return build_rag_service(
        settings,
        session,
        audit_service=audit,
        usage_service=usage,
    )


RagServiceDep = Annotated[RagService, Depends(get_rag_service)]


def get_market_service(session: SessionDep, audit_service: AuditServiceDep) -> MarketService:
    return MarketService(session, audit_service)


def get_canonical_runtime(
    request: Request,
    session: SessionDep,
) -> Iterator[ProductionCanonicalRuntime]:
    """Bind PostgreSQL canonical adapters to the request unit of work."""

    from app.runtime.canonical import build_production_canonical_runtime

    runtime = getattr(request.app.state, "canonical_runtime", None)
    if not isinstance(runtime, ProductionCanonicalRuntime):
        from app.db.session import get_session_factory

        runtime = build_production_canonical_runtime(
            get_session_factory(), settings=request.app.state.settings
        )
    with runtime.bind_session(session):
        yield runtime


CanonicalRuntimeDep = Annotated[ProductionCanonicalRuntime, Depends(get_canonical_runtime)]


def get_canonical_evidence_service(
    request: Request,
    settings: SettingsDep,
    session: SessionDep,
) -> CanonicalEvidenceService:
    """Read-only USD-M evidence assembler. Does not start Watcher.

    Live Binance reads share one process cache and one request-weight budget.
    """

    from app.market_contracts.adapters.factory import canonical_evidence_source_for_process

    source = canonical_evidence_source_for_process(request.app.state, settings)
    return CanonicalEvidenceService(settings, source=source, session=session)


CanonicalEvidenceServiceDep = Annotated[
    CanonicalEvidenceService, Depends(get_canonical_evidence_service)
]


def get_perpetual_market_monitor(request: Request, settings: SettingsDep) -> PerpetualMarketMonitor:
    """Process-local USD-M monitor. Does not start Watcher."""
    monitor = getattr(request.app.state, "market_monitor", None)
    if not isinstance(monitor, PerpetualMarketMonitor):
        monitor = build_perpetual_market_monitor(settings)
        request.app.state.market_monitor = monitor
    return monitor


def get_perpetual_market_monitor_service(
    monitor: Annotated[PerpetualMarketMonitor, Depends(get_perpetual_market_monitor)],
    settings: SettingsDep,
) -> PerpetualMarketMonitorService:
    return PerpetualMarketMonitorService(monitor, settings=settings)


PerpetualMarketMonitorDep = Annotated[PerpetualMarketMonitor, Depends(get_perpetual_market_monitor)]
PerpetualMarketMonitorServiceDep = Annotated[
    PerpetualMarketMonitorService, Depends(get_perpetual_market_monitor_service)
]


def get_proposal_service(session: SessionDep, audit_service: AuditServiceDep) -> ProposalService:
    return ProposalService(session, audit_service)


def get_approval_service(
    session: SessionDep,
    audit_service: AuditServiceDep,
    canonical_runtime: CanonicalRuntimeDep,
) -> ApprovalService:
    return ApprovalService(session, audit_service, plans=canonical_runtime.plans)


def get_execution_service(
    session: SessionDep,
    settings: SettingsDep,
    audit_service: AuditServiceDep,
    market_data_service: MarketDataServiceDep,
    risk_service: RiskServiceDep,
    risk_settings: RiskSettingsServiceDep,
    kill_switch: KillSwitchServiceDep,
    canonical_runtime: CanonicalRuntimeDep,
) -> ExecutionService:
    exchange_execution = resolve_exchange_execution_provider(settings)
    return ExecutionService(
        session,
        settings,
        audit_service,
        exchange_execution=exchange_execution,
        risk_service=risk_service,
        risk_settings=risk_settings,
        market_data_service=market_data_service,
        kill_switch=kill_switch,
        canonical_runtime=canonical_runtime,
    )


def get_position_service(
    session: SessionDep,
    settings: SettingsDep,
    audit_service: AuditServiceDep,
    risk_settings: RiskSettingsServiceDep,
    market_data_service: MarketDataServiceDep,
) -> PositionService:
    return PositionService(
        session,
        audit_service,
        settings=settings,
        risk_settings=risk_settings,
        market_data_service=market_data_service,
    )


def get_performance_service(session: SessionDep) -> PerformanceService:
    return PerformanceService(session)


def get_paper_portfolio_service(session: SessionDep) -> PaperPortfolioService:
    return PaperPortfolioService(session)


PerformanceServiceDep = Annotated[PerformanceService, Depends(get_performance_service)]
PaperPortfolioServiceDep = Annotated[PaperPortfolioService, Depends(get_paper_portfolio_service)]


def get_journal_service(
    session: SessionDep,
    audit_service: AuditServiceDep,
    settings: SettingsDep,
    rag_service: RagServiceDep,
) -> JournalService:
    sync = JournalRagSyncService(rag_service, settings)
    return JournalService(session, audit_service, rag_sync=sync)


def get_journal_trade_service(
    session: SessionDep,
    audit_service: AuditServiceDep,
) -> JournalTradeService:
    return JournalTradeService(session, audit_service)


def get_journal_statistics_service(
    session: SessionDep,
    settings: SettingsDep,
) -> JournalStatisticsService:
    return JournalStatisticsService(session, max_rows=settings.journal_stats_max_rows)


def get_strategy_analytics_service(
    session: SessionDep,
    settings: SettingsDep,
) -> StrategyAnalyticsService:
    return StrategyAnalyticsService(session, max_rows=settings.journal_stats_max_rows)


def get_journal_excursion_replay_service(
    session: SessionDep,
    audit_service: AuditServiceDep,
    settings: SettingsDep,
) -> JournalExcursionReplayService:
    return JournalExcursionReplayService(session, audit_service, settings)


def get_journal_import_service(
    session: SessionDep,
    audit_service: AuditServiceDep,
) -> JournalImportService:
    return JournalImportService(session, audit_service)


def get_journal_attachment_service(
    session: SessionDep,
    audit_service: AuditServiceDep,
    settings: SettingsDep,
) -> JournalAttachmentService:
    return JournalAttachmentService(session, audit_service, get_attachment_storage(), settings)


def get_workflow_service(
    proposal_service: ProposalServiceDep,
    approval_service: ApprovalServiceDep,
) -> WorkflowService:
    return WorkflowService(proposal_service, approval_service)


def get_market_data_service(
    settings: SettingsDep,
    strategy_service: StrategyServiceDep,
) -> MarketDataService:
    provider = resolve_market_data_provider(settings)
    cache = MarketDataCache(settings)
    return MarketDataService(
        provider,
        cache=cache,
        indicator_service=IndicatorService(),
        strategy_service=strategy_service,
    )


MarketDataServiceDep = Annotated[MarketDataService, Depends(get_market_data_service)]
MarketServiceDep = Annotated[MarketService, Depends(get_market_service)]
ProposalServiceDep = Annotated[ProposalService, Depends(get_proposal_service)]
ApprovalServiceDep = Annotated[ApprovalService, Depends(get_approval_service)]
ExecutionServiceDep = Annotated[ExecutionService, Depends(get_execution_service)]
PositionServiceDep = Annotated[PositionService, Depends(get_position_service)]
JournalServiceDep = Annotated[JournalService, Depends(get_journal_service)]
JournalTradeServiceDep = Annotated[JournalTradeService, Depends(get_journal_trade_service)]
JournalStatisticsServiceDep = Annotated[
    JournalStatisticsService, Depends(get_journal_statistics_service)
]
StrategyAnalyticsServiceDep = Annotated[
    StrategyAnalyticsService, Depends(get_strategy_analytics_service)
]
JournalExcursionReplayServiceDep = Annotated[
    JournalExcursionReplayService, Depends(get_journal_excursion_replay_service)
]
JournalImportServiceDep = Annotated[JournalImportService, Depends(get_journal_import_service)]
JournalAttachmentServiceDep = Annotated[
    JournalAttachmentService, Depends(get_journal_attachment_service)
]
WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]


def get_analytics_facade(session: SessionDep) -> TradingAnalyticsFacade:
    return TradingAnalyticsFacade(session)


AnalyticsFacadeDep = Annotated[TradingAnalyticsFacade, Depends(get_analytics_facade)]


def get_learning_analytics_service(session: SessionDep) -> LearningAnalyticsService:
    return LearningAnalyticsService(session)


LearningAnalyticsServiceDep = Annotated[
    LearningAnalyticsService, Depends(get_learning_analytics_service)
]


def get_validation_priority_service(session: SessionDep) -> ValidationPriorityService:
    return ValidationPriorityService(session)


ValidationPriorityServiceDep = Annotated[
    ValidationPriorityService, Depends(get_validation_priority_service)
]


def get_coaching_service(session: SessionDep) -> CoachingService:
    return CoachingService(session)


CoachingServiceDep = Annotated[CoachingService, Depends(get_coaching_service)]


def get_strategy_quality_service(session: SessionDep) -> StrategyQualityService:
    return StrategyQualityService(session)


StrategyQualityServiceDep = Annotated[StrategyQualityService, Depends(get_strategy_quality_service)]


def get_position_sizing_service() -> PositionSizingService:
    return PositionSizingService()


def get_loss_acceptance_service() -> LossAcceptanceService:
    return LossAcceptanceService()


def get_strategy_library_service(
    session: SessionDep,
    rag_service: RagServiceDep,
) -> StrategyLibraryService:
    return StrategyLibraryService(session, rag_service=rag_service)


def get_manual_level_service(session: SessionDep) -> ManualLevelService:
    return ManualLevelService(session)


def get_pretrade_analysis_service(
    session: SessionDep,
    market_data_service: MarketDataServiceDep,
) -> PreTradeAnalysisService:
    return PreTradeAnalysisService(session, market_data_service)


def get_human_vs_system_service(
    session: SessionDep,
    settings: SettingsDep,
) -> HumanVsSystemService:
    provider = resolve_market_data_provider(settings)
    candle_service = HistoricalCandleService(session, provider, settings)
    return HumanVsSystemService(session, historical_candle_service=candle_service)


def get_lesson_candidate_service(
    session: SessionDep,
    settings: SettingsDep,
    rag_service: RagServiceDep,
) -> LessonCandidateService:
    audit = AuditService(session, strict_mode=settings.observability_strict_mode)
    return LessonCandidateService(
        session,
        audit_service=audit,
        rag_service=rag_service,
        settings=settings,
    )


def get_strategy_testability_service(session: SessionDep) -> StrategyTestabilityService:
    return StrategyTestabilityService(session)


def get_structured_rules_service(session: SessionDep) -> StructuredRulesService:
    return StructuredRulesService(session)


def get_structure_from_text_service() -> StructureFromTextService:
    return StructureFromTextService()


def get_backtest_service(
    session: SessionDep, settings: SettingsDep, audit_service: AuditServiceDep
) -> BacktestService:
    return BacktestService(session, settings, audit_service)


def get_backtest_journal_service(
    session: SessionDep, settings: SettingsDep, audit_service: AuditServiceDep
) -> BacktestJournalService:
    return BacktestJournalService(session, audit_service, settings)


def get_setup_evidence_service(session: SessionDep, settings: SettingsDep) -> SetupEvidenceService:
    return SetupEvidenceService(session, settings)


def get_research_validation_service(
    session: SessionDep, settings: SettingsDep
) -> ResearchValidationService:
    return ResearchValidationService(session, settings)


def get_tradingview_signal_service(
    session: SessionDep, settings: SettingsDep
) -> TradingViewSignalService:
    return TradingViewSignalService(session, settings)


def get_paper_signal_orchestration_service(
    session: SessionDep,
    settings: SettingsDep,
    kill_switch: KillSwitchServiceDep,
    proposal_service: ProposalServiceDep,
    audit_service: AuditServiceDep,
) -> PaperSignalOrchestrationService:
    return PaperSignalOrchestrationService(
        session,
        settings=settings,
        kill_switch=kill_switch,
        proposal_service=proposal_service,
        audit_service=audit_service,
    )


def get_blofin_sync_service(session: SessionDep, settings: SettingsDep) -> BloFinSyncService:
    return BloFinSyncService(session, settings)


def get_paper_validation_service(session: SessionDep) -> PaperValidationService:
    return PaperValidationService(session)


def get_paper_validation_runtime_service(
    session: SessionDep, settings: SettingsDep
) -> PaperValidationRuntimeService:
    return PaperValidationRuntimeService(session, settings)


def get_paper_eligibility_service(
    session: SessionDep, settings: SettingsDep
) -> PaperEligibilityService:
    return PaperEligibilityService(session, settings)


def get_paper_alert_service(session: SessionDep, settings: SettingsDep) -> PaperAlertService:
    delivery = AlertDeliveryService(session, settings)
    return PaperAlertService(session, delivery_service=delivery)


def get_paper_validation_draft_service(session: SessionDep) -> PaperValidationDraftService:
    return PaperValidationDraftService(session)


def get_paper_validation_candidate_service(session: SessionDep) -> PaperValidationCandidateService:
    return PaperValidationCandidateService(session)


def get_paper_validation_run_plan_service(session: SessionDep) -> PaperValidationRunPlanService:
    return PaperValidationRunPlanService(session)


def get_paper_validation_run_session_service(
    session: SessionDep,
) -> PaperValidationRunSessionService:
    return PaperValidationRunSessionService(session)


def get_paper_validation_session_observation_service(
    session: SessionDep,
) -> PaperValidationSessionObservationService:
    return PaperValidationSessionObservationService(session)


def get_paper_validation_session_result_service(
    session: SessionDep,
) -> PaperValidationSessionResultService:
    return PaperValidationSessionResultService(session)


def get_alert_delivery_service(session: SessionDep, settings: SettingsDep) -> AlertDeliveryService:
    return AlertDeliveryService(session, settings)


def get_market_watcher_bridge_service(
    session: SessionDep, settings: SettingsDep, audit_service: AuditServiceDep
) -> MarketWatcherBridgeService:
    return MarketWatcherBridgeService(session, settings, audit_service=audit_service)


def get_market_watcher_service(session: SessionDep, settings: SettingsDep) -> MarketWatcherService:
    return MarketWatcherService(session, settings)


def get_watcher_monitoring_service(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    canonical_runtime: CanonicalRuntimeDep,
    providers: ProviderRegistryDep,
    monitor: PerpetualMarketMonitorDep,
) -> WatcherMonitoringService:
    paper_runtime = getattr(request.app.state, "watcher_paper_runtime", None)
    return WatcherMonitoringService(
        session,
        settings,
        providers=providers,
        canonical_runtime=canonical_runtime,
        monitor=monitor,
        paper_runtime=paper_runtime,
    )


def get_paper_scheduler_service(
    session: SessionDep, settings: SettingsDep, audit_service: AuditServiceDep
) -> PaperSchedulerService:
    return PaperSchedulerService(session, settings, audit_service=audit_service)


def get_historical_candle_service(
    session: SessionDep,
    settings: SettingsDep,
    strategy_service: StrategyServiceDep,
) -> HistoricalCandleService:
    provider = resolve_market_data_provider(settings)
    _ = strategy_service
    return HistoricalCandleService(session, provider, settings)


PositionSizingServiceDep = Annotated[PositionSizingService, Depends(get_position_sizing_service)]
LossAcceptanceServiceDep = Annotated[LossAcceptanceService, Depends(get_loss_acceptance_service)]
StrategyLibraryServiceDep = Annotated[StrategyLibraryService, Depends(get_strategy_library_service)]
ManualLevelServiceDep = Annotated[ManualLevelService, Depends(get_manual_level_service)]
PreTradeAnalysisServiceDep = Annotated[
    PreTradeAnalysisService, Depends(get_pretrade_analysis_service)
]
HumanVsSystemServiceDep = Annotated[HumanVsSystemService, Depends(get_human_vs_system_service)]
LessonCandidateServiceDep = Annotated[LessonCandidateService, Depends(get_lesson_candidate_service)]
StrategyTestabilityServiceDep = Annotated[
    StrategyTestabilityService, Depends(get_strategy_testability_service)
]
StructuredRulesServiceDep = Annotated[StructuredRulesService, Depends(get_structured_rules_service)]
StructureFromTextServiceDep = Annotated[
    StructureFromTextService, Depends(get_structure_from_text_service)
]
BacktestServiceDep = Annotated[BacktestService, Depends(get_backtest_service)]
BacktestJournalServiceDep = Annotated[BacktestJournalService, Depends(get_backtest_journal_service)]
SetupEvidenceServiceDep = Annotated[SetupEvidenceService, Depends(get_setup_evidence_service)]
ResearchValidationServiceDep = Annotated[
    ResearchValidationService, Depends(get_research_validation_service)
]
TradingViewSignalServiceDep = Annotated[
    TradingViewSignalService, Depends(get_tradingview_signal_service)
]
PaperSignalOrchestrationServiceDep = Annotated[
    PaperSignalOrchestrationService, Depends(get_paper_signal_orchestration_service)
]
BloFinSyncServiceDep = Annotated[BloFinSyncService, Depends(get_blofin_sync_service)]
PaperValidationServiceDep = Annotated[PaperValidationService, Depends(get_paper_validation_service)]
PaperValidationRuntimeServiceDep = Annotated[
    PaperValidationRuntimeService, Depends(get_paper_validation_runtime_service)
]
PaperEligibilityServiceDep = Annotated[
    PaperEligibilityService, Depends(get_paper_eligibility_service)
]
PaperAlertServiceDep = Annotated[PaperAlertService, Depends(get_paper_alert_service)]
PaperValidationDraftServiceDep = Annotated[
    PaperValidationDraftService, Depends(get_paper_validation_draft_service)
]
PaperValidationCandidateServiceDep = Annotated[
    PaperValidationCandidateService, Depends(get_paper_validation_candidate_service)
]
PaperValidationRunPlanServiceDep = Annotated[
    PaperValidationRunPlanService, Depends(get_paper_validation_run_plan_service)
]
PaperValidationRunSessionServiceDep = Annotated[
    PaperValidationRunSessionService, Depends(get_paper_validation_run_session_service)
]
PaperValidationSessionObservationServiceDep = Annotated[
    PaperValidationSessionObservationService,
    Depends(get_paper_validation_session_observation_service),
]
PaperValidationSessionResultServiceDep = Annotated[
    PaperValidationSessionResultService, Depends(get_paper_validation_session_result_service)
]
AlertDeliveryServiceDep = Annotated[AlertDeliveryService, Depends(get_alert_delivery_service)]
MarketWatcherServiceDep = Annotated[MarketWatcherService, Depends(get_market_watcher_service)]
MarketWatcherBridgeServiceDep = Annotated[
    MarketWatcherBridgeService, Depends(get_market_watcher_bridge_service)
]
WatcherMonitoringServiceDep = Annotated[
    WatcherMonitoringService, Depends(get_watcher_monitoring_service)
]
PaperSchedulerServiceDep = Annotated[PaperSchedulerService, Depends(get_paper_scheduler_service)]
HistoricalCandleServiceDep = Annotated[
    HistoricalCandleService, Depends(get_historical_candle_service)
]


def get_dashboard_summary_service(
    session: SessionDep,
    settings: SettingsDep,
    audit_service: AuditServiceDep,
) -> DashboardSummaryService:
    return DashboardSummaryService(
        session,
        settings,
        risk_settings=RiskSettingsService(session, audit_service),
    )


DashboardSummaryServiceDep = Annotated[
    DashboardSummaryService, Depends(get_dashboard_summary_service)
]


def get_notification_preferences_service(
    session: SessionDep,
    audit_service: AuditServiceDep,
) -> NotificationPreferencesService:
    return NotificationPreferencesService(session, audit_service)


NotificationPreferencesServiceDep = Annotated[
    NotificationPreferencesService, Depends(get_notification_preferences_service)
]


def get_risk_settings_service(
    session: SessionDep,
    audit_service: AuditServiceDep,
) -> RiskSettingsService:
    return RiskSettingsService(session, audit_service)


RiskSettingsServiceDep = Annotated[RiskSettingsService, Depends(get_risk_settings_service)]


def get_autonomous_trading_settings_service(
    session: SessionDep,
    audit_service: AuditServiceDep,
) -> AutonomousTradingSettingsService:
    return AutonomousTradingSettingsService(session, audit_service)


AutonomousTradingSettingsServiceDep = Annotated[
    AutonomousTradingSettingsService,
    Depends(get_autonomous_trading_settings_service),
]


def get_kill_switch_service(
    session: SessionDep,
    audit_service: AuditServiceDep,
    settings: SettingsDep,
) -> KillSwitchService:
    return KillSwitchService(session, audit_service, settings)


KillSwitchServiceDep = Annotated[KillSwitchService, Depends(get_kill_switch_service)]
