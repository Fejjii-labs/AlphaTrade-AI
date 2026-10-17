import { validatedFetch } from "./validated-fetch";
import * as validators from "./generated/validators";
import * as generatedApi from "./generated/client";
import type { BrainDraftBinding, BrainOverview, BrainSetup, SfpDraft } from "./brain-types";
import { apiFetch } from "@/lib/api/client";
import type {
  ApprovalRequest,
  AuditRecord,
  AuthResponse,
  HealthResponse,
  IngestDocumentResponse,
  FileImportPreview,
  DisciplineScoreResult,
  DashboardSummary,
  JournalEntry,
  JournalImportBatch,
  JournalImportRequestBody,
  JournalImportResult,
  JournalStatsParams,
  JournalStatsResponse,
  PaginatedCanonicalJournalTrades,
  PaginatedJournalImportBatches,
  PaginatedConversations,
  PaginatedConversationMessages,
  PaginatedStrategyProposals,
  ConversationSummary,
  StrategyProposalRecord,
  RiskBehaviorAnalytics,
  SetupAnalyticsResponse,
  TradeReviewAnalytics,
  SetupDimension,
  LearningAnalyticsParams,
  LearningAnalyticsSummaryResponse,
  SetupPerformanceResponse,
  DisciplineAnalyticsResponse,
  ConfidenceOutcomeResponse,
  BehaviorInsightsResponse,
  LessonThemesResponse,
  SetupRankingResponse,
  PriorityItemType,
  ValidationPriorityParams,
  ValidationPriorityQueueResponse,
  ValidationPrioritySummaryResponse,
  ValidationPriorityExplainResponse,
  StrategyQualityParams,
  StrategyQualityDetectorsResponse,
  StrategyQualitySummaryResponse,
  DetectorExplainResponse,
  PaperPortfolioParams,
  PaperPortfolioResponse,
  PerformanceSnapshotListResponse,
  CoachingParams,
  CoachingPromptsResponse,
  CoachingSummaryResponse,
  CoachingExplainResponse,
  CoachingSaveRequest,
  CoachingCategory,
  MeResponse,
  PaperAccountStatus,
  PaperAccountRegistration,
  MessageResponse,
  OrganizationInvitation,
  InvitationListResponse,
  PaginatedApprovalRequests,
  PaginatedAuditRecords,
  PaginatedJournalEntries,
  PaginatedPositions,
  PaginatedTradeProposals,
  PaginatedUsageEvents,
  PaperOrder,
  PaginatedPaperOrders,
  ExecutePaperPlanRequest,
  ExecutePaperPlanResult,
  CanonicalCandidateRead,
  PaginatedCanonicalCandidates,
  CanonicalSetupAssessmentRead,
  CanonicalEligibilityRead,
  CanonicalExecutionReceiptRead,
  CanonicalLearningRecordRead,
  CanonicalLearningStatsRead,
  CanonicalPaperEvaluationRead,
  CanonicalEvidenceRead,
  CanonicalMarketMonitorStatusRead,
  CanonicalTradePlanRevision,
  ProposalWorkflowView,
  ApprovalWorkflowView,
  Position,
  ProviderStatusResponse,
  RagSearchResponse,
  ReadinessResponse,
  TokenPair,
  TradeProposal,
  UsageSummary,
  UsageFeatureBreakdown,
  SubscriptionPlan,
  BillingStatus,
  BillingCustomer,
  CheckoutResponse,
  PortalResponse,
  UsageExportResponse,
  UsageProviderBreakdown,
  QuotaStatus,
  OrganizationQuotaConfig,
  MembershipRole,
  MarketAnalyzeResponse,
  ManualChartLevel,
  PaginatedManualChartLevels,
  PaginatedUserStrategies,
  PaginatedUserStrategyVersions,
  PaperEligibilityReport,
  PreTradeAnalyzeResponse,
  PositionSizingResult,
  HumanVsSystemComparison,
  UserStrategy,
  UserRiskSettings,
  UserRiskSettingsUpdate,
  KillSwitchStatus,
  KillSwitchMutationRequest,
  BacktestRun,
  BacktestRunCreate,
  BacktestVerifyResult,
  BacktestJournalRequest,
  BacktestJournalResult,
  JournalComparisonParams,
  JournalComparisonResponse,
  SetupEvidenceParams,
  SetupEvidenceResponse,
  PaginatedBacktestRuns,
  PaginatedBacktestTrades,
  PaperValidationMetrics,
  PaperValidationRun,
  PaperValidationSummary,
  PaperSchedulerStatus,
  PaperSchedulerTickResult,
  PaperRuntimeHistoryRecord,
  PaperAlert,
  PaperAlertSummary,
  SetupAlertReviewItem,
  SetupAlertReviewSummary,
  SetupAlertDraftCreateResult,
  PaperValidationDraftChecklist,
  PaperValidationDraftItem,
  PaperValidationDraftPrepStatus,
  PaperValidationDraftSummary,
  PaperValidationCandidateItem,
  PaperValidationCandidateQueueResult,
  PaperValidationCandidateSummary,
  PaperValidationRunPlanCreateResult,
  PaperValidationRunPlanItem,
  PaperValidationRunPlanSummary,
  PaperValidationRunSessionItem,
  PaperValidationRunSessionStartResult,
  PaperValidationSessionObservationItem,
  PaperValidationSessionResultCreateResult,
  PaperValidationSessionResultItem,
  AlertDeliveryStatusResponse,
  NotificationPreferences,
  NotificationTestResult,
  TelegramEnrollmentStartResponse,
  MarketWatcherStatus,
  MarketWatcherSummary,
  WatcherMonitoringSnapshot,
  MarketWatcherScanRequest,
  MarketWatcherScanResult,
  PaginatedMarketWatcherRecentScans,
  MarketWatcherBridgeStatus,
  MarketWatcherBridgeTickResult,
  PaginatedMarketWatcherBridgeHistory,
  PaginatedMarketWatcherObservations,
  PaperScanResult,
  PaperSignalResult,
  PaperTickResult,
  PaperTradeRecord,
  StrategyTestability,
  StructuredRules,
  LessonCandidate,
  PaginatedLessonCandidates,
  MarketSnapshotResponse,
  OHLCVResponse,
  TickerResponse,
  WatchlistItem,
  WatcherWatchlistConfiguration,
  WatcherWatchlistSlot,
  WatcherWatchlistStatus,
  ExchangeDiagnosticsSummary,
  AlertDeliveryPreviewResponse,
  AlertRoutingSummary,
  TelegramAlertDeliveryResponse,
  TelegramTestAlertResponse,
  ResearchValidationEvidenceParams,
  ResearchValidationEvidenceResponse,
  ResearchValidationPromoteResult,
  ResearchValidationStatusResponse,
  TradingViewSignalCreateCandidateResult,
  TradingViewSignalItem,
  TradingViewSignalListResponse,
  TradingViewSignalStatus,
  BloFinSyncResult,
  BloFinSyncSnapshotItem,
  PaperSignalOrchestrationApproveResult,
  PaperSignalOrchestrationDecisionItem,
  PaperSignalOrchestrationEvaluateResult,
  PaperSignalOrchestrationListResponse,
  PaperSignalOrchestrationStatus,
} from "@/lib/api/types";

export const PROMOTE_RESEARCH_VALIDATION_CANDIDATE = "PROMOTE_RESEARCH_VALIDATION_CANDIDATE";
export const CREATE_TRADINGVIEW_PAPER_CANDIDATE = "CREATE_TRADINGVIEW_PAPER_CANDIDATE";
export const APPROVE_PAPER_SIGNAL_PROPOSAL = "APPROVE_PAPER_SIGNAL_PROPOSAL";

export const api = {
  strategyBrain: {
    overview: () => apiFetch<BrainOverview>("/strategy-brain/overview", { auth: true }),
    setup: (id: string) => apiFetch<BrainSetup>(`/strategy-brain/setups/${id}`, { auth: true }),
    createNested: (body: BrainDraftBinding) =>
      apiFetch<{ strategy_id: string; version_id: string }>("/strategy-brain/templates/nested", {
        method: "POST", auth: true, body: JSON.stringify(body),
      }),
    createSfp: (body: SfpDraft) =>
      apiFetch<{ strategy_id: string; version_id: string }>("/strategy-brain/templates/sfp", {
        method: "POST", auth: true, body: JSON.stringify(body),
      }),
  },
  auth: {
    register: (body: { email: string; password: string; organization_name: string }) =>
      apiFetch<AuthResponse>("/auth/register", {
        method: "POST",
        body: JSON.stringify(body),
        auth: false,
      }),
    login: (body: { email: string; password: string }) =>
      apiFetch<AuthResponse>("/auth/login", {
        method: "POST",
        body: JSON.stringify(body),
        auth: false,
      }),
    refresh: (refresh_token: string) =>
      apiFetch<TokenPair>("/auth/refresh", {
        method: "POST",
        body: JSON.stringify({ refresh_token }),
        auth: false,
      }),
    logout: (refresh_token?: string) =>
      apiFetch<{ message: string }>("/auth/logout", {
        method: "POST",
        body: JSON.stringify(refresh_token ? { refresh_token } : {}),
        auth: true,
      }),
    me: () => apiFetch<MeResponse>("/auth/me", { auth: true }),
    requestVerifyEmail: (body?: { email?: string }) =>
      apiFetch<MessageResponse>("/auth/verify-email/request", {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      }),
    confirmVerifyEmail: (token: string) =>
      apiFetch<MessageResponse>("/auth/verify-email/confirm", {
        method: "POST",
        body: JSON.stringify({ token }),
        auth: false,
      }),
    requestPasswordReset: (email: string) =>
      apiFetch<MessageResponse>("/auth/password-reset/request", {
        method: "POST",
        body: JSON.stringify({ email }),
        auth: false,
      }),
    confirmPasswordReset: (token: string, new_password: string) =>
      apiFetch<MessageResponse>("/auth/password-reset/confirm", {
        method: "POST",
        body: JSON.stringify({ token, new_password }),
        auth: false,
      }),
  },
  organizations: {
    listInvitations: () => apiFetch<InvitationListResponse>("/organizations/invitations"),
    createInvitation: (body: { email: string; role: MembershipRole }) =>
      apiFetch<OrganizationInvitation>("/organizations/invitations", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    acceptInvitation: (id: string, token: string) =>
      apiFetch<OrganizationInvitation>(`/organizations/invitations/${id}/accept`, {
        method: "POST",
        body: JSON.stringify({ token }),
      }),
    revokeInvitation: (id: string) =>
      apiFetch<OrganizationInvitation>(`/organizations/invitations/${id}/revoke`, {
        method: "POST",
      }),
  },
  health: {
    get: () => apiFetch<HealthResponse>("/health", { auth: false }),
    ready: () => apiFetch<ReadinessResponse>("/health/ready", { auth: false }),
  },
  providers: {
    status: () => apiFetch<ProviderStatusResponse>("/providers/status", { auth: false }),
  },
  exchange: {
    diagnosticsSummary: () =>
      apiFetch<ExchangeDiagnosticsSummary>("/exchange/diagnostics/summary", { auth: true }),
    blofinSync: () =>
      apiFetch<BloFinSyncResult>("/exchange/blofin/sync", { method: "POST", auth: true }),
    blofinSyncLatest: () =>
      apiFetch<BloFinSyncSnapshotItem>("/exchange/blofin/sync/latest", { auth: true }),
  },
  tradingview: {
    listSignals: (params?: {
      status?: TradingViewSignalStatus;
      symbol?: string;
      limit?: number;
      offset?: number;
    }) =>
      apiFetch<TradingViewSignalListResponse>("/tradingview/signals", {
        query: params,
        auth: true,
      }),
    getSignal: (id: string) =>
      apiFetch<TradingViewSignalItem>(`/tradingview/signals/${id}`, { auth: true }),
    createCandidate: (id: string, body: { confirm: string }) =>
      apiFetch<TradingViewSignalCreateCandidateResult>(
        `/tradingview/signals/${id}/create-candidate`,
        {
          method: "POST",
          body: JSON.stringify(body),
          auth: true,
        },
      ),
  },
  paperSignalOrchestration: {
    listDecisions: (params?: {
      status?: PaperSignalOrchestrationStatus;
      symbol?: string;
      limit?: number;
      offset?: number;
    }) =>
      apiFetch<PaperSignalOrchestrationListResponse>("/paper-signal-orchestration/decisions", {
        query: params,
        auth: true,
      }),
    getDecision: (id: string) =>
      apiFetch<PaperSignalOrchestrationDecisionItem>(
        `/paper-signal-orchestration/decisions/${id}`,
        { auth: true },
      ),
    evaluate: (signalId: string) =>
      apiFetch<PaperSignalOrchestrationEvaluateResult>(
        `/paper-signal-orchestration/signals/${signalId}/evaluate`,
        { method: "POST", auth: true },
      ),
    orchestrate: (signalId: string) =>
      apiFetch<PaperSignalOrchestrationEvaluateResult>(
        `/paper-signal-orchestration/signals/${signalId}/orchestrate`,
        { method: "POST", auth: true },
      ),
    approvePaperProposal: (decisionId: string, body: { confirm: string }) =>
      apiFetch<PaperSignalOrchestrationApproveResult>(
        `/paper-signal-orchestration/decisions/${decisionId}/approve-paper-proposal`,
        {
          method: "POST",
          body: JSON.stringify(body),
          auth: true,
        },
      ),
  },
  chat: { message: generatedApi.chatMessage },
  agent: {
    turn: generatedApi.agentTurn,
    retryCapture: generatedApi.captureRetry,
    confirmProposal: generatedApi.confirmProposal,
    rejectProposal: generatedApi.rejectProposal,
  },
  conversations: {
    list: (params?: { strategy_id?: string; limit?: number; offset?: number }) =>
      apiFetch<PaginatedConversations>("/conversations", { query: params, auth: true }),
    create: (body: { title?: string; strategy_id?: string }) =>
      apiFetch<ConversationSummary>("/conversations", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    get: (id: string) => apiFetch<ConversationSummary>(`/conversations/${id}`, { auth: true }),
    listMessages: (id: string, params?: { limit?: number; offset?: number }, options?: { signal?: AbortSignal }) =>
      apiFetch<PaginatedConversationMessages>(`/conversations/${id}/messages`, {
        query: params,
        auth: true,
        signal: options?.signal,
      }),
    listProposals: (
      id: string,
      params?: { status?: string; limit?: number; offset?: number },
    ) =>
      apiFetch<PaginatedStrategyProposals>(`/conversations/${id}/proposals`, {
        query: params,
        auth: true,
      }),
    createProposal: (id: string, body: { text: string; strategy_id?: string }) =>
      apiFetch<StrategyProposalRecord>(`/conversations/${id}/proposals`, {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    getProposal: (conversationId: string, proposalId: string) =>
      apiFetch<StrategyProposalRecord>(
        `/conversations/${conversationId}/proposals/${proposalId}`,
        { auth: true },
      ),
    confirmProposal: (
      conversationId: string,
      proposalId: string,
      body: {
        confirm: string;
        request_id?: string;
        expected_content_hash: string;
        expected_parent_version_id?: string | null;
        expected_target_strategy_id?: string | null;
        expected_organization_id: string;
        expected_user_id: string;
        expected_conversation_id: string;
      },
    ) =>
      apiFetch<StrategyProposalRecord>(
        `/conversations/${conversationId}/proposals/${proposalId}/confirm`,
        { method: "POST", body: JSON.stringify(body), auth: true },
      ),
    rejectProposal: (
      conversationId: string,
      proposalId: string,
      body: { confirm: string },
    ) =>
      apiFetch<StrategyProposalRecord>(
        `/conversations/${conversationId}/proposals/${proposalId}/reject`,
        { method: "POST", body: JSON.stringify(body), auth: true },
      ),
  },
  watchlist: {
    list: (params?: { limit?: number; offset?: number }) =>
      apiFetch<WatchlistItem[]>("/market/watchlist", { query: params }),
    create: (body: {
      symbol: string;
      exchange: string;
      timeframes: string[];
      strategy_ids: string[];
      enabled?: boolean;
      organization_id?: string;
      user_id?: string;
    }) =>
      apiFetch<WatchlistItem>("/market/watchlist", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (
      id: string,
      body: { timeframes?: string[]; strategy_ids?: string[]; enabled?: boolean },
    ) =>
      apiFetch<WatchlistItem>(`/market/watchlist/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    delete: (id: string) =>
      apiFetch<void>(`/market/watchlist/${id}`, {
        method: "DELETE",
      }),
  },
  proposals: {
    list: (params?: { limit?: number; offset?: number; status?: string }) =>
      apiFetch<PaginatedTradeProposals>("/proposals", { query: params }),
    get: (id: string) => apiFetch<TradeProposal>(`/proposals/${id}`),
    workflow: (id: string) => apiFetch<ProposalWorkflowView>(`/proposals/${id}/workflow`),
    listRevisions: (id: string) =>
      apiFetch<CanonicalTradePlanRevision[]>(`/proposals/${id}/revisions`, { auth: true }),
    getRevision: (id: string, revisionId: string) =>
      apiFetch<CanonicalTradePlanRevision>(`/proposals/${id}/revisions/${revisionId}`, {
        auth: true,
      }),
    lossAcceptance: (id: string, body: { planned_loss_amount: string; accepted: boolean }) =>
      apiFetch<TradeProposal>(`/proposals/${id}/loss-acceptance`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
  },
  approvals: {
    list: (params?: { limit?: number; offset?: number; status?: string }) =>
      apiFetch<PaginatedApprovalRequests>("/approvals", { query: params }),
    get: (id: string) => apiFetch<ApprovalRequest>(`/approvals/${id}`),
    workflow: (id: string) => apiFetch<ApprovalWorkflowView>(`/approvals/${id}/workflow`),
    approve: (id: string, reason?: string) =>
      apiFetch<ApprovalRequest>(`/approvals/${id}/approve`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      }),
    reject: (id: string, reason?: string) =>
      apiFetch<ApprovalRequest>(`/approvals/${id}/reject`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      }),
    modify: (id: string, reason?: string, modified_fields?: Record<string, string>) =>
      apiFetch<ApprovalRequest>(`/approvals/${id}/modify`, {
        method: "POST",
        body: JSON.stringify({ reason, modified_fields }),
      }),
    needsMoreAnalysis: (id: string, reason?: string) =>
      apiFetch<ApprovalRequest>(`/approvals/${id}/needs-more-analysis`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      }),
  },
  execution: {
    paperAccountStatus: () =>
      apiFetch<PaperAccountStatus>("/execution/accounts/paper", { auth: true }),
    registerPaperAccount: () =>
      apiFetch<PaperAccountRegistration>("/execution/accounts/paper", {
        method: "POST",
        auth: true,
        body: JSON.stringify({}),
      }),
    paperOrder: (body: {
      proposal_id: string;
      approval_id?: string;
      idempotency_key?: string;
      symbol?: string;
      side?: string;
      type?: string;
      size?: string;
    }) =>
      apiFetch<PaperOrder>("/execution/paper", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    listOrders: (params?: { limit?: number; offset?: number }) =>
      apiFetch<PaginatedPaperOrders>("/execution/orders", { query: params, auth: true }),
    getOrder: (id: string) => apiFetch<PaperOrder>(`/execution/orders/${id}`, { auth: true }),
    executePaperPlan: (body: ExecutePaperPlanRequest) =>
      apiFetch<ExecutePaperPlanResult>("/execution/paper-plan", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  canonical: {
    listCandidates: (params?: { limit?: number; offset?: number }) =>
      apiFetch<PaginatedCanonicalCandidates>("/canonical/candidates", { query: params, auth: true }),
    getCandidate: (id: string) =>
      apiFetch<CanonicalCandidateRead>(`/canonical/candidates/${id}`, { auth: true }),
    getSetupAssessment: (id: string) =>
      apiFetch<CanonicalSetupAssessmentRead>(`/canonical/setup-assessments/${id}`, { auth: true }),
    getEligibility: (candidateId: string) =>
      apiFetch<CanonicalEligibilityRead>(`/canonical/candidates/${candidateId}/eligibility`, {
        auth: true,
      }),
    getExecutionReceipt: (receiptId: string) =>
      apiFetch<CanonicalExecutionReceiptRead>(`/canonical/executions/${receiptId}`, { auth: true }),
    getLearningRecord: (candidateId: string) =>
      apiFetch<CanonicalLearningRecordRead>(`/canonical/learning/records/${candidateId}`, {
        auth: true,
      }),
    strategyStats: (params?: { learning_venue_mode?: string }) =>
      apiFetch<CanonicalLearningStatsRead>("/canonical/learning/strategy-stats", {
        query: params,
        auth: true,
      }),
    paperEvaluation: (params?: { learning_venue_mode?: string }) =>
      apiFetch<CanonicalPaperEvaluationRead>("/canonical/paper-evaluation/summary", {
        query: params,
        auth: true,
      }),
    getEvidence: (params?: { symbol?: string }) =>
      apiFetch<CanonicalEvidenceRead>("/canonical/evidence", { query: params, auth: true }),
    getMarketStatus: (params?: { symbol?: string }) =>
      apiFetch<CanonicalMarketMonitorStatusRead>("/canonical/market-status", {
        query: params,
        auth: true,
      }),
  },
  positions: {
    list: (params?: { limit?: number; offset?: number; status?: string }) =>
      apiFetch<PaginatedPositions>("/positions", { query: params }),
    get: (id: string) => apiFetch<Position>(`/positions/${id}`),
    closePaper: (id: string, body: { exit_price: string; reason?: string }) =>
      apiFetch<Position>(`/positions/${id}/close-paper`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  journal: {
    list: (params?: { limit?: number; offset?: number }) =>
      apiFetch<PaginatedJournalEntries>("/journal/entries", { query: params }),
    get: (id: string) => apiFetch<JournalEntry>(`/journal/entries/${id}`),
    prefill: (params: { linked_proposal_id?: string; linked_position_id?: string }) =>
      apiFetch<{
        symbol: string;
        timeframe: string;
        direction: string;
        strategy_id?: string | null;
        entry_rationale: string;
        linked_proposal_id?: string | null;
        linked_position_id?: string | null;
        tags: string[];
      }>("/journal/prefill", { query: params }),
    create: (body: {
      symbol: string;
      timeframe: string;
      direction: string;
      entry_rationale: string;
      exit_rationale?: string;
      lessons?: string;
      improvement_rule?: string;
      emotions?: string[];
      mistakes?: string[];
      strategy_id?: string;
      linked_proposal_id?: string;
      linked_position_id?: string;
      result?: string;
      pnl?: string;
      stress_score?: number;
      tags?: string[];
      organization_id?: string;
      user_id?: string;
    }) =>
      apiFetch<JournalEntry>("/journal/entries", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    delete: (id: string) =>
      apiFetch<void>(`/journal/entries/${id}`, { method: "DELETE" }),
    statistics: (params?: JournalStatsParams, options?: { signal?: AbortSignal }) =>
      apiFetch<JournalStatsResponse>("/journal/statistics", { query: params, signal: options?.signal }),
    listTrades: (params?: { status?: string; source?: string; symbol?: string; limit?: number; offset?: number }) =>
      apiFetch<PaginatedCanonicalJournalTrades>("/journal/trades", { query: params }),
    importTrades: (body: JournalImportRequestBody) =>
      apiFetch<JournalImportResult>("/journal/trades/import", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    listImports: (params?: { limit?: number; offset?: number }) =>
      apiFetch<PaginatedJournalImportBatches>("/journal/imports", { query: params }),
    getImport: (id: string) => apiFetch<JournalImportBatch>(`/journal/imports/${id}`),
    comparison: (params?: JournalComparisonParams) =>
      apiFetch<JournalComparisonResponse>("/journal/comparison", { query: params }),
    setupEvidence: (params?: SetupEvidenceParams) =>
      apiFetch<SetupEvidenceResponse>("/journal/setup-evidence", { query: params }),
  },
  analytics: {
    setups: (params?: { start_date?: string; end_date?: string; setup_type?: string }) =>
      apiFetch<SetupAnalyticsResponse>("/analytics/setups", { query: params }),
    tradeReview: (params?: { start_date?: string; end_date?: string }) =>
      apiFetch<TradeReviewAnalytics>("/analytics/trade-review", { query: params }),
    discipline: (params?: { start_date?: string; end_date?: string }, options?: { signal?: AbortSignal }) =>
      apiFetch<DisciplineScoreResult>("/analytics/discipline", { query: params, signal: options?.signal }),
    riskBehavior: (params?: { start_date?: string; end_date?: string }, options?: { signal?: AbortSignal }) =>
      apiFetch<RiskBehaviorAnalytics>("/analytics/risk-behavior", { query: params, signal: options?.signal }),
  },
  learningAnalytics: {
    summary: (params?: LearningAnalyticsParams, options?: { signal?: AbortSignal }) =>
      apiFetch<LearningAnalyticsSummaryResponse>("/learning-analytics/summary", {
        query: params, signal: options?.signal,
      }),
    setupPerformance: (params?: LearningAnalyticsParams & { dimension?: SetupDimension }, options?: { signal?: AbortSignal }) =>
      apiFetch<SetupPerformanceResponse>("/learning-analytics/setup-performance", {
        query: params, signal: options?.signal,
      }),
    discipline: (params?: LearningAnalyticsParams, options?: { signal?: AbortSignal }) =>
      apiFetch<DisciplineAnalyticsResponse>("/learning-analytics/discipline", {
        query: params, signal: options?.signal,
      }),
    confidenceOutcome: (params?: LearningAnalyticsParams) =>
      apiFetch<ConfidenceOutcomeResponse>("/learning-analytics/confidence-outcome", {
        query: params,
      }),
    behaviorInsights: (params?: LearningAnalyticsParams) =>
      apiFetch<BehaviorInsightsResponse>("/learning-analytics/behavior-insights", {
        query: params,
      }),
    lessons: (params?: LearningAnalyticsParams) =>
      apiFetch<LessonThemesResponse>("/learning-analytics/lessons", {
        query: params,
      }),
    setupRanking: (params?: LearningAnalyticsParams & { dimension?: SetupDimension }, options?: { signal?: AbortSignal }) =>
      apiFetch<SetupRankingResponse>("/learning-analytics/setup-ranking", {
        query: params, signal: options?.signal,
      }),
  },
  validationPriority: {
    queue: (params?: ValidationPriorityParams & { item_type?: PriorityItemType; limit?: number }) =>
      apiFetch<ValidationPriorityQueueResponse>("/validation-priority/queue", {
        query: params,
      }),
    summary: (params?: ValidationPriorityParams) =>
      apiFetch<ValidationPrioritySummaryResponse>("/validation-priority/summary", {
        query: params,
      }),
    explain: (
      itemType: PriorityItemType,
      itemId: string,
      params?: ValidationPriorityParams,
    ) =>
      apiFetch<ValidationPriorityExplainResponse>(
        `/validation-priority/explain/${itemType}/${itemId}`,
        { query: params },
      ),
  },
  strategyQuality: {
    detectors: (
      params?: StrategyQualityParams & { condition?: string; timeframe?: string },
    ) =>
      apiFetch<StrategyQualityDetectorsResponse>("/strategy-quality/detectors", {
        query: params,
      }),
    summary: (params?: StrategyQualityParams, options?: { signal?: AbortSignal }) =>
      apiFetch<StrategyQualitySummaryResponse>("/strategy-quality/summary", {
        query: params, signal: options?.signal,
      }),
    explain: (condition: string, params?: StrategyQualityParams) =>
      apiFetch<DetectorExplainResponse>(
        `/strategy-quality/detectors/${condition}/explain`,
        { query: params },
      ),
  },
  performance: {
    portfolio: (params?: PaperPortfolioParams) =>
      apiFetch<PaperPortfolioResponse>("/performance/portfolio", {
        query: params,
        auth: true,
      }),
    snapshots: (params?: { start_date?: string; end_date?: string; limit?: number }) =>
      apiFetch<PerformanceSnapshotListResponse>("/performance/snapshots", {
        query: params,
        auth: true,
      }),
  },
  coaching: {
    prompts: (params?: CoachingParams) =>
      apiFetch<CoachingPromptsResponse>("/coaching/prompts", { query: params }),
    summary: (params?: CoachingParams) =>
      apiFetch<CoachingSummaryResponse>("/coaching/summary", { query: params }),
    explain: (category: CoachingCategory, matchedKey: string, params?: CoachingParams) =>
      apiFetch<CoachingExplainResponse>(`/coaching/prompts/${category}/${matchedKey}/explain`, {
        query: params,
      }),
    savePrompt: (body: CoachingSaveRequest) =>
      apiFetch<LessonCandidate>("/coaching/prompts/save", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  dashboard: {
    attention: generatedApi.attention,
    dailyReview: generatedApi.dailyReview,
    summary: () => apiFetch<DashboardSummary>("/dashboard/summary"),
  },
  knowledge: {
    previewFile: (file: File, title: string, sourceType: string) => {
      const body = new FormData();
      body.append("file", file);
      body.append("title", title);
      body.append("source_type", sourceType);
      return apiFetch<FileImportPreview>("/knowledge/files/preview", { method: "POST", body });
    },
    importFile: (file: File, title: string, sourceType: string, receipt: string) => {
      const body = new FormData();
      body.append("file", file);
      body.append("title", title);
      body.append("source_type", sourceType);
      body.append("preview_receipt", receipt);
      body.append("confirm", "true");
      return validatedFetch<IngestDocumentResponse>("/knowledge/files/import", { method: "POST", body, responseValidator: validators.knowledgeIngestResponse });
    },
    listDocuments: generatedApi.knowledgeDocuments,
    listChunks: generatedApi.knowledgeChunks,
    ingest: generatedApi.knowledgeIngest,
    retryIndexing: generatedApi.knowledgeRetryIndexing,
    search: (body: {
      query: string;
      top_k?: number;
      source_types?: string[];
      strategy_tag?: string;
      symbol_tag?: string;
      timeframe_tag?: string;
      risk_tag?: string;
    }) =>
      apiFetch<RagSearchResponse>("/knowledge/search", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  billing: {
    plans: () => apiFetch<SubscriptionPlan[]>("/billing/plans"),
    status: () => apiFetch<BillingStatus>("/billing/status"),
    createCustomer: (body?: { billing_email?: string }) =>
      apiFetch<BillingCustomer>("/billing/customer", {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      }),
    checkout: (planId: string) =>
      apiFetch<CheckoutResponse>("/billing/checkout", {
        method: "POST",
        body: JSON.stringify({ plan_id: planId }),
      }),
    portal: () =>
      apiFetch<PortalResponse>("/billing/portal", {
        method: "POST",
      }),
    exportUsage: () =>
      apiFetch<UsageExportResponse>("/billing/usage/export", {
        method: "POST",
        body: JSON.stringify({}),
      }),
  },
  usage: {
    summary: () => apiFetch<UsageSummary>("/usage/summary"),
    events: (params?: { limit?: number; offset?: number }) =>
      apiFetch<PaginatedUsageEvents>("/usage/events", { query: params }),
    byFeature: () => apiFetch<UsageFeatureBreakdown[]>("/usage/by-feature"),
    byProvider: () => apiFetch<UsageProviderBreakdown[]>("/usage/by-provider"),
    quota: () => apiFetch<QuotaStatus>("/usage/quota"),
    updateQuota: (body: Partial<OrganizationQuotaConfig>) =>
      apiFetch<QuotaStatus>("/usage/quota", {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
  },
  audit: {
    events: (params?: { limit?: number; offset?: number; event_type?: string }) =>
      apiFetch<PaginatedAuditRecords>("/audit/events", { query: params }),
  },
  alerts: {
    list: (params?: {
      alert_type?: string;
      severity?: string;
      unread_only?: boolean;
      limit?: number;
    }) =>
      apiFetch<{ items: PaperAlert[]; total: number }>("/alerts", { query: params, auth: true }),
    summary: () => apiFetch<PaperAlertSummary>("/alerts/summary", { auth: true }),
    setupReview: (params?: {
      status?: string;
      condition?: string;
      symbol?: string;
      timeframe?: string;
      direction?: string;
      min_confidence?: number;
      limit?: number;
      offset?: number;
    }) =>
      apiFetch<{ items: SetupAlertReviewItem[]; total: number; limit: number; offset: number }>(
        "/alerts/setup-review",
        { query: params, auth: true },
      ),
    setupReviewSummary: () =>
      apiFetch<SetupAlertReviewSummary>("/alerts/setup-review/summary", { auth: true }),
    updateSetupReview: (
      id: string,
      body: { review_status: string; review_notes?: string | null },
    ) =>
      apiFetch<SetupAlertReviewItem>(`/alerts/setup-review/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
        auth: true,
      }),
    createSetupDraft: (
      id: string,
      body: { confirm: string; notes?: string | null; risk_mode?: string },
    ) =>
      apiFetch<SetupAlertDraftCreateResult>(`/alerts/setup-review/${id}/draft`, {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    routingSummary: () =>
      apiFetch<AlertRoutingSummary>("/alerts/routing/summary", { auth: true }),
    deliveryStatus: () =>
      apiFetch<AlertDeliveryStatusResponse>("/alerts/delivery-status", { auth: true }),
    markRead: (id: string) =>
      apiFetch<PaperAlert>(`/alerts/${id}/read`, { method: "PATCH", auth: true }),
    markAllRead: () =>
      apiFetch<{ marked_read: number }>("/alerts/read-all", { method: "PATCH", auth: true }),
    deliver: (id: string) =>
      apiFetch<{ alert: PaperAlert; delivered: boolean; message: string }>(
        `/alerts/${id}/deliver`,
        { method: "POST", auth: true },
      ),
    deliverPending: () =>
      apiFetch<{ processed: number; delivered: number; failed: number }>(
        "/alerts/deliver-pending",
        { method: "POST", auth: true },
      ),
    testTelegram: (body: { confirm: string; message?: string }) =>
      apiFetch<TelegramTestAlertResponse>("/alerts/test-telegram", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    deliverTelegram: (id: string, body: { confirm: string }) =>
      apiFetch<TelegramAlertDeliveryResponse>(`/alerts/${id}/deliver-telegram`, {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    previewDelivery: (body: {
      channel?: "telegram";
      limit?: number;
      severity_min?: string;
    }) =>
      apiFetch<AlertDeliveryPreviewResponse>("/alerts/delivery/preview", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
  },
  notifications: {
    startTelegramEnrollment: () =>
      apiFetch<TelegramEnrollmentStartResponse>("/telegram-paper/enrollment/start", {
        method: "POST",
        auth: true,
      }),
    preferences: () =>
      apiFetch<NotificationPreferences>("/notifications/preferences", { auth: true }),
    updatePreferences: (body: Partial<NotificationPreferences>) =>
      apiFetch<NotificationPreferences>("/notifications/preferences", {
        method: "PATCH",
        body: JSON.stringify(body),
        auth: true,
      }),
    resetPreferences: () =>
      apiFetch<NotificationPreferences>("/notifications/preferences/reset-defaults", {
        method: "POST",
        auth: true,
      }),
    sendTest: () =>
      apiFetch<NotificationTestResult>("/notifications/test", { method: "POST", auth: true }),
  },
  watcherWatchlist: {
    configuration: () =>
      apiFetch<WatcherWatchlistConfiguration>("/watcher/watchlist", { auth: true }),
    replace: (slots: Array<Pick<WatcherWatchlistSlot, "symbol" | "enabled">>, revision: number) =>
      apiFetch<WatcherWatchlistConfiguration>("/watcher/watchlist", {
        method: "PUT",
        auth: true,
        body: JSON.stringify({ slots, revision }),
      }),
    status: () => apiFetch<WatcherWatchlistStatus>("/watcher/watchlist/status", { auth: true }),
  },
  marketWatcher: {
    status: () => apiFetch<MarketWatcherStatus>("/market-watcher/status", { auth: true }),
    summary: () => apiFetch<MarketWatcherSummary>("/market-watcher/summary", { auth: true }),
    monitoring: () =>
      apiFetch<WatcherMonitoringSnapshot>("/market-watcher/monitoring", { auth: true }),
    scan: (body: MarketWatcherScanRequest) =>
      apiFetch<MarketWatcherScanResult>("/market-watcher/scan", {
        method: "POST",
        auth: true,
        body: JSON.stringify(body),
      }),
    recentScans: (limit = 10) =>
      apiFetch<PaginatedMarketWatcherRecentScans>("/market-watcher/scans/recent", {
        auth: true,
        query: { limit },
      }),
    observations: (params?: { symbol?: string; limit?: number; offset?: number }) =>
      apiFetch<PaginatedMarketWatcherObservations>("/market-watcher/observations", {
        auth: true,
        query: params,
      }),
    bridgeStatus: () =>
      apiFetch<MarketWatcherBridgeStatus>("/market-watcher/bridge/status", { auth: true }),
    bridgeTick: () =>
      apiFetch<MarketWatcherBridgeTickResult>("/market-watcher/bridge/tick", {
        method: "POST",
        auth: true,
      }),
    bridgeHistory: (params?: { limit?: number; offset?: number }) =>
      apiFetch<PaginatedMarketWatcherBridgeHistory>("/market-watcher/bridge/history", {
        auth: true,
        query: params,
      }),
  },
  market: {
    ticker: (params?: { symbol?: string; exchange?: string }) =>
      apiFetch<TickerResponse>("/market/ticker", { query: params }),
    ohlcv: (params?: { symbol?: string; exchange?: string; timeframe?: string; limit?: number }) =>
      apiFetch<OHLCVResponse>("/market/ohlcv", { query: params }),
    snapshot: (params?: { symbol?: string; exchange?: string; timeframe?: string }) =>
      apiFetch<MarketSnapshotResponse>("/market/snapshots", { query: params }),
    analyze: (body: {
      symbol: string;
      exchange?: string;
      timeframe?: string;
      strategy_ids?: string[];
    }) =>
      apiFetch<MarketAnalyzeResponse>("/market/analyze", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    snapshots: (params?: { symbol?: string; exchange?: string; timeframe?: string }) =>
      apiFetch<MarketSnapshotResponse>("/market/snapshots", { query: params }),
  },
  strategies: {
    list: (params?: { limit?: number; offset?: number }) =>
      apiFetch<PaginatedUserStrategies>("/strategies", { query: params, auth: true }),
    get: (id: string, options?: { signal?: AbortSignal }) =>
      apiFetch<UserStrategy>(`/strategies/${id}`, { auth: true, signal: options?.signal }),
    create: (body: {
      name: string;
      setup_type: string;
      card: Record<string, unknown>;
      notes?: string;
    }) =>
      apiFetch<UserStrategy>("/strategies", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    modules: () => apiFetch<string[]>("/strategies/modules", { auth: true }),
    update: generatedApi.strategyPatch,
    createVersion: (id: string, body: { card: Record<string, unknown>; validation_status?: string }) =>
      apiFetch<{ id: string; version: number }>(`/strategies/${id}/versions`, {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    requestBacktest: (id: string, body?: BacktestRunCreate) =>
      apiFetch<BacktestRun>(`/strategies/${id}/backtests`, {
        method: "POST",
        body: JSON.stringify(body ?? {}),
        auth: true,
      }),
    listBacktests: (id: string) =>
      apiFetch<PaginatedBacktestRuns>(`/strategies/${id}/backtests`, { auth: true }),
    listBacktestTrades: (runId: string, params?: { limit?: number; offset?: number }) =>
      apiFetch<PaginatedBacktestTrades>(`/backtests/${runId}/trades`, {
        auth: true,
        query: params,
      }),
    listVersions: (id: string) =>
      apiFetch<PaginatedUserStrategyVersions>(`/strategies/${id}/versions`, { auth: true }),
    compileVersion: (strategyId: string, versionId: string) =>
      apiFetch<{
        status: string;
        compiled: { id: string; content_hash: string; compile_status: string } | null;
        failures: Array<{ code?: string; message?: string }>;
      }>(`/strategies/${strategyId}/versions/${versionId}/compile`, {
        method: "POST",
        auth: true,
      }),
    approveVersion: (strategyId: string, versionId: string, body: { confirm: string }) =>
      apiFetch<{ id: string; new_state: string }>(
        `/strategies/${strategyId}/versions/${versionId}/approve`,
        {
          method: "POST",
          body: JSON.stringify(body),
          auth: true,
        },
      ),
    paperEligibility: (id: string) =>
      apiFetch<PaperEligibilityReport>(`/strategies/${id}/paper-eligibility`, { auth: true }),
    startPaperValidation: (id: string, body?: Record<string, unknown>) =>
      apiFetch<{ id: string; status: string }>(`/strategies/${id}/paper-validation/start`, {
        method: "POST",
        body: JSON.stringify(body ?? {}),
        auth: true,
      }),
    paperValidation: (id: string) =>
      apiFetch<PaperValidationSummary>(`/strategies/${id}/paper-validation`, { auth: true }),
    scanPaperValidation: (runId: string) =>
      apiFetch<PaperScanResult>(`/paper-validation/${runId}/scan`, {
        method: "POST",
        auth: true,
      }),
    tickPaperValidation: (runId: string) =>
      apiFetch<PaperTickResult>(`/paper-validation/${runId}/tick`, {
        method: "POST",
        auth: true,
      }),
    stopPaperValidation: (runId: string) =>
      apiFetch<PaperValidationRun>(`/paper-validation/${runId}/stop`, {
        method: "POST",
        auth: true,
      }),
    paperValidationSignals: (runId: string) =>
      apiFetch<{ items: PaperSignalResult[]; total: number }>(
        `/paper-validation/${runId}/signals`,
        { auth: true },
      ),
    paperValidationTrades: (runId: string) =>
      apiFetch<{ items: PaperTradeRecord[]; total: number }>(
        `/paper-validation/${runId}/trades`,
        { auth: true },
      ),
    paperValidationMetrics: (runId: string) =>
      apiFetch<PaperValidationMetrics>(`/paper-validation/${runId}/metrics`, { auth: true }),
    schedulerStatus: () =>
      apiFetch<PaperSchedulerStatus>("/paper-validation/scheduler/status", { auth: true }),
    schedulerTick: () =>
      apiFetch<PaperSchedulerTickResult>("/paper-validation/scheduler/tick", {
        method: "POST",
        auth: true,
      }),
    schedulerHistory: (params?: { run_id?: string; limit?: number }) =>
      apiFetch<{ items: PaperRuntimeHistoryRecord[]; total: number }>(
        "/paper-validation/scheduler/history",
        { query: params, auth: true },
      ),
    drafts: (params?: { limit?: number; offset?: number }) =>
      apiFetch<{ items: PaperValidationDraftItem[]; total: number; limit: number; offset: number }>(
        "/paper-validation/drafts",
        { query: params, auth: true },
      ),
    getDraft: (id: string) =>
      apiFetch<PaperValidationDraftItem>(`/paper-validation/drafts/${id}`, { auth: true }),
    updateDraftPrep: (
      id: string,
      body: {
        prep_status?: PaperValidationDraftPrepStatus;
        thesis?: string | null;
        entry_criteria?: string | null;
        invalidation_criteria?: string | null;
        risk_notes?: string | null;
        checklist?: Partial<PaperValidationDraftChecklist>;
      },
    ) =>
      apiFetch<PaperValidationDraftItem>(`/paper-validation/drafts/${id}/prep`, {
        method: "PATCH",
        body: JSON.stringify(body),
        auth: true,
      }),
    draftSummary: () =>
      apiFetch<PaperValidationDraftSummary>("/paper-validation/drafts/summary", { auth: true }),
    queueDraft: (id: string, body: { confirm: string }) =>
      apiFetch<PaperValidationCandidateQueueResult>(`/paper-validation/drafts/${id}/queue`, {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    candidates: (params?: { limit?: number; offset?: number }) =>
      apiFetch<{ items: PaperValidationCandidateItem[]; total: number; limit: number; offset: number }>(
        "/paper-validation/candidates",
        { query: params, auth: true },
      ),
    getCandidate: (id: string) =>
      apiFetch<PaperValidationCandidateItem>(`/paper-validation/candidates/${id}`, { auth: true }),
    updateCandidateStatus: (id: string, body: { candidate_status: string }) =>
      apiFetch<PaperValidationCandidateItem>(`/paper-validation/candidates/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
        auth: true,
      }),
    candidateSummary: () =>
      apiFetch<PaperValidationCandidateSummary>("/paper-validation/candidates/summary", {
        auth: true,
      }),
    createRunPlan: (
      candidateId: string,
      body: {
        confirm: string;
        validation_window: string;
        observation_timeframe: string;
        max_duration_minutes: number;
        planned_entry_rule: string;
        planned_invalidation_rule: string;
        planned_success_criteria: string;
        planned_failure_criteria: string;
      },
    ) =>
      apiFetch<PaperValidationRunPlanCreateResult>(
        `/paper-validation/candidates/${candidateId}/plan`,
        {
          method: "POST",
          body: JSON.stringify(body),
          auth: true,
        },
      ),
    runPlans: (params?: { limit?: number; offset?: number }) =>
      apiFetch<{ items: PaperValidationRunPlanItem[]; total: number; limit: number; offset: number }>(
        "/paper-validation/run-plans",
        { query: params, auth: true },
      ),
    getRunPlan: (id: string) =>
      apiFetch<PaperValidationRunPlanItem>(`/paper-validation/run-plans/${id}`, { auth: true }),
    updateRunPlanStatus: (id: string, body: { plan_status: string }) =>
      apiFetch<PaperValidationRunPlanItem>(`/paper-validation/run-plans/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
        auth: true,
      }),
    runPlanSummary: () =>
      apiFetch<PaperValidationRunPlanSummary>("/paper-validation/run-plans/summary", {
        auth: true,
      }),
    startRunSession: (planId: string, body: { confirm: string; notes?: string | null }) =>
      apiFetch<PaperValidationRunSessionStartResult>(
        `/paper-validation/run-plans/${planId}/start`,
        {
          method: "POST",
          body: JSON.stringify(body),
          auth: true,
        },
      ),
    runSessions: (params?: { limit?: number; offset?: number }) =>
      apiFetch<{
        items: PaperValidationRunSessionItem[];
        total: number;
        limit: number;
        offset: number;
      }>("/paper-validation/run-sessions", { query: params, auth: true }),
    getRunSession: (id: string) =>
      apiFetch<PaperValidationRunSessionItem>(`/paper-validation/run-sessions/${id}`, {
        auth: true,
      }),
    updateRunSessionStatus: (id: string, body: { session_status: string }) =>
      apiFetch<PaperValidationRunSessionItem>(`/paper-validation/run-sessions/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
        auth: true,
      }),
    sessionObservations: (sessionId: string, params?: { limit?: number; offset?: number }) =>
      apiFetch<{
        items: PaperValidationSessionObservationItem[];
        total: number;
        limit: number;
        offset: number;
      }>(`/paper-validation/run-sessions/${sessionId}/observations`, {
        query: params,
        auth: true,
      }),
    recordObservation: (
      sessionId: string,
      body: {
        confirm: string;
        observation_kind: string;
        observed_price?: number | null;
        observed_at?: string | null;
        note?: string | null;
      },
    ) =>
      apiFetch<PaperValidationSessionObservationItem>(
        `/paper-validation/run-sessions/${sessionId}/observations`,
        {
          method: "POST",
          body: JSON.stringify(body),
          auth: true,
        },
      ),
    getSessionResult: (sessionId: string) =>
      apiFetch<PaperValidationSessionResultItem>(
        `/paper-validation/run-sessions/${sessionId}/result`,
        { auth: true },
      ),
    recordSessionResult: (
      sessionId: string,
      body: Record<string, unknown> & { confirm: string },
    ) =>
      apiFetch<PaperValidationSessionResultCreateResult>(
        `/paper-validation/run-sessions/${sessionId}/result`,
        {
          method: "POST",
          body: JSON.stringify(body),
          auth: true,
        },
      ),
    updateSessionResult: (sessionId: string, body: Record<string, unknown>) =>
      apiFetch<PaperValidationSessionResultItem>(
        `/paper-validation/run-sessions/${sessionId}/result`,
        {
          method: "PATCH",
          body: JSON.stringify(body),
          auth: true,
        },
      ),
    testability: (id: string) =>
      apiFetch<StrategyTestability>(`/strategies/${id}/testability`, { auth: true }),
    patchStructuredRules: (id: string, body: Partial<StructuredRules>) =>
      apiFetch<StructuredRules>(`/strategies/${id}/structured-rules`, {
        method: "PATCH",
        body: JSON.stringify(body),
        auth: true,
      }),
    validateStructuredRules: (id: string, body: StructuredRules) =>
      apiFetch<{ valid: boolean; errors: string[]; warnings: string[] }>(
        `/strategies/${id}/structured-rules/validate`,
        { method: "POST", body: JSON.stringify(body), auth: true },
      ),
    structureFromText: (id: string, text: string) =>
      apiFetch<{ draft?: StructuredRules; validation: { valid: boolean } }>(
        `/strategies/${id}/structure-from-text`,
        { method: "POST", body: JSON.stringify({ text }), auth: true },
      ),
  },
  manualLevels: {
    list: (params?: { symbol?: string; exchange?: string }) =>
      apiFetch<PaginatedManualChartLevels>("/manual-levels", { query: params, auth: true }),
    create: (body: Record<string, unknown>) =>
      apiFetch<ManualChartLevel>("/manual-levels", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    update: (id: string, body: Record<string, unknown>) =>
      apiFetch<ManualChartLevel>(`/manual-levels/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
        auth: true,
      }),
    delete: (id: string) =>
      apiFetch<void>(`/manual-levels/${id}`, { method: "DELETE", auth: true }),
  },
  pretrade: {
    analyze: (body: Record<string, unknown>) =>
      apiFetch<PreTradeAnalyzeResponse>("/pretrade/analyze", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
  },
  risk: {
    settings: () => apiFetch<UserRiskSettings>("/risk/settings", { auth: true }),
    updateSettings: (body: UserRiskSettingsUpdate) =>
      apiFetch<UserRiskSettings>("/risk/settings", {
        method: "PATCH",
        body: JSON.stringify(body),
        auth: true,
      }),
    resetSettings: () =>
      apiFetch<UserRiskSettings>("/risk/settings/reset-defaults", {
        method: "POST",
        auth: true,
      }),
    size: (body: Record<string, unknown>) =>
      apiFetch<PositionSizingResult>("/risk/size", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    lossAcceptance: (body: { planned_loss_amount: string; accepted: boolean }) =>
      apiFetch<{ can_execute_paper: boolean; recommendation: string }>("/risk/loss-acceptance", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    killSwitch: () => apiFetch<KillSwitchStatus>("/risk/kill-switch", { auth: true }),
    activateKillSwitch: (body: KillSwitchMutationRequest) =>
      apiFetch<KillSwitchStatus>("/risk/kill-switch/activate", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    deactivateKillSwitch: (body: KillSwitchMutationRequest) =>
      apiFetch<KillSwitchStatus>("/risk/kill-switch/deactivate", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
  },
  humanVsSystem: {
    compare: (tradeId: string) =>
      apiFetch<HumanVsSystemComparison>(`/human-vs-system/${tradeId}`, { auth: true }),
    analyze: (tradeId: string) =>
      apiFetch<HumanVsSystemComparison>(`/human-vs-system/${tradeId}/analyze`, { auth: true }),
  },
  journalDiscipline: {
    analyze: (journalId: string) =>
      apiFetch<{
        journal_entry_id: string;
        comparison: HumanVsSystemComparison;
        lessons_generated: string[];
        lesson_candidate_ids: string[];
      }>(`/journal/entries/${journalId}/discipline-analysis`, { auth: true }),
  },
  backtests: {
    get: (id: string) => apiFetch<BacktestRun>(`/backtests/${id}`, { auth: true }),
    listTrades: (id: string, params?: { limit?: number; offset?: number }) =>
      apiFetch<PaginatedBacktestTrades>(`/backtests/${id}/trades`, { auth: true, query: params }),
    cancel: (id: string) =>
      apiFetch<BacktestRun>(`/backtests/${id}/cancel`, { method: "POST", auth: true }),
    verify: (id: string) =>
      apiFetch<BacktestVerifyResult>(`/backtests/${id}/verify`, { method: "POST", auth: true }),
    journalTrades: (id: string, body: BacktestJournalRequest) =>
      apiFetch<BacktestJournalResult>(`/backtests/${id}/journal-trades`, {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
  },
  researchValidation: {
    evidence: (params?: ResearchValidationEvidenceParams) =>
      apiFetch<ResearchValidationEvidenceResponse>("/research-validation/evidence", {
        query: params,
        auth: true,
      }),
    promote: (body: { confirm: string; backtest_run_id: string }) =>
      apiFetch<ResearchValidationPromoteResult>("/research-validation/promote", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    backtestStatus: (backtestRunId: string) =>
      apiFetch<ResearchValidationStatusResponse>(
        `/research-validation/backtests/${backtestRunId}/status`,
        { auth: true },
      ),
  },
  lessons: {
    listCandidates: (params?: { status?: string; mistake_type?: string }) => {
      const q = new URLSearchParams();
      if (params?.status) q.set("status", params.status);
      if (params?.mistake_type) q.set("mistake_type", params.mistake_type);
      const suffix = q.toString() ? `?${q}` : "";
      return apiFetch<PaginatedLessonCandidates>(`/lessons/candidates${suffix}`, { auth: true });
    },
    getCandidate: (id: string) =>
      apiFetch<LessonCandidate>(`/lessons/candidates/${id}`, { auth: true }),
    createCandidate: (body: Record<string, unknown>) =>
      apiFetch<LessonCandidate>("/lessons/candidates", {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      }),
    accept: (id: string, body: Record<string, unknown>) =>
      apiFetch<LessonCandidate>(`/lessons/candidates/${id}/accept`, {
        method: "PATCH",
        body: JSON.stringify(body),
        auth: true,
      }),
    reject: (id: string, body: Record<string, unknown>) =>
      apiFetch<LessonCandidate>(`/lessons/candidates/${id}/reject`, {
        method: "PATCH",
        body: JSON.stringify(body),
        auth: true,
      }),
    listAccepted: (params?: { mistake_type?: string }) => {
      const q = new URLSearchParams();
      if (params?.mistake_type) q.set("mistake_type", params.mistake_type);
      const suffix = q.toString() ? `?${q}` : "";
      return apiFetch<PaginatedLessonCandidates>(`/lessons/accepted${suffix}`, { auth: true });
    },
  },
};

export type { AuditRecord };
export { ApiError } from "@/lib/api/client";
