/** Typed models mirroring backend Pydantic schemas. */

export type RiskSeverity = "info" | "low" | "medium" | "high" | "critical";
export type TradeDirection = "long" | "short";
export type ProposalStatus =
  | "draft"
  | "pending_approval"
  | "approved"
  | "rejected"
  | "expired"
  | "executed"
  | "cancelled";
export type ApprovalStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "modified"
  | "paused"
  | "cancelled"
  | "closed"
  | "needs_more_analysis";
export type PositionStatus = "open" | "closed" | "liquidated";
/** Native candle timeframes mirrored from the backend Timeframe enum. */
export const TIMEFRAMES = [
  "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "3d", "1w",
] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];
export type StrategyId =
  | "htf_trend_pullback"
  | "liquidity_sweep_reversal"
  | "countertrend_short_build"
  | "passive_level_order"
  | "profit_protection"
  | "green_day_guard"
  | "mental_capital_guard"
  | "nested_continuation"
  | "sfp"
  | "manual_review";
/** Backend DocumentSourceType values (RAG corpus). */
export type DocumentSourceType =
  | "trading_playbook"
  | "product_requirements"
  | "system_architecture"
  | "risk_policy"
  | "strategy_template"
  | "trade_journal"
  | "review_note"
  | "mistakes_database"
  | "general_note";
export type AuditSeverity = "info" | "warning" | "error" | "critical";
export type AuditEventType = string;
export type ProviderKind =
  | "llm"
  | "embeddings"
  | "vector"
  | "exchange"
  | "market_data"
  | "news"
  | "notifications"
  | "tracing"
  | "email";
export type ProviderHealth = "healthy" | "degraded" | "unavailable";

export interface HealthResponse {
  status: string;
  app: string;
  version: string;
  environment: string;
  execution_mode: string;
  real_trading_enabled: boolean;
  exchange_mode?: string;
  must_verify_email: boolean;
  demo_seed_enabled?: boolean;
  market_watcher_enabled?: boolean;
  market_watcher_bridge_enabled?: boolean;
  watcher_orchestration_enabled?: boolean;
  watcher_paper_staging_activation?: boolean;
  telegram_alerts_enabled?: boolean;
  telegram_interaction_enabled?: boolean;
  automatic_telegram_delivery_enabled?: boolean;
  telegram_paper_activation_armed?: boolean;
  telegram_inbound_mode?: "off" | "polling" | "webhook";
  telegram_network_permitted?: boolean;
  perpetual_evidence_source?: "replay" | "binance_usdm";
  perpetual_evidence_activation?: "inactive" | "active" | "refused";
  perpetual_evidence_intended_staging_source?: "binance_usdm";
  perpetual_evidence_rollback_source?: "replay";
  live_market_read_only?: true;
  exchange_credentials_used_for_market_evidence?: false;
  spot_fallback_permitted?: false;
  fabricated_fallback_permitted?: false;
  live_quote_freshness_seconds?: 10;
  first_perpetual_symbol?: "BTCUSDT";
  git_sha?: string | null;
  worker_runtime?: WorkerRuntimeObservation;
  timestamp: string;
}

export interface WorkerComponentObservation {
  available?: boolean;
  worker_id?: string;
  heartbeat_at?: string | null;
  activation_state?: string;
  lease_owner?: string;
  lease_epoch?: number;
  lease_expires_at?: string | null;
  fence_held?: boolean;
  last_scan_at?: string | null;
  last_scan_reason?: string;
  market_source?: string;
  freshness_seconds?: number | null;
  telegram_runtime_state?: string;
  inbound_mode?: string;
  outbox_pending?: number;
  outbox_retryable?: number;
  outbox_dead_letter?: number;
  last_delivery_at?: string | null;
  last_error_code?: string;
  kill_switch_active?: boolean;
  request_count?: number;
  request_weight?: number;
  rate_limited_count?: number;
  cache_hits?: number;
  health_state?: "RUNNING" | "STALE" | "UNAVAILABLE";
  heartbeat_age_seconds?: number | null;
  heartbeat_stale_after_seconds?: number;
}

export interface WorkerRuntimeObservation {
  available?: boolean;
  watcher?: WorkerComponentObservation;
  telegram?: WorkerComponentObservation;
}

export interface ReadinessResponse {
  status: string;
  ready: boolean;
  providers_total: number;
  providers_unavailable: number;
  timestamp: string;
}

export interface ProviderStatus {
  name: string;
  kind: ProviderKind;
  health: ProviderHealth;
  using_fallback: boolean;
  is_mock: boolean;
  detail?: string | null;
  last_success_at?: string | null;
  error_message?: string | null;
}

export interface ProviderStatusResponse {
  generated_at: string;
  providers: ProviderStatus[];
}

export interface TakeProfitLevel {
  price: string;
  size_fraction: number;
}

export interface ExitCriteria {
  invalidation: string;
  stop_loss: string;
  take_profits: TakeProfitLevel[];
  breakeven_trigger?: string | null;
  runner_enabled?: boolean;
  runner_notes?: string | null;
}

export interface RiskCheckResult {
  action: "allow" | "warn" | "block";
  severity: RiskSeverity;
  triggered_rules: Array<{
    rule_id: string;
    action: string;
    severity: RiskSeverity;
    message: string;
  }>;
  summary: string;
}

export interface TradeProposal {
  id: string;
  organization_id: string;
  user_id: string;
  signal_id?: string | null;
  strategy_id: StrategyId;
  symbol: string;
  timeframe: Timeframe;
  direction: TradeDirection;
  entry_price: string;
  entry_low?: string | null;
  entry_high?: string | null;
  position_size: string;
  leverage: string;
  exit: ExitCriteria;
  confidence: number;
  risk_level: RiskSeverity;
  rationale: string;
  status: ProposalStatus;
  approval_required: boolean;
  risk_result?: RiskCheckResult | null;
  loss_acceptance_required?: boolean;
  loss_acceptance_status?: string;
  planned_loss_amount?: string | null;
  created_at: string;
}

export interface PaginatedTradeProposals {
  items: TradeProposal[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApprovalAuthorization {
  authorization_id: string;
  approval_request_id: string;
  organization_id: string;
  user_id: string;
  account_id: string;
  revision_id: string;
  plan_id: string;
  plan_content_hash: string;
  state: string;
  expires_at: string;
}

export interface ApprovalRequest {
  id: string;
  proposal_id: string;
  plan_revision_id?: string | null;
  organization_id: string;
  user_id: string;
  status: ApprovalStatus;
  proposed_action?: string | null;
  modified_fields?: Record<string, string> | null;
  risk_level: RiskSeverity;
  confidence: number;
  approval_reason?: string | null;
  audit_event_id?: string | null;
  authorization?: ApprovalAuthorization | null;
  created_at: string;
  decided_at?: string | null;
}

export interface PaginatedApprovalRequests {
  items: ApprovalRequest[];
  total: number;
  limit: number;
  offset: number;
}

export interface ProposalWorkflowView {
  proposal: TradeProposal;
  approval: ApprovalRequest | null;
  can_execute_paper: boolean;
  block_reason: string | null;
}

export interface ApprovalWorkflowView {
  approval: ApprovalRequest;
  proposal: TradeProposal | null;
  can_execute_paper: boolean;
  block_reason: string | null;
}

export interface Position {
  id: string;
  organization_id: string;
  user_id: string;
  symbol: string;
  direction: TradeDirection;
  size: string;
  entry_price: string;
  leverage: string;
  stop_loss?: string | null;
  take_profits: TakeProfitLevel[];
  liquidation_price?: string | null;
  unrealized_pnl: string;
  realized_pnl: string;
  risk_state: Record<string, string>;
  status: PositionStatus;
  opened_at: string;
  closed_at?: string | null;
}

export interface PaginatedPositions {
  items: Position[];
  total: number;
  limit: number;
  offset: number;
}

export interface WatchlistItem {
  id: string;
  organization_id: string;
  user_id: string;
  symbol: string;
  exchange: string;
  timeframes: Timeframe[];
  strategy_ids: StrategyId[];
  enabled: boolean;
  created_at: string;
}

export interface JournalEntry {
  id: string;
  organization_id: string;
  user_id: string;
  symbol: string;
  timeframe: Timeframe;
  direction: TradeDirection;
  strategy_id?: StrategyId | null;
  entry_rationale: string;
  exit_rationale?: string | null;
  emotions: string[];
  mistakes: string[];
  lessons?: string | null;
  improvement_rule?: string | null;
  result: string;
  pnl?: string | null;
  stress_score?: number | null;
  tags: string[];
  screenshot_refs: string[];
  linked_proposal_id?: string | null;
  linked_position_id?: string | null;
  rag_synced?: boolean;
  created_at: string;
}

export interface SetupStatistics {
  setup_type: StrategyId;
  proposal_count: number;
  paper_trade_count: number;
  winning_paper_trades: number;
  losing_paper_trades: number;
  average_paper_pnl?: string | null;
  average_risk_level?: string | null;
  average_confidence?: number | null;
  most_common_mistakes: string[];
  most_common_lessons: string[];
  last_used_at?: string | null;
}

export interface SetupAnalyticsResponse {
  organization_id: string;
  user_id: string;
  setup_type_filter?: StrategyId | null;
  date_range: { start?: string | null; end?: string | null };
  setups: SetupStatistics[];
}

export interface TradeReviewAnalytics {
  total_journaled_trades: number;
  win_count: number;
  loss_count: number;
  average_pnl?: string | null;
  most_frequent_setup_type?: StrategyId | null;
  most_frequent_mistake_tag?: string | null;
  most_frequent_emotion_tag?: string | null;
  trades_after_daily_loss_warning: number;
  trades_after_green_day_warning: number;
  trades_blocked_by_risk_engine: number;
  proposals_rejected_by_user: number;
  proposals_needing_more_analysis: number;
}

export interface DisciplineScoreResult {
  score: number;
  grade: string;
  positive_behaviors: string[];
  negative_behaviors: string[];
  improvement_suggestions: string[];
}

// --- Learning analytics (Slice 84 — read-only, record derived) ---

export type SetupDimension =
  | "condition"
  | "timeframe"
  | "symbol"
  | "direction"
  | "confidence_bucket";

export type LearningAnalyticsParams = {
  start_date?: string;
  end_date?: string;
  user_id?: string;
  min_sample?: number;
};

interface LearningAnalyticsContext {
  organization_id: string;
  user_id?: string | null;
  date_range: { start?: string | null; end?: string | null };
  min_sample: number;
}

export interface LearningAnalyticsFunnel {
  alerts: number;
  drafts: number;
  candidates: number;
  run_plans: number;
  run_sessions: number;
  completed_sessions: number;
  cancelled_sessions: number;
  results: number;
}

export interface OutcomeDistributionItem {
  outcome: string;
  count: number;
  rate?: number | null;
}

export interface RateMetrics {
  success_rate?: number | null;
  failure_rate?: number | null;
  invalidated_rate?: number | null;
  missed_entry_rate?: number | null;
  no_trade_rate?: number | null;
  inconclusive_rate?: number | null;
  behaved_as_expected_rate?: number | null;
  invalidation_hit_rate?: number | null;
}

export interface ObservationMetrics {
  total_observations: number;
  average_per_session?: number | null;
  by_kind: Record<string, number>;
}

export interface LearningAnalyticsSummaryResponse extends LearningAnalyticsContext {
  funnel: LearningAnalyticsFunnel;
  total_sessions: number;
  completed_sessions: number;
  cancelled_sessions: number;
  results_count: number;
  outcome_distribution: OutcomeDistributionItem[];
  rates: RateMetrics;
  observations: ObservationMetrics;
  average_minutes_to_outcome?: number | null;
  lessons_count: number;
}

export interface SetupPerformanceGroup {
  dimension_value: string;
  sample_size: number;
  insufficient_data: boolean;
  quality_score?: number | null;
  success_rate?: number | null;
  failure_rate?: number | null;
  invalidation_hit_rate?: number | null;
  behaved_as_expected_rate?: number | null;
  outcome_distribution: OutcomeDistributionItem[];
}

export interface SetupPerformanceResponse extends LearningAnalyticsContext {
  dimension: SetupDimension;
  groups: SetupPerformanceGroup[];
}

export interface DisciplineAnalyticsResponse extends LearningAnalyticsContext {
  sample_size: number;
  insufficient_data: boolean;
  discipline_score?: number | null;
  discipline_grade: string;
  discipline_breakdown: Record<string, number>;
  entry_breakdown: Record<string, number>;
  issue_frequency: Record<string, number>;
  positive_behaviors: string[];
  negative_behaviors: string[];
  improvement_suggestions: string[];
}

export interface ConfidenceBucketStat {
  bucket: string;
  lower: number;
  upper: number;
  sample_size: number;
  insufficient_data: boolean;
  success_rate?: number | null;
}

export interface ConfidenceOutcomeResponse extends LearningAnalyticsContext {
  buckets: ConfidenceBucketStat[];
  correlation: string;
}

export interface BehaviorInsight {
  code: string;
  message: string;
  severity: string;
  sample_size: number;
  confidence: string;
}

export interface BehaviorInsightsResponse extends LearningAnalyticsContext {
  insights: BehaviorInsight[];
}

export interface LessonTheme {
  theme: string;
  count: number;
  example_excerpt?: string | null;
}

export interface LessonThemesResponse extends LearningAnalyticsContext {
  lessons_count: number;
  themes: LessonTheme[];
}

export interface SetupRankingItem {
  setup_key: string;
  rank: number;
  quality_score: number;
  sample_size: number;
}

export interface SetupRankingResponse extends LearningAnalyticsContext {
  dimension: SetupDimension;
  note: string;
  ranked: SetupRankingItem[];
}

// --- Validation prioritization (Slice 85 — read-only, record derived) ---

export type PriorityItemType = "run_plan" | "candidate";

export type ValidationActionLabel =
  | "prioritize"
  | "watch"
  | "collect_more_data"
  | "avoid_for_now";

export type ReliabilityTier = "none" | "low" | "medium" | "high";

export type FactorDirection = "positive" | "negative" | "neutral";

export type ValidationPriorityParams = {
  start_date?: string;
  end_date?: string;
  user_id?: string;
  min_sample?: number;
};

interface ValidationPriorityContext {
  organization_id: string;
  user_id?: string | null;
  date_range: { start?: string | null; end?: string | null };
  min_sample: number;
  note: string;
}

export interface PriorityFactor {
  code: string;
  label: string;
  direction: FactorDirection;
  contribution: number;
  detail: string;
}

export interface ValidationPriorityItem {
  item_type: PriorityItemType;
  item_id: string;
  symbol?: string | null;
  condition?: string | null;
  timeframe?: string | null;
  direction?: string | null;
  confidence?: number | null;
  confidence_bucket?: string | null;
  current_status: string;
  priority_score: number;
  action_label: ValidationActionLabel;
  reliability: ReliabilityTier;
  matched_dimension: string;
  matched_key: string;
  matched_sample_size: number;
  historical_success_rate?: number | null;
  historical_invalidation_rate?: number | null;
  factors: PriorityFactor[];
  rationale: string[];
}

export interface ValidationPriorityQueueResponse extends ValidationPriorityContext {
  item_type_filter?: PriorityItemType | null;
  limit: number;
  total_pending: number;
  items: ValidationPriorityItem[];
}

export interface ActionLabelCount {
  action_label: ValidationActionLabel;
  count: number;
}

export interface ReliabilityCount {
  reliability: ReliabilityTier;
  count: number;
}

export interface ValidationPrioritySummaryResponse extends ValidationPriorityContext {
  total_pending: number;
  run_plans_pending: number;
  candidates_pending: number;
  by_action: ActionLabelCount[];
  by_reliability: ReliabilityCount[];
}

export interface ValidationPriorityExplainResponse extends ValidationPriorityContext {
  item: ValidationPriorityItem;
}

// --- Strategy quality & detector performance (Slice 89 — read-only) ---

export type DetectorTrustTier = "none" | "low" | "medium" | "high";

export type DetectorVerdict =
  | "trusted"
  | "watch"
  | "improve"
  | "avoid_for_now"
  | "needs_more_validation";

export type CalibrationLabel =
  | "well_calibrated"
  | "overconfident"
  | "underconfident"
  | "insufficient_data";

export type StrategyQualityParams = {
  start_date?: string;
  end_date?: string;
  user_id?: string;
  min_sample?: number;
};

interface StrategyQualityContext {
  organization_id: string;
  user_id?: string | null;
  date_range: { start?: string | null; end?: string | null };
  min_sample: number;
  note: string;
}

export interface DetectorFactor {
  code: string;
  label: string;
  direction: FactorDirection;
  contribution: number;
  detail: string;
}

export interface DetectorWarning {
  code: string;
  message: string;
  severity: string;
}

export interface ConfidenceCalibration {
  mean_confidence?: number | null;
  mean_success_rate?: number | null;
  correlation: string;
  calibration_label: CalibrationLabel;
  buckets: ConfidenceBucketStat[];
}

export interface DetectorTimeframeStat {
  condition: string;
  timeframe: string;
  sample_size: number;
  insufficient_data: boolean;
  invalidation_rate?: number | null;
  success_rate?: number | null;
}

export interface DetectorQualityReport {
  condition: string;
  detector_version?: string | null;
  sample_size: number;
  insufficient_data: boolean;
  trust_tier: DetectorTrustTier;
  verdict: DetectorVerdict;
  quality_score?: number | null;
  raw_quality_score?: number | null;
  success_rate?: number | null;
  failure_rate?: number | null;
  invalidated_rate?: number | null;
  missed_entry_rate?: number | null;
  no_trade_rate?: number | null;
  inconclusive_rate?: number | null;
  invalidation_hit_rate?: number | null;
  behaved_as_expected_rate?: number | null;
  should_have_avoided_rate?: number | null;
  should_have_waited_rate?: number | null;
  outcome_distribution: OutcomeDistributionItem[];
  discipline_breakdown: Record<string, number>;
  entry_breakdown: Record<string, number>;
  confidence_calibration: ConfidenceCalibration;
  warnings: DetectorWarning[];
  factors: DetectorFactor[];
  rationale: string[];
}

export interface StrategyQualityDetectorsResponse extends StrategyQualityContext {
  condition_filter?: string | null;
  timeframe_filter?: string | null;
  detectors: DetectorQualityReport[];
}

export interface TrustTierCount {
  trust_tier: DetectorTrustTier;
  count: number;
}

export interface VerdictCount {
  verdict: DetectorVerdict;
  count: number;
}

export interface DetectorRankItem {
  condition: string;
  rank: number;
  quality_score: number;
  sample_size: number;
  trust_tier: DetectorTrustTier;
  verdict: DetectorVerdict;
}

export interface StrategyQualitySummaryResponse extends StrategyQualityContext {
  total_detectors: number;
  detectors_with_data: number;
  total_results: number;
  by_trust_tier: TrustTierCount[];
  by_verdict: VerdictCount[];
  ranked: DetectorRankItem[];
  warnings: DetectorWarning[];
}

export interface DetectorExplainResponse extends StrategyQualityContext {
  report: DetectorQualityReport;
  timeframes: DetectorTimeframeStat[];
}

// --- Paper portfolio (Slice 91B — read-only) ---

export type PortfolioSourceFilter = "all" | "proposal_flow" | "paper_validation";

export type PaperPortfolioParams = {
  start_date?: string;
  end_date?: string;
  source?: PortfolioSourceFilter;
  symbol?: string;
  setup?: string;
  timeframe?: string;
  timezone?: string;
};

export interface PaperPortfolioSafetyBanner {
  execution_mode: string;
  paper_only: boolean;
  real_trading_enabled: boolean;
  disclaimer: string;
}

export interface PaperPortfolioAccount {
  starting_balance: string;
  current_equity: string;
  cumulative_realized_pnl: string;
  unrealized_pnl: string | null;
  open_trade_count: number;
  closed_trade_count: number;
  as_of: string;
  limitations: string[];
}

export interface PerformanceMetrics {
  trade_count: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate: number;
  net_pnl: string;
  gross_profit: string;
  gross_loss: string;
  total_fees: string;
  total_funding: string;
  avg_win: string;
  avg_loss: string;
  expectancy: string;
  profit_factor: number | null;
  avg_r_multiple: number | null;
  max_drawdown: string;
  max_drawdown_pct: number | null;
  avg_duration_seconds: number | null;
  violations: number;
  equity_curve: { index: number; timestamp: string | null; cumulative_pnl: string }[];
}

export interface PortfolioGroupBreakdown {
  key: string;
  metrics: PerformanceMetrics;
}

export interface DollarEquityPoint {
  index: number;
  timestamp: string | null;
  equity: string;
  cumulative_realized_pnl: string;
  unrealized_pnl: string | null;
  event: "start" | "trade_close" | "live";
}

export interface DailyPortfolioPoint {
  date: string;
  starting_equity: string;
  ending_equity: string;
  daily_pnl: string;
  daily_drawdown: string;
  daily_drawdown_pct: number | null;
  trades_closed: number;
}

export type PortfolioTrendLabel =
  | "improving"
  | "flat"
  | "deteriorating"
  | "insufficient_data";

export interface PortfolioTrend {
  label: PortfolioTrendLabel;
  window_days: number;
  recent_net_pnl: string | null;
  prior_net_pnl: string | null;
  rationale: string;
}

export interface OpenExposureSummary {
  open_trade_count: number;
  proposal_flow_count: number;
  paper_validation_count: number;
  unrealized_pnl_total: string | null;
  notional_exposure: string | null;
  limitations: string[];
}

export interface PortfolioFiltersApplied {
  start_date: string | null;
  end_date: string | null;
  source: string;
  symbol: string | null;
  setup: string | null;
  timeframe: string | null;
  timezone: string;
}

export interface PortfolioBreakdowns {
  by_symbol: PortfolioGroupBreakdown[];
  by_setup: PortfolioGroupBreakdown[];
  by_timeframe: PortfolioGroupBreakdown[];
  by_strategy: PortfolioGroupBreakdown[];
  by_source: PortfolioGroupBreakdown[];
  by_detector: PortfolioGroupBreakdown[];
}

export interface PaperPortfolioResponse {
  safety: PaperPortfolioSafetyBanner;
  account: PaperPortfolioAccount;
  metrics: PerformanceMetrics;
  open_exposure: OpenExposureSummary;
  equity_curve: DollarEquityPoint[];
  daily_series: DailyPortfolioPoint[];
  breakdowns: PortfolioBreakdowns;
  trend: PortfolioTrend;
  date_range: { start?: string | null; end?: string | null } | null;
  filters_applied: PortfolioFiltersApplied;
}

export interface PerformanceSnapshotListItem {
  id: string;
  scope: string;
  as_of: string;
  trade_count: number;
  net_pnl: string;
  win_rate: number;
  profit_factor: number | null;
  max_drawdown: string;
  max_drawdown_pct: number | null;
}

export interface PerformanceSnapshotListResponse {
  items: PerformanceSnapshotListItem[];
  total: number;
}

export type CoachingCategory =
  | "missed_entry"
  | "should_have_waited"
  | "should_have_avoided"
  | "invalidation_hit"
  | "low_quality_setup"
  | "overconfidence"
  | "weak_confidence_correlation";

export type CoachingLessonSeverity = "low" | "medium" | "high" | "critical";

export type CoachingParams = {
  start_date?: string;
  end_date?: string;
  user_id?: string;
  min_sample?: number;
  category?: CoachingCategory;
  severity?: CoachingLessonSeverity;
  limit?: number;
};

interface CoachingContext {
  organization_id: string;
  user_id?: string | null;
  date_range: { start?: string | null; end?: string | null };
  min_sample: number;
  note: string;
}

export interface CoachingSource {
  matched_dimension: string;
  matched_key: string;
  sample_size: number;
  source_session_ids: string[];
  analytics_codes: string[];
  rate?: number | null;
}

export interface CoachingFactor {
  code: string;
  label: string;
  direction: FactorDirection;
  contribution: number;
  detail: string;
}

export interface CoachingPrompt {
  signature: string;
  category: CoachingCategory;
  title: string;
  prompt_text: string;
  severity: CoachingLessonSeverity;
  reliability: ReliabilityTier;
  concern_score: number;
  insufficient_data: boolean;
  source: CoachingSource;
  factors: CoachingFactor[];
  rationale: string[];
  already_saved_lesson_id?: string | null;
}

export interface CoachingPromptsResponse extends CoachingContext {
  total: number;
  items: CoachingPrompt[];
}

export interface CategoryCount {
  category: CoachingCategory;
  count: number;
}

export interface SeverityCount {
  severity: CoachingLessonSeverity;
  count: number;
}

export interface CoachingSummaryResponse extends CoachingContext {
  total_open: number;
  pending_coaching_lessons: number;
  by_category: CategoryCount[];
  by_severity: SeverityCount[];
  top_prompt?: CoachingPrompt | null;
}

export interface CoachingExplainResponse extends CoachingContext {
  prompt: CoachingPrompt;
}

export interface CoachingSaveRequest {
  category: CoachingCategory;
  matched_dimension: string;
  matched_key: string;
  min_sample?: number;
  start_date?: string;
  end_date?: string;
  reviewer_note?: string;
}

export interface RiskBehaviorAnalytics {
  risk_blocks_count: number;
  daily_loss_warnings: number;
  green_day_warnings: number;
  overtrading_warnings: number;
  revenge_trading_warnings: number;
  proposals_rejected: number;
  proposals_needs_more_analysis: number;
  paper_orders_rejected: number;
  approval_pending_count: number;
  approval_approved_count: number;
  journal_completion_rate: number;
  triggered_rules: Record<string, number>;
}

export interface DashboardSafetyStatus {
  execution_mode: string;
  paper_only: boolean;
  real_trading_enabled: boolean;
  real_trading_disabled: boolean;
}

export interface DailyDisciplineSnapshot {
  date: string;
  timezone: string;
  trades_today: number;
  paper_trades_opened_today: number;
  paper_trades_closed_today: number;
  journal_entries_today: number;
  realized_pnl_today_paper: string | null;
  unrealized_pnl_paper: string | null;
  net_pnl_today_paper: string | null;
  daily_loss_limit: string | null;
  daily_target: string | null;
  loss_lock_active: boolean;
  green_day_protection_active: boolean;
  overtrading_warning_active: boolean;
  max_trades_per_day: number | null;
  remaining_trades_allowed: number | null;
  discipline_status: "calm" | "caution" | "locked" | "review_only" | string;
  risk_settings_source: "configured_daily_state" | "user_risk_settings" | "system_default" | string;
  pnl_sources: Record<string, string | null>;
  reasons: string[];
  recommended_action: string;
  limitations: string[];
}

export interface StrategyReadinessCounts {
  needs_structure: number;
  ready_for_backtest: number;
  needs_more_sample: number;
  paper_eligible: number;
  paper_validation_running: number;
  paper_validated: number;
  restricted: number;
}

export interface StrategyActionItem {
  strategy_id: string;
  name: string;
  status: string;
  next_action: string;
  blockers: string[];
  link_hint: string;
}

export interface StrategyReadinessSummary {
  counts: StrategyReadinessCounts;
  top_needing_action: StrategyActionItem[];
  limitations: string[];
}

export interface ActivePaperValidationItem {
  strategy_id: string;
  name: string;
  status: string;
}

export interface OpenPaperTradeItem {
  journal_trade_id?: string | null;
  account_id?: string | null;
  exchange?: string | null;
  entry_price?: string | null;
  position_id: string | null;
  paper_trade_id?: string | null;
  strategy_id?: string | null;
  strategy_name?: string | null;
  symbol: string;
  direction: string;
  unrealized_pnl: string | null;
  status: string;
  source?: string;
}

export interface DisciplineScoreSummary {
  score: number | null;
  grade: string | null;
  band: "strong" | "good" | "caution" | "review_needed" | string | null;
  main_contributors: string[];
  limitations: string[];
}

export interface OpenPaperTradesSummary {
  paper_execution_count?: number;
  proposal_flow_count: number;
  paper_validation_count: number;
  total_count: number;
  total_open_exposure: string | null;
  items: OpenPaperTradeItem[];
  limitations: string[];
}

export interface UserRiskSettings {
  organization_id: string;
  user_id: string;
  daily_loss_limit: string | null;
  daily_target: string | null;
  max_trades_per_day: number;
  max_risk_per_trade_percent: string;
  default_account_balance: string;
  timezone: string;
  green_day_protection_enabled: boolean;
  one_loss_stop_enabled: boolean;
  overtrading_guard_enabled: boolean;
  notes: string | null;
  using_defaults: boolean;
  timezone_fallback: boolean;
}

export interface UserRiskSettingsUpdate {
  daily_loss_limit?: string | null;
  daily_target?: string | null;
  max_trades_per_day?: number;
  max_risk_per_trade_percent?: string;
  default_account_balance?: string;
  timezone?: string;
  green_day_protection_enabled?: boolean;
  one_loss_stop_enabled?: boolean;
  overtrading_guard_enabled?: boolean;
  notes?: string | null;
}

export interface KillSwitchStatus {
  organization_id: string;
  active: boolean;
  reason: string | null;
  activated_by: string | null;
  activated_at: string | null;
  deactivated_by: string | null;
  deactivated_at: string | null;
  version: number;
  scope: string;
  global_active: boolean;
  execution_blocked: boolean;
}

export interface KillSwitchMutationRequest {
  confirm: boolean;
  reason: string;
  expected_version?: number | null;
}

export interface AlertSummaryItem {
  alert_type: string;
  severity: string;
  message: string;
}

export interface AlertsLessonsSummary {
  unread_alerts: number;
  latest_high_priority: AlertSummaryItem[];
  pending_lessons: number;
  accepted_lessons: number;
  top_pending_lessons: string[];
  limitations: string[];
}

export interface NextRecommendedAction {
  action: string;
  reason: string;
  link: string;
  priority: number;
}

export interface DashboardSummary {
  safety: DashboardSafetyStatus;
  daily_discipline: DailyDisciplineSnapshot | null;
  discipline_score: DisciplineScoreSummary | null;
  strategy_readiness: StrategyReadinessSummary | null;
  active_paper_validations: ActivePaperValidationItem[];
  open_paper_trades: OpenPaperTradeItem[];
  open_paper_trades_summary: OpenPaperTradesSummary | null;
  alerts_lessons: AlertsLessonsSummary | null;
  market_watcher: {
    effective_enabled: boolean;
    last_scan_at: string | null;
    fresh_observations: number;
    limitations: string[];
  } | null;
  bridge: {
    effective_enabled: boolean;
    last_tick_at: string | null;
    scans_triggered_last_tick: number;
    limitations: string[];
  } | null;
  next_recommended_action: NextRecommendedAction;
  limitations: string[];
}

export interface PaginatedJournalEntries {
  items: JournalEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface Citation {
  document_id: string;
  chunk_id: string;
  title?: string | null;
  source_type: DocumentSourceType | string;
  section_title?: string | null;
  page_number?: number | null;
  chunk_ordinal?: number | null;
  score?: number | null;
  snippet?: string | null;
  source_filename?: string | null;
}

export interface RetrievedChunk {
  chunk_id: string;
  document_id: string;
  title?: string | null;
  section_title?: string | null;
  page_number?: number | null;
  chunk_ordinal: number;
  source_type: DocumentSourceType | string;
  content: string;
  score: number;
  source_filename?: string | null;
}

export interface RagSearchResponse {
  query: string;
  chunks: RetrievedChunk[];
  citations: Citation[];
  degraded?: boolean;
  fallback_used?: boolean;
  vector_backend?: string | null;
  detail?: string | null;
}

export type RagDocument = import("./generated/types").components["schemas"]["RagDocument"];
export type PaginatedRagDocuments = import("./generated/types").components["schemas"]["PaginatedRagDocuments"];

export interface ChunkMetadata {
  title?: string | null;
  section_title?: string | null;
  page_number?: number | null;
  source_type: DocumentSourceType | string;
  strategy_tag?: string | null;
  symbol_tag?: string | null;
  timeframe_tag?: string | null;
  risk_tag?: string | null;
  source_filename?: string | null;
}

export interface RagChunk {
  id: string;
  document_id: string;
  organization_id?: string | null;
  user_id?: string | null;
  title?: string | null;
  section_title?: string | null;
  page_number?: number | null;
  chunk_ordinal: number;
  content: string;
  token_count?: number | null;
  text_hash?: string | null;
  embedding_ref?: string | null;
  metadata: ChunkMetadata;
  created_at: string;
}

export interface PaginatedRagChunks {
  items: RagChunk[];
  total: number;
  limit: number;
  offset: number;
}

export type IngestDocumentResponse = import("./generated/types").components["schemas"]["IngestDocumentResponse"];

export interface FileImportPreview {
  filename: string;
  title: string;
  source_type: string;
  media_type: string;
  byte_size: number;
  raw_content_hash: string;
  extracted_text_hash: string;
  extracted_text: string;
  extracted_characters: number;
  warnings: string[];
  preview_receipt: string;
  expires_at: string;
  saved: false;
  vector_index_status: "not_started";
}

export interface UsageSummary {
  organization_id?: string | null;
  user_id?: string | null;
  period_start?: string | null;
  period_end?: string | null;
  event_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_provider_reported_cost: string;
  total_estimated_cost: string;
  total_cost: string;
  billing_grade_cost: string;
  cost_is_placeholder: boolean;
  total_tool_calls: number;
  fallback_count: number;
  cache_hit_count: number;
}

export interface UsageEvent {
  usage_event_id?: string | null;
  request_id?: string | null;
  organization_id?: string | null;
  user_id?: string | null;
  feature: string;
  model?: string | null;
  provider?: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  provider_reported_cost?: string | null;
  estimated_cost: string;
  cost_source: string;
  cost_is_placeholder: boolean;
  is_billing_grade?: boolean;
  tool_calls: number;
  cache_hit: boolean;
  fallback_used: boolean;
  latency_ms?: number | null;
  status: string;
  timestamp: string;
}

export interface UsageFeatureBreakdown {
  feature: string;
  event_count: number;
  total_tokens: number;
  total_cost: string;
  fallback_count: number;
}

export interface UsageProviderBreakdown {
  provider: string;
  event_count: number;
  total_tokens: number;
  total_cost: string;
  fallback_count: number;
}

export interface OrganizationQuotaConfig {
  organization_id: string;
  monthly_token_limit: number;
  monthly_cost_limit: string;
  daily_request_limit: number;
  limit_agent_chat: number;
  limit_rag_ingest: number;
  limit_market_analyze: number;
  limit_agent_narrative: number;
  limit_paper_execution: number;
  soft_warning_threshold: string;
  hard_block_threshold: string;
}

export interface QuotaUsageSnapshot {
  monthly_tokens_used: number;
  monthly_tokens_limit: number;
  monthly_tokens_pct: number;
  monthly_cost_used: string;
  monthly_cost_limit: string;
  monthly_cost_pct: number;
  daily_requests_used: number;
  daily_requests_limit: number;
  daily_requests_pct: number;
  feature_usage: Record<string, number>;
}

export interface QuotaStatus {
  quota: OrganizationQuotaConfig;
  usage: QuotaUsageSnapshot;
  soft_limit_reached: boolean;
  hard_limit_reached: boolean;
  warnings: string[];
  blocked_features: string[];
}

export interface PaginatedUsageEvents {
  items: UsageEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface SubscriptionPlan {
  plan_id: string;
  name: string;
  description: string;
  monthly_token_limit: number;
  monthly_cost_limit: string;
  daily_request_limit: number;
  limit_agent_chat: number;
  limit_rag_ingest: number;
  limit_market_analyze: number;
  limit_agent_narrative: number;
  limit_paper_execution: number;
  seat_limit: number;
  price_display: string;
  price_currency: string;
  stripe_price_id?: string | null;
}

export interface BillingCustomer {
  id: string;
  organization_id: string;
  provider: string;
  provider_customer_id: string;
  billing_email: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface BillingSubscription {
  id: string;
  organization_id: string;
  provider_subscription_id: string | null;
  plan_id: string;
  status: string;
  current_period_start: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  created_at: string;
  updated_at: string;
}

export interface BillingStatus {
  billing_enabled: boolean;
  provider: string;
  is_mock: boolean;
  live_checkout_available: boolean;
  current_plan_id: string;
  customer: BillingCustomer | null;
  subscription: BillingSubscription | null;
}

export interface CheckoutResponse {
  checkout_url: string;
  session_id: string;
  is_mock: boolean;
}

export interface PortalResponse {
  portal_url: string;
  is_mock: boolean;
}

export interface UsageExportResponse {
  batch_id: string;
  organization_id: string;
  period_start: string;
  period_end: string;
  total_events: number;
  total_tokens: number;
  provider_reported_cost: string;
  estimated_cost: string;
  billing_grade_cost: string;
  cost_is_billing_grade: boolean;
  fallback_event_count: number;
  line_items: Array<{
    feature: string;
    event_count: number;
    total_tokens: number;
    provider_reported_cost: string;
    estimated_cost: string;
    cost_is_billing_grade: boolean;
  }>;
  provider: string;
  exported_at: string;
}

export interface AuditRecord {
  event_id: string;
  request_id: string;
  trace_id: string;
  user_id?: string | null;
  organization_id?: string | null;
  event_type: AuditEventType;
  resource_type: string;
  resource_id?: string | null;
  actor_type: string;
  action: string;
  result: string;
  severity: AuditSeverity;
  payload_hash: string;
  redacted_metadata: Record<string, unknown>;
  timestamp: string;
}

export interface PaginatedAuditRecords {
  items: AuditRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface ToolOutput {
  tool_name: string;
  success: boolean;
  result?: Record<string, unknown> | null;
  output?: Record<string, unknown> | null;
  used_fallback: boolean;
  error?: string | null;
  latency_ms?: number | null;
}

export type TradingAnalysisDetail = import("./generated/types").components["schemas"]["TradingAnalysisDetail"];

export type TradingNarrativeDetail = import("./generated/types").components["schemas"]["TradingNarrativeDetail"];

export type NarrativeMetadata = import("./generated/types").components["schemas"]["NarrativeMetadata"];

export type AgentMessageResponse = import("./generated/types").components["schemas"]["AgentMessageResponse"];

export type AgentStructuredProposal = import("./generated/types").components["schemas"]["StructuredActionProposal"];
export type AgentTurnRequest = import("./generated/types").components["schemas"]["AgentTurnRequest"];
export type AgentTurnResult = import("./generated/types").components["schemas"]["AgentTurnResult"];
export type UserStrategyUpdate = import("./generated/types").components["schemas"]["UserStrategyUpdate"];

export type ConversationStatus = "active" | "archived";
export type ConversationMessageRole = "user" | "assistant" | "system";
export type StrategyProposalStatus = "draft" | "confirmed" | "rejected" | "superseded";

export interface ConversationSummary {
  id: string;
  organization_id: string;
  user_id: string;
  title?: string | null;
  status: ConversationStatus;
  strategy_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessageRecord {
  id: string;
  conversation_id: string;
  organization_id: string;
  user_id: string;
  role: ConversationMessageRole;
  content: string;
  request_id?: string | null;
  intent?: string | null;
  payload?: Record<string, unknown>;
  created_at: string;
}

export interface PaginatedConversations {
  items: ConversationSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaginatedConversationMessages {
  items: ConversationMessageRecord[];
  total: number;
  limit: number;
  offset: number;
}

export type StrategyProposalRecord = import("./generated/types").components["schemas"]["StrategyProposalRecord"];

export interface PaginatedStrategyProposals {
  items: StrategyProposalRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaperOrder {
  id: string;
  proposal_id?: string | null;
  approval_id?: string | null;
  organization_id?: string;
  user_id?: string;
  strategy_id?: string | null;
  mode?: string;
  symbol: string;
  side: string;
  type?: string;
  status: string;
  /** Backend PaperOrder uses `size`; `quantity` is a compatibility alias. */
  quantity?: string;
  size?: string;
  price?: string | null;
  filled_price?: string | null;
  reduce_only?: boolean;
  idempotency_key?: string;
  exchange_order_id?: string | null;
  created_at: string;
}

export interface PaginatedPaperOrders {
  items: PaperOrder[];
  total: number;
  limit: number;
  offset: number;
}

export interface ExecutePaperPlanRequest {
  account_id: string;
  authorization_id: string;
  revision_id: string;
  idempotency_key: string;
  correlation_id?: string | null;
}

export interface ExecutePaperPlanResult {
  replayed: boolean;
  outcome: "ALLOW" | "BLOCKED";
  command_id: string;
  canonical_payload_hash: string;
  receipt: {
    receipt_id: string;
    command_id: string;
    authorization_id?: string | null;
    organization_id: string;
    account_id: string;
    created_at: string;
    outcome: "ALLOW" | "BLOCKED";
    blocked_reason_code?: string | null;
  };
  projection?: {
    receipt_id: string;
    state: string;
    version: number;
  } | null;
  blocked_reason_code?: string | null;
}

export interface CanonicalCandidate {
  candidate_id: string;
  organization_id: string;
  state: string;
  assessment_id: string;
  evidence_window_hash: string;
  strategy_version_id: string;
  setup_definition_id?: string;
  direction: string;
  timeframe: string;
  evidence_instrument?: string;
  valid_until: string;
  created_at?: string;
  content_hash: string;
  confidence?: number | null;
}

export interface CanonicalEvidenceSource {
  venue: string;
  market_type: string;
  instrument_id: string;
  provider_symbol: string;
  provider_name: string;
  source_family: string;
  adapter_version: string;
  is_live: boolean;
  is_mock: boolean;
  fallback_used: false;
}

export interface CanonicalEvidenceFreshness {
  policy_version: string;
  state: string;
  evaluated_at: string;
  source_time?: string | null;
  age_seconds?: string | null;
  valid_until?: string | null;
}

export interface CanonicalEvidenceCompleteness {
  ohlcv_15m: string;
  ohlcv_4h: string;
  cvd: string;
  signed_flow: string;
  coverage_content_hash?: string | null;
  cvd_content_hash?: string | null;
  signed_flow_content_hash?: string | null;
}

export interface CanonicalCurrentPriceRead {
  usable_as_current_market_price: boolean;
  presentation: string;
  price?: string | null;
  source_time?: string | null;
  venue_trade_id?: string | null;
  is_live: boolean;
  is_mock: boolean;
  fallback_used: false;
  freshness: CanonicalEvidenceFreshness;
}

export interface CanonicalSetupEvidenceRead {
  available: boolean;
  evidence_window_hash?: string | null;
  trigger_interval_start?: string | null;
  trigger_interval_end?: string | null;
  evaluated_at?: string | null;
  cvd_signed_quote_delta?: string | null;
  signed_flow_ratio?: string | null;
  completeness: CanonicalEvidenceCompleteness;
  reason?: string | null;
}

export interface CanonicalEvidenceRead {
  authority: "canonical";
  live_executable: false;
  watcher_activated: false;
  organization_id: string;
  symbol: string;
  source: CanonicalEvidenceSource;
  current_price: CanonicalCurrentPriceRead;
  setup_evidence: CanonicalSetupEvidenceRead;
  timestamps: Record<string, string | null>;
  unavailable_reason?: string | null;
}

export interface CanonicalMarketMonitorStatusRead {
  authority: "canonical_market_monitor";
  live_executable: false;
  watcher_activated: false;
  compatibility_price_used: false;
  symbol: string;
  mode: "live_perpetual" | "replay" | string;
  availability: "fresh" | "stale" | "degraded" | "unavailable" | "replay" | string;
  reason: string;
  perpetual: true;
  source: CanonicalEvidenceSource;
  current_price: CanonicalCurrentPriceRead;
  last_update: string | null;
  evaluated_at: string;
  stream: {
    reconnect_state: string;
    gap_state: string;
    warm_up_status: string;
    last_sequence?: number | null;
    last_event_id?: string | null;
    last_event_at?: string | null;
    reconnect_count: number;
  };
  coverage: {
    completeness: string;
    gap_state: string;
    content_hash?: string | null;
    first_trade_id?: string | null;
    last_trade_id?: string | null;
  };
  cvd: {
    available: boolean;
    signed_quote_delta?: string | null;
    total_quote_volume?: string | null;
    signed_flow_ratio?: string | null;
    event_count: number;
    event_set_hash?: string | null;
    reason?: string | null;
  };
  ohlcv: {
    available: boolean;
    completeness_15m: string;
    completeness_4h: string;
    latest_15m_close?: string | null;
    latest_15m_end?: string | null;
    reason?: string | null;
  };
  provider: {
    name: string;
    health: string;
    is_mock: boolean;
    using_fallback: boolean;
    detail?: string | null;
  };
  backoff: {
    active: boolean;
    attempt: number;
    next_retry_at?: string | null;
    last_error_class?: string | null;
  };
  content_hash: string;
  unavailable_reason?: string | null;
  activation?: {
    state: "inactive" | "active" | "refused";
    configured_source: "replay" | "binance_usdm";
    intended_staging_source: "binance_usdm";
    rollback_source: "replay";
    read_only: true;
    exchange_credentials_used: false;
    spot_fallback_permitted: false;
    fabricated_fallback_permitted: false;
    first_symbol: "BTCUSDT";
    trade_freshness_seconds: 10;
  };
}

export interface CanonicalCandidateRead {
  authority: "canonical";
  candidate: CanonicalCandidate;
}

export interface PaginatedCanonicalCandidates {
  items: CanonicalCandidateRead[];
  total: number;
  limit: number;
  offset: number;
}

export interface CanonicalSetupAssessmentRead {
  authority: "canonical_lineage_projection";
  assessment_id: string;
  organization_id: string;
  candidate_id: string;
  assessment_state: string;
  assessment_content_hash?: string | null;
  evidence_window_hash: string;
  live_executable: false;
}

export interface CanonicalEligibilityRead {
  authority: "canonical";
  evaluation: {
    eligibility: {
      eligibility_id: string;
      candidate_id: string;
      assessment_id: string;
      state: string;
      reason_codes?: string[];
      paper_actionable?: boolean;
      checked_at?: string | null;
      valid_until?: string | null;
    };
    paper_actionable: boolean;
    live_executable: false;
    content_hash: string;
  };
}

export interface CanonicalExecutionReceiptRead {
  authority: "canonical";
  live_executable: false;
  receipt: ExecutePaperPlanResult["receipt"];
  projection?: ExecutePaperPlanResult["projection"];
}

export interface CanonicalLearningRecordRead {
  authority: "canonical";
  record: {
    attribution_id: string;
    candidate_id: string;
    journal_trade_id?: string | null;
    narrative_explanation?: string | null;
    facts?: {
      outcome?: {
        result?: string | null;
        net_pnl?: string | null;
        status?: string | null;
      };
      human_vs_system?: {
        executed_trade_outcome?: boolean;
      };
    };
  };
}

export interface CanonicalLearningStatsRead {
  authority: "canonical";
  venue_mode?: string | null;
  snapshot: {
    organization_id: string;
    patterns: Array<{
      strategy_version_id: string;
      setup_definition_id: string;
      learning_venue_mode: string;
      sample_candidates: number;
      plan_approved_count: number;
      filled_count: number;
      executed_outcome_count: number;
      win_count: number;
      loss_count: number;
      executed_win_rate?: string | null;
    }>;
    human_vs_system: {
      human_reject_or_skip: number;
      human_approvals: number;
      paper_system_executions: number;
      executed_outcomes: number;
      setup_confirmed_count: number;
    };
  };
}

export interface PaperEvaluationRefinement {
  suggestion_id: string;
  category: string;
  summary: string;
  severity: string;
  activate: false;
  auto_activate: false;
  banner: string;
  narrative_explanation?: string | null;
}

export interface CanonicalPaperEvaluationRead {
  authority: "canonical";
  live_executable: false;
  watcher_activated: false;
  summary: {
    organization_id: string;
    authority: "paper_evaluation_measurement";
    live_executable: false;
    facts: {
      watcher: {
        scan_count: number;
        confirmed_setup_count: number;
        candidates_published: number;
        stale_evidence_count: number;
        provider_outage_count: number;
      };
      conversion: {
        scans: number;
        assessments: number;
        confirmed_setups: number;
        candidates: number;
        eligible: number;
        blocked: number;
        approved: number;
        rejected: number;
        skipped: number;
        filled: number;
        closed: number;
      };
      false_signals: {
        confirmed_losses: number;
        executed_outcomes: number;
        false_signal_rate?: string | null;
      };
      strategy_overall: {
        win_rate?: string | null;
        expectancy?: string | null;
        max_drawdown?: string | null;
        average_mfe?: string | null;
        average_mae?: string | null;
        executed_outcome_count: number;
        win_count?: number;
        loss_count?: number;
        breakeven_count?: number;
        net_pnl_total?: string | null;
        confidence: string;
      };
      strategy_versions?: Array<{
        strategy_version_id?: string | null;
        setup_definition_id?: string | null;
        executed_outcome_count: number;
        win_count?: number;
        loss_count?: number;
        win_rate?: string | null;
        net_pnl_total?: string | null;
      }>;
      rule_adherence: {
        risk_adhered_count: number;
        stop_violation_count: number;
        adherence_rate?: string | null;
      };
      blocked: {
        blocked_count: number;
        by_reason: Array<[string, number]>;
      };
      human_vs_system: {
        human_reject_or_skip: number;
        human_approvals: number;
        paper_system_executions: number;
        executed_outcomes: number;
      };
      missed_opportunities: {
        rejected_confirmed: number;
        skipped_confirmed: number;
        eligible_not_approved: number;
        blocked_after_confirmation: number;
        counterfactual_pnl: null;
        warning: string;
      };
      data_quality: {
        fresh_count: number;
        stale_count: number;
        unavailable_count: number;
        replay_count: number;
        stale_or_unavailable_rate?: string | null;
      };
      warnings: Array<{ code: string; message: string }>;
    };
    refinements: PaperEvaluationRefinement[];
    narrative?: { banner: string; text: string } | null;
  };
}

export interface CanonicalTradePlanRevision {
  schema_version?: string;
  plan_id: string;
  revision_id: string;
  organization_id: string;
  user_id: string;
  account_id: string;
  candidate_id: string;
  strategy_version_id: string;
  setup_definition_id: string;
  side: "BUY" | "SELL" | string;
  timeframe: string;
  execution_instrument?: string;
  evidence_instrument?: string;
  quantity: { value: string; unit: string };
  quantity_unit: string;
  order_type?: string;
  limit_price?: { value: string; unit: string } | null;
  risk_and_exits: {
    stop: { value: string; unit: string };
    targets: Array<{
      order: number;
      price: { value: string; unit: string };
      quantity_fraction: string;
    }>;
    risk_budget: { value: string; unit: string };
    maximum_loss: { value: string; unit: string };
    leverage: string;
  };
  content_hash: string;
  correlation_id: string;
  created_at: string;
  valid_from: string;
  valid_until: string;
  evidence_ids: string[];
}

export interface User {
  id: string;
  email: string;
  role: string;
  risk_profile: string;
  timezone: string;
  is_active: boolean;
  email_verified: boolean;
  created_at: string;
}

export interface MessageResponse {
  message: string;
}

export type MembershipRole = "owner" | "trader" | "viewer";

export interface OrganizationInvitation {
  id: string;
  organization_id: string;
  email: string;
  role: MembershipRole;
  invited_by_user_id: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  created_at: string;
  is_pending: boolean;
}

export interface InvitationListResponse {
  invitations: OrganizationInvitation[];
}

export interface Organization {
  id: string;
  name: string;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface AuthResponse {
  user: User;
  organization: Organization;
  tokens: TokenPair;
}

export interface MeResponse {
  user: User;
  organization: Organization;
}

export interface PaperExecutionAccount {
  id: string;
  name: string;
  execution_mode: "PAPER";
  account_mode: "NET";
  enabled: boolean;
}

export interface PaperAccountStatus {
  account: PaperExecutionAccount | null;
  can_register: boolean;
}

export interface PaperAccountRegistration {
  account: PaperExecutionAccount;
  created: boolean;
}

export interface MarketDataMeta {
  symbol: string;
  exchange: string;
  timeframe?: Timeframe | null;
  timestamp: string;
  source: string;
  is_live: boolean;
  is_stale: boolean;
  stale_reason?: string | null;
  provider_name: string;
  fallback_used: boolean;
  retrieved_at: string;
  cache_hit?: boolean;
}

export interface TickerResponse {
  meta: MarketDataMeta;
  last_price: string;
  bid?: string | null;
  ask?: string | null;
  volume_24h?: string | null;
  change_24h_pct?: number | null;
}

export interface OHLCVBar {
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  timestamp: string;
}

export interface OHLCVResponse {
  meta: MarketDataMeta;
  bars: OHLCVBar[];
}

export interface IndicatorContext {
  symbol: string;
  timeframe: Timeframe;
  rsi?: number | null;
  vwap?: string | null;
  ema_fast?: string | null;
  ema_slow?: string | null;
  macd?: number | null;
  macd_signal?: number | null;
  atr?: string | null;
  volatility?: number | null;
  volume_trend?: number | null;
  funding_rate?: string | null;
  timestamp: string;
}

export interface MarketSnapshotResponse {
  meta: MarketDataMeta;
  ticker?: TickerResponse | null;
  latest_bar?: OHLCVBar | null;
  indicators?: IndicatorContext | null;
  funding_rate?: string | null;
}

export interface StrategySignalSummary {
  strategy_id: StrategyId;
  direction?: string | null;
  confidence?: number | null;
  evidence: string[];
  data_quality_note?: string | null;
}

export interface MarketAnalyzeResponse {
  snapshot: MarketSnapshotResponse;
  indicators: IndicatorContext;
  strategy_signals: StrategySignalSummary[];
  data_quality: string;
  confidence_penalty_applied: boolean;
}

export type ManualLevelType =
  | "support"
  | "resistance"
  | "fibonacci"
  | "trend_line"
  | "vwap"
  | "liquidity_zone"
  | "previous_high"
  | "previous_low"
  | "user_note";

export type PreTradeRecommendation =
  | "no_trade"
  | "watch"
  | "small_probe"
  | "normal_size"
  | "high_conviction";

export interface StrategyCard {
  strategy_name: string;
  market_type: string;
  asset_universe: string[];
  timeframes: Timeframe[];
  entry_conditions: string[];
  confirmation_conditions: string[];
  invalidation: string[];
  stop_loss: string[];
  take_profit_plan: string[];
  runner_plan: string[];
  position_sizing: string[];
  add_rules: string[];
  no_trade_rules: string[];
  backtest_rules: string[];
  success_criteria: string[];
  validation_status: string;
}

export interface UserStrategy {
  id: string;
  name: string;
  setup_type: StrategyId;
  current_version: number;
  enabled: boolean;
  notes?: string | null;
  latest_card?: StrategyCard | null;
  validation_status?: string | null;
  backtest_status?: string | null;
  paper_validation_status?: string | null;
  paper_eligible?: boolean;
  created_at: string;
  updated_at: string;
}

// --------------------------------------------------------------------------
// Backtest engine v2 (AT-034) — deterministic historical simulation
// --------------------------------------------------------------------------

export type BacktestRunStatus =
  | "not_started"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancel_requested"
  | "cancelled";

export type BacktestSplitMode = "none" | "holdout" | "rolling";

export type BacktestSplitLabel = "in_sample" | "out_of_sample";

export type BacktestRecommendation =
  | "needs_structured_rules"
  | "needs_more_sample_size"
  | "needs_review"
  | "backtested"
  | "paper_eligible"
  | "restricted"
  | "unreliable_data";

export interface BacktestSplitConfig {
  mode: BacktestSplitMode;
  oos_fraction: number;
  window_bars?: number | null;
  step_bars?: number | null;
}

export interface BacktestAssumptions {
  symbol: string;
  exchange: string;
  timeframe: Timeframe;
  start_date?: string | null;
  end_date?: string | null;
  initial_capital: string;
  fees_bps: string;
  slippage_bps: string;
  funding_assumption?: string;
  funding_rate_bps_per_8h?: string;
  runner_trail_pct?: string;
  split_config?: BacktestSplitConfig | null;
  risk_per_trade_pct: string;
  max_trades?: number | null;
  sample_size?: number;
}

export interface EquityCurvePoint {
  timestamp: string;
  equity: string;
}

export interface BacktestMetrics {
  trade_count: number;
  win_rate: number;
  profit_factor: number;
  expectancy: string;
  max_drawdown_pct: number;
  average_win: string;
  average_loss: string;
  largest_win: string;
  largest_loss: string;
  consecutive_losses: number;
  average_time_in_trade_bars: number;
  total_fees: string;
  total_slippage: string;
  total_funding?: string;
  net_pnl: string;
  return_pct: number;
  ending_equity: string;
  equity_curve?: EquityCurvePoint[];
  symbol: string;
  timeframe: string;
}

export interface BacktestSplitMetrics {
  split_label: BacktestSplitLabel;
  split_index: number;
  start_time: string;
  end_time: string;
  trade_count: number;
  win_rate: number;
  profit_factor: number;
  expectancy: string;
  net_pnl: string;
  max_drawdown_pct: number;
}

export interface BacktestDatasetSummary {
  dataset_hash: string;
  candle_count: number;
  gap_count: number;
  stale_count: number;
  first_open_time?: string | null;
  last_open_time?: string | null;
  source_counts: Record<string, number>;
}

export interface BacktestResult {
  metrics: BacktestMetrics | null;
  trades?: BacktestTradeRecord[];
  recommendation: BacktestRecommendation | string;
  meets_success_criteria?: boolean;
  limitations?: string[];
  data_quality?: string;
  rule_engine_source?: string;
  note?: string;
  result_hash?: string | null;
  engine_version?: string | null;
  split_metrics?: BacktestSplitMetrics[] | null;
  oos_metrics?: BacktestSplitMetrics | null;
  dataset_summary?: BacktestDatasetSummary | null;
  cancelled?: boolean;
  processed_bars?: number | null;
  total_bars?: number | null;
  replay?: StrategyReplayReport | null;
}

export interface StrategyReplayReport {
  mode?: "trade_simulation" | "sfp_research";
  strategy_version_id: string;
  parameter_hash: string;
  improvement_claim: false;
  missing_evidence: string[];
  stale_evidence?: string[];
  samples: {
    split_label: string;
    status: string;
    candle_count: number;
    setup_count?: number;
    candidate_count: number;
    lifecycle_counts?: Record<string, number>;
  }[];
  research_buckets?: {
    split_label: string;
    symbol: string;
    timeframe: string;
    direction: string;
    level_type: string;
    strategy_version_id: string;
    quality_bucket: string;
    regime: string;
    setup_count: number;
    confirmed_count: number;
  }[];
}

export interface BacktestTradeRecord {
  id?: string | null;
  entry_time: string;
  exit_time: string;
  direction: TradeDirection;
  entry_price: string;
  exit_price: string;
  stop_loss: string;
  size: string;
  fees: string;
  slippage_cost: string;
  gross_pnl: string;
  net_pnl: string;
  tp_hit_status: string;
  exit_reason: string;
  rule_notes?: string | null;
  mfe_price?: string | null;
  mae_price?: string | null;
  mfe_amount?: string | null;
  mae_amount?: string | null;
  available_profit?: string | null;
  capture_pct?: string | null;
  funding_cost?: string;
  split_label?: BacktestSplitLabel;
  split_index?: number;
  sequence?: number | null;
}

/** @deprecated Use BacktestTradeRecord */
export type BacktestTrade = BacktestTradeRecord;

export interface BacktestRun {
  id: string;
  strategy_id: string;
  strategy_version_id?: string | null;
  organization_id: string;
  user_id: string;
  status: BacktestRunStatus;
  assumptions: BacktestAssumptions;
  result?: BacktestResult | null;
  error_message?: string | null;
  config_hash?: string | null;
  dataset_id?: string | null;
  engine_version?: string | null;
  result_hash?: string | null;
  idempotency_key?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  cancel_requested_at?: string | null;
  processed_bars?: number | null;
  total_bars?: number | null;
  created_at: string;
  updated_at: string;
}

export interface BacktestRunCreate {
  assumptions?: Partial<BacktestAssumptions>;
  strategy_version_id?: string | null;
  idempotency_key?: string | null;
}

export interface PaginatedBacktestTrades {
  items: BacktestTradeRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface BacktestVerifyResult {
  run_id: string;
  result_hash_stored?: string | null;
  result_hash_recomputed?: string | null;
  match: boolean;
  dataset_ok: boolean;
  detail?: string | null;
}

export type BacktestJournalRowOutcome = "created" | "would_create" | "duplicate" | "invalid";

export interface BacktestJournalRequest {
  dry_run: boolean;
}

export interface BacktestJournalRowResult {
  index: number;
  backtest_trade_id?: string | null;
  outcome: BacktestJournalRowOutcome;
  external_ref?: string | null;
  journal_trade_id?: string | null;
  errors: string[];
}

export interface BacktestJournalResult {
  run_id: string;
  dry_run: boolean;
  committed: boolean;
  total_rows: number;
  created_count: number;
  duplicate_count: number;
  invalid_count: number;
  results: BacktestJournalRowResult[];
}

export type JournalComparisonCohort = "human" | "paper_system" | "backtest";

export interface JournalComparisonFilters {
  strategy_id?: string | null;
  strategy_version_id?: string | null;
  setup_id?: string | null;
  symbol?: string | null;
  timeframe?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  market_regime?: MarketRegime | null;
  entry_method?: JournalEntryMethod | null;
  source?: JournalTradeSource | null;
}

export interface JournalComparisonCohortResult {
  cohort: JournalComparisonCohort;
  metrics: JournalTradeStatsMetrics;
  sample_count: number;
  truncated: boolean;
}

export type ComparisonBreakdownDimension = "setup" | "market_regime";

export interface DecisionQualityMetrics {
  timing_sample_count: number;
  average_entry_timing_pct: number | null;
  early_exit_sample_count: number;
  early_exit_count: number | null;
  early_exit_rate: number | null;
  missed_profit_sample_count: number;
  average_missed_profit: string | null;
  average_capture_pct: number | null;
  warnings: JournalStatsWarning[];
}

export interface ComparisonScorecard {
  actor: ExecutionActor;
  metrics: JournalTradeStatsMetrics;
  decision_quality: DecisionQualityMetrics;
  sample_count: number;
  truncated: boolean;
}

export interface ComparisonDimensionBucket {
  key: string;
  group_id?: string | null;
  label: string;
  metrics: JournalTradeStatsMetrics;
  sample_count: number;
}

export interface ComparisonBreakdown {
  dimension: ComparisonBreakdownDimension;
  buckets: ComparisonDimensionBucket[];
}

export interface ComparisonLinks {
  journal_trades_path: string;
  journal_statistics_path: string;
  journal_comparison_path: string;
  backtests_path: string;
  research_validation_path: string;
  paper_validation_candidates_path: string;
}

export interface JournalComparisonResponse {
  filters: JournalComparisonFilters;
  cohorts: JournalComparisonCohortResult[];
  scorecards: ComparisonScorecard[];
  by_entry_method: ComparisonDimensionBucket[];
  by_source: ComparisonDimensionBucket[];
  rule_compliance: ComparisonDimensionBucket[];
  decision_quality: DecisionQualityMetrics;
  breakdowns: ComparisonBreakdown[];
  links: ComparisonLinks;
  confidence: SampleConfidence;
  warnings: JournalStatsWarning[];
  max_rows: number;
  generated_at: string;
  note: string;
}

export type SetupEvidenceTier = "tier1" | "tier2" | "tier3";

export interface SetupEvidenceThresholds {
  tier1_oos_min_trades: number;
  tier1_oos_min_profit_factor: number;
  tier1_min_confirm_trades: number;
  tier2_min_trades: number;
  tier2_oos_min_trades: number;
  tier2_oos_min_profit_factor: number;
}

export interface SetupEvidenceMeasured {
  oos_trade_count: number;
  oos_profit_factor?: number | null;
  oos_expectancy?: string | null;
  confirm_trade_count: number;
  confirm_expectancy?: string | null;
  total_backtest_trades: number;
  backtest_run_id?: string | null;
}

export interface SetupEvidenceItem {
  strategy_id: string;
  strategy_version_id: string;
  strategy_name: string;
  version: number;
  tier: SetupEvidenceTier;
  measured: SetupEvidenceMeasured;
  thresholds: SetupEvidenceThresholds;
  note: string;
}

export interface SetupEvidenceResponse {
  items: SetupEvidenceItem[];
  generated_at: string;
  note: string;
}

export type JournalComparisonParams = {
  strategy_id?: string;
  strategy_version_id?: string;
  setup_id?: string;
  symbol?: string;
  timeframe?: string;
  date_from?: string;
  date_to?: string;
  market_regime?: MarketRegime;
  entry_method?: JournalEntryMethod;
  source?: JournalTradeSource;
  breakdown_limit?: number;
};

export type SetupEvidenceParams = {
  strategy_id?: string;
  strategy_version_id?: string;
  setup_id?: string;
};

export interface ResearchValidationEvidenceItem {
  backtest_run_id: string;
  strategy_id: string;
  strategy_version_id?: string | null;
  strategy_name: string;
  version: number;
  symbol?: string | null;
  timeframe?: string | null;
  regime?: string | null;
  status: BacktestRunStatus;
  dataset_hash?: string | null;
  config_hash?: string | null;
  result_hash?: string | null;
  evidence_tier: SetupEvidenceTier;
  sample_size: number;
  oos_trade_count: number;
  oos_expectancy?: string | null;
  oos_profit_factor?: number | null;
  confirm_trade_count: number;
  eligible_for_promotion: boolean;
  warnings: string[];
  existing_candidate_id?: string | null;
  existing_run_plan_id?: string | null;
  promotion_blocked_reason?: string | null;
}

export interface ResearchValidationEvidenceResponse {
  items: ResearchValidationEvidenceItem[];
  generated_at: string;
  note: string;
}

export interface ResearchValidationPromoteRequest {
  confirm: string;
  backtest_run_id: string;
}

export interface ResearchValidationEligibility {
  eligible: boolean;
  tier?: SetupEvidenceTier | null;
  warnings: string[];
  blocked_reason?: string | null;
}

export interface ResearchValidationLinks {
  candidate_id?: string | null;
  draft_id?: string | null;
  source_alert_id?: string | null;
  run_plan_id?: string | null;
  backtest_run_id: string;
  strategy_id?: string | null;
  strategy_version_id?: string | null;
  journal_comparison_path?: string | null;
  setup_evidence_path?: string | null;
  journal_statistics_path?: string | null;
}

export interface ResearchValidationPromoteResult {
  candidate: PaperValidationCandidateItem;
  already_exists: boolean;
  eligibility: ResearchValidationEligibility;
  links: ResearchValidationLinks;
}

export interface ResearchValidationStatusResponse {
  evidence: ResearchValidationEvidenceItem;
  links: ResearchValidationLinks;
  generated_at: string;
  note: string;
}

export type ResearchValidationEvidenceParams = {
  backtest_run_id?: string;
  strategy_id?: string;
  strategy_version_id?: string;
};

export type TradingViewSignalStatus =
  | "received"
  | "validated"
  | "rejected"
  | "duplicate"
  | "candidate_created";

export interface TradingViewSignalLinks {
  setup_definition_id: string | null;
  strategy_id: string | null;
  strategy_version_id: string | null;
  source_alert_id: string | null;
  draft_id: string | null;
  candidate_id: string | null;
  journal_trade_id: string | null;
  backtest_run_id: string | null;
  paper_candidate_path: string | null;
  strategy_path: string | null;
  journal_path: string | null;
}

export interface TradingViewSignalItem {
  id: string;
  organization_id: string;
  external_alert_id: string;
  idempotency_key: string;
  status: TradingViewSignalStatus;
  symbol: string;
  timeframe: string;
  direction: string;
  setup_name: string | null;
  setup_version: number | null;
  setup_definition_id: string | null;
  strategy_id: string | null;
  strategy_version_id: string | null;
  trigger_level: number | null;
  invalidation_level: number | null;
  take_profit_level: number | null;
  stop_loss_level: number | null;
  confidence: number | null;
  source_metadata: Record<string, unknown> | null;
  validation_errors: string[] | null;
  rejection_reason: string | null;
  received_at: string;
  validated_at: string | null;
  occurred_at: string | null;
  duplicate_of_signal_id: string | null;
  links: TradingViewSignalLinks;
  note: string;
}

export interface TradingViewSignalListResponse {
  items: TradingViewSignalItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface TradingViewSignalCreateCandidateResult {
  signal: TradingViewSignalItem;
  candidate_id: string;
  draft_id: string;
  source_alert_id: string;
  already_exists: boolean;
  note: string;
}

export type BloFinSyncHealthStatus = "ok" | "degraded" | "stale" | "unavailable";

export interface BloFinSyncSnapshotItem {
  id: string;
  organization_id: string;
  user_id: string | null;
  synced_at: string;
  health_status: BloFinSyncHealthStatus;
  provider: string;
  exchange_mode: string;
  account_snapshot: Record<string, unknown>;
  positions_snapshot: Record<string, unknown>;
  market_context: Record<string, unknown>;
  provenance: Record<string, unknown>;
  is_stale: boolean;
  stale_reason: string | null;
  error_summary: string | null;
  position_count: number;
  balance_count: number;
  note: string;
}

export interface BloFinSyncResult {
  snapshot: BloFinSyncSnapshotItem;
  note: string;
}

export type PaperSignalOrchestrationMode =
  | "observe_only"
  | "candidate_only"
  | "approval_required";

export type PaperSignalOrchestrationStatus =
  | "eligible"
  | "blocked"
  | "awaiting_review"
  | "paper_candidate_created"
  | "paper_proposal_created"
  | "expired"
  | "rejected";

export interface PaperSignalEligibilityCheck {
  code: string;
  passed: boolean;
  detail: string;
}

export interface PaperSignalOrchestrationTransition {
  at: string;
  from_status: string | null;
  to_status: string;
  reason: string;
  actor_user_id: string | null;
}

export interface PaperSignalOrchestrationLinks {
  tradingview_signal_id: string;
  setup_definition_id: string | null;
  strategy_id: string | null;
  strategy_version_id: string | null;
  journal_trade_id: string | null;
  backtest_run_id: string | null;
  candidate_id: string | null;
  run_plan_id: string | null;
  proposal_id: string | null;
  signal_path: string | null;
  candidate_path: string | null;
  run_plan_path: string | null;
  proposal_path: string | null;
  journal_path: string | null;
}

export interface PaperSignalOrchestrationDecisionItem {
  id: string;
  organization_id: string;
  tradingview_signal_id: string;
  idempotency_key: string;
  status: PaperSignalOrchestrationStatus;
  mode: PaperSignalOrchestrationMode;
  symbol: string;
  timeframe: string;
  direction: string;
  reason_codes: string[] | null;
  reason_summary: string | null;
  eligibility_checks: PaperSignalEligibilityCheck[];
  risk_checks: PaperSignalEligibilityCheck[];
  transitions: PaperSignalOrchestrationTransition[];
  links: PaperSignalOrchestrationLinks;
  decided_by: string | null;
  approved_by: string | null;
  decided_at: string;
  expired_at: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
  note: string;
}

export interface PaperSignalOrchestrationListResponse {
  items: PaperSignalOrchestrationDecisionItem[];
  total: number;
  limit: number;
  offset: number;
  mode: PaperSignalOrchestrationMode;
  enabled: boolean;
}

export interface PaperSignalOrchestrationEvaluateResult {
  decision: PaperSignalOrchestrationDecisionItem;
  already_exists: boolean;
  note: string;
}

export interface PaperSignalOrchestrationApproveResult {
  decision: PaperSignalOrchestrationDecisionItem;
  proposal_id: string;
  already_exists: boolean;
  note: string;
}

export interface PaperValidationMetrics {
  paper_trades_count: number;
  win_rate: number;
  net_pnl: string;
  gross_pnl?: string;
  profit_factor: number;
  expectancy: string;
  max_drawdown_pct: number;
  total_fees?: string;
  total_slippage?: string;
  average_win?: string;
  average_loss?: string;
  consecutive_losses?: number;
  average_holding_time_hours?: number;
  plan_adherence_avg?: number | null;
  early_exit_count?: number;
  stop_respected_count?: number;
  runner_helped_count?: number;
}

export interface PaperValidationRun {
  id: string;
  strategy_id: string;
  strategy_version_id?: string | null;
  status: string;
  runtime_mode?: string;
  paper_eligible: boolean;
  config?: Record<string, unknown> | null;
  blockers?: string[];
  last_scan_at?: string | null;
  last_tick_at?: string | null;
  last_scan_result?: Record<string, unknown> | null;
  metrics?: PaperValidationMetrics | null;
  recommendation?: string | null;
  notes?: string | null;
  created_at: string;
  updated_at: string;
}

export interface PaperSignalResult {
  id: string;
  triggered: boolean;
  status: string;
  symbol: string;
  direction: string;
  confidence: number;
  suggested_entry?: string | null;
  stop_loss?: string | null;
  reason?: string | null;
  created_at: string;
}

export interface CanonicalJournalTradeListItem {
  execution_lifecycle_id?: string | null;
  source?: JournalTradeSource;
  exchange?: string | null;
  entry_time?: string | null;
  created_at?: string;
  id: string;
  symbol: string;
  timeframe: string;
  direction: string;
  status: string;
  thesis?: string | null;
  trigger?: string | null;
  entry_price?: string | null;
  exit_price?: string | null;
  exit_reason?: string | null;
  fees?: string | null;
  net_pnl?: string | null;
  result: string;
  strategy_label?: string | null;
  strategy_version_id?: string | null;
  candidate_id?: string | null;
}

export interface PaginatedCanonicalJournalTrades {
  items: CanonicalJournalTradeListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaperTradeRecord {
  id: string;
  status: string;
  symbol: string;
  direction: string;
  entry_price?: string | null;
  exit_price?: string | null;
  net_pnl?: string | null;
  exit_reason?: string | null;
  created_at: string;
}

export interface PaperScanResult {
  run_id: string;
  signal?: PaperSignalResult | null;
  trade_created: boolean;
  blockers: string[];
  scanned_at: string;
}

export interface PaperTickResult {
  run_id: string;
  trades_closed: number;
  trades_open: number;
  metrics?: PaperValidationMetrics | null;
  recommendation?: string | null;
  ticked_at: string;
}

export interface PaperValidationSummary {
  strategy_id: string;
  paper_eligible: boolean;
  latest_status?: string | null;
  runs: PaperValidationRun[];
  total: number;
  limitation?: string;
}

export interface PaperSchedulerStatus {
  env_enabled: boolean;
  tenant_enabled: boolean;
  effective_enabled: boolean;
  config: {
    enabled: boolean;
    interval_seconds: number;
    max_runs_per_cycle: number;
    max_scans_per_minute: number;
  };
  last_tick_at?: string | null;
  last_tick_status?: string | null;
  real_trading_enabled: boolean;
  limitation: string;
}

export interface PaperSchedulerTickResult {
  ticked_at: string;
  env_enabled: boolean;
  effective_enabled: boolean;
  runs_processed: number;
  runs_skipped: number;
  scans_executed: number;
  ticks_executed: number;
  alerts_created: number;
  decisions: string[];
  limitation: string;
}

export interface PaperRuntimeHistoryRecord {
  id: string;
  run_id?: string | null;
  strategy_id?: string | null;
  symbol?: string | null;
  mode: string;
  started_at: string;
  completed_at?: string | null;
  status: string;
  reason?: string | null;
  signals_created: number;
  trades_opened: number;
  trades_closed: number;
  blockers: string[];
  warnings: string[];
  data_freshness?: string | null;
  latency_ms?: number | null;
}

export interface PaperAlert {
  id: string;
  alert_type: string;
  severity: string;
  strategy_id?: string | null;
  paper_validation_run_id?: string | null;
  paper_trade_id?: string | null;
  message: string;
  read_at?: string | null;
  delivery_status?: string;
  delivery_channel?: string;
  delivery_attempts?: number;
  last_delivery_error?: string | null;
  delivered_at?: string | null;
  next_retry_at?: string | null;
  delivery_skipped_reason?: string | null;
  retry_exhausted?: boolean;
  alert_source?: string;
  created_at: string;
}

export type SetupAlertReviewStatus = "unreviewed" | "watching" | "ignored" | "important";

export interface SetupAlertReviewItem {
  alert_id: string;
  created_at: string;
  symbol?: string | null;
  timeframe?: string | null;
  condition?: string | null;
  direction?: string | null;
  confidence?: number | null;
  reason?: string | null;
  trigger_level?: number | null;
  invalidation_level?: number | null;
  latest_price?: number | null;
  delivery_channel: string;
  delivery_status: string;
  dedupe_key?: string | null;
  review_status: SetupAlertReviewStatus;
  review_notes?: string | null;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface SetupAlertReviewSummary {
  total_unreviewed: number;
  total_watching: number;
  total_important: number;
  total_ignored: number;
  by_condition: Record<string, number>;
  by_symbol: Record<string, number>;
  latest_created_at?: string | null;
  highest_confidence_alerts: Array<{
    alert_id: string;
    symbol?: string | null;
    condition?: string | null;
    confidence?: number | null;
    created_at: string;
  }>;
}

export type PaperValidationDraftRiskMode = "conservative" | "moderate" | "aggressive";
export type PaperValidationDraftStatus = "draft" | "archived" | "cancelled";
export type PaperValidationDraftPrepStatus =
  | "draft"
  | "needs_review"
  | "ready_for_validation"
  | "archived";

export interface PaperValidationDraftChecklist {
  trend_checked: boolean;
  support_resistance_checked: boolean;
  volume_checked: boolean;
  risk_reward_checked: boolean;
  invalidation_checked: boolean;
  higher_timeframe_checked: boolean;
  news_or_funding_checked: boolean;
}

export interface PaperValidationDraftItem {
  draft_id: string;
  source_alert_id: string;
  symbol?: string | null;
  timeframe?: string | null;
  condition?: string | null;
  direction?: string | null;
  confidence?: number | null;
  trigger_level?: number | null;
  invalidation_level?: number | null;
  latest_price?: number | null;
  reason?: string | null;
  risk_mode: PaperValidationDraftRiskMode;
  status: PaperValidationDraftStatus;
  created_at: string;
  created_by?: string | null;
  thesis?: string | null;
  entry_criteria?: string | null;
  invalidation_criteria?: string | null;
  risk_notes?: string | null;
  prep_status: PaperValidationDraftPrepStatus;
  checklist: PaperValidationDraftChecklist;
  prep_completion_score: number;
  missing_checklist_items: string[];
  is_ready_for_validation: boolean;
}

export interface PaperValidationDraftSummary {
  total_drafts: number;
  latest_condition?: string | null;
  latest_created_at?: string | null;
  ready_for_validation_count?: number;
}

export type PaperValidationCandidateStatus = "queued" | "reviewing" | "archived";

export type PromotionSource = "alert_draft" | "research_validation";

export interface PaperValidationCandidateItem {
  candidate_id: string;
  draft_id: string;
  source_alert_id: string;
  symbol?: string | null;
  timeframe?: string | null;
  condition?: string | null;
  direction?: string | null;
  confidence?: number | null;
  trigger_level?: number | null;
  invalidation_level?: number | null;
  latest_price?: number | null;
  thesis?: string | null;
  entry_criteria?: string | null;
  invalidation_criteria?: string | null;
  risk_notes?: string | null;
  checklist_snapshot: PaperValidationDraftChecklist;
  risk_mode: PaperValidationDraftRiskMode;
  candidate_status: PaperValidationCandidateStatus;
  created_at: string;
  promotion_source?: PromotionSource | null;
  backtest_run_id?: string | null;
  strategy_id?: string | null;
  strategy_version_id?: string | null;
  dataset_hash?: string | null;
  config_hash?: string | null;
  result_hash?: string | null;
  evidence_tier?: SetupEvidenceTier | null;
  sample_size?: number | null;
  oos_expectancy?: string | null;
  regime?: string | null;
  evidence_snapshot?: Record<string, unknown> | null;
}

export interface PaperValidationCandidateSummary {
  total_queued: number;
  total_reviewing: number;
  total_archived: number;
  by_condition?: Record<string, number>;
  by_symbol?: Record<string, number>;
  latest_created_at?: string | null;
}

export interface PaperValidationCandidateQueueResult {
  candidate: PaperValidationCandidateItem;
  already_exists: boolean;
}

export type PaperValidationRunPlanStatus = "planned" | "needs_revision" | "archived";

export interface PaperValidationRunPlanItem {
  plan_id: string;
  candidate_id: string;
  draft_id: string;
  source_alert_id: string;
  symbol?: string | null;
  timeframe?: string | null;
  condition?: string | null;
  direction?: string | null;
  confidence?: number | null;
  trigger_level?: number | null;
  invalidation_level?: number | null;
  latest_price?: number | null;
  thesis?: string | null;
  entry_criteria?: string | null;
  invalidation_criteria?: string | null;
  risk_notes?: string | null;
  checklist_snapshot: PaperValidationDraftChecklist;
  risk_mode: PaperValidationDraftRiskMode;
  plan_status: PaperValidationRunPlanStatus;
  validation_window?: string | null;
  observation_timeframe?: string | null;
  max_duration_minutes?: number | null;
  planned_entry_rule?: string | null;
  planned_invalidation_rule?: string | null;
  planned_success_criteria?: string | null;
  planned_failure_criteria?: string | null;
  created_at: string;
}

export interface PaperValidationRunPlanSummary {
  total_planned: number;
  total_needs_revision: number;
  total_archived: number;
  by_condition?: Record<string, number>;
  by_symbol?: Record<string, number>;
  latest_created_at?: string | null;
}

export interface PaperValidationRunPlanCreateResult {
  plan: PaperValidationRunPlanItem;
  already_exists: boolean;
}

export type PaperValidationRunSessionStatus = "running" | "completed" | "cancelled";

export interface PaperValidationRunSessionItem {
  session_id: string;
  run_plan_id: string;
  candidate_id: string;
  draft_id: string;
  source_alert_id: string;
  symbol?: string | null;
  timeframe?: string | null;
  condition?: string | null;
  direction?: string | null;
  risk_mode: PaperValidationDraftRiskMode;
  validation_window?: string | null;
  observation_timeframe?: string | null;
  max_duration_minutes?: number | null;
  session_status: PaperValidationRunSessionStatus;
  notes?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  created_at: string;
}

export interface PaperValidationRunSessionStartResult {
  session: PaperValidationRunSessionItem;
  already_active: boolean;
}

export type PaperValidationObservationKind =
  | "approached_trigger"
  | "hit_trigger"
  | "hit_invalidation"
  | "missed_entry"
  | "price_moved_without_entry"
  | "price_update"
  | "general_note";

export type PaperValidationOutcome =
  | "success"
  | "failure"
  | "invalidated"
  | "missed_entry"
  | "no_trade"
  | "inconclusive";

export type PaperValidationCriteriaMet = "met" | "not_met" | "partial" | "unknown";

export type PaperValidationEntryAssessment =
  | "entered_as_planned"
  | "missed_entry"
  | "price_moved_without_entry"
  | "no_entry";

export type PaperValidationDisciplineAssessment =
  | "disciplined"
  | "should_have_waited"
  | "should_have_entered"
  | "should_have_avoided";

export interface PaperValidationSessionObservationItem {
  observation_id: string;
  run_session_id: string;
  run_plan_id: string;
  observation_kind: PaperValidationObservationKind;
  observed_price?: number | null;
  observed_at?: string | null;
  note?: string | null;
  created_at: string;
}

export interface PaperValidationSessionResultItem {
  result_id: string;
  run_session_id: string;
  run_plan_id: string;
  outcome: PaperValidationOutcome;
  success_criteria_met: PaperValidationCriteriaMet;
  success_criteria_notes?: string | null;
  failure_criteria_met: PaperValidationCriteriaMet;
  failure_criteria_notes?: string | null;
  invalidation_hit: boolean;
  invalidation_notes?: string | null;
  entry_assessment: PaperValidationEntryAssessment;
  discipline_assessment: PaperValidationDisciplineAssessment;
  behaved_as_expected?: boolean | null;
  lessons?: string | null;
  recorded_at: string;
  created_at: string;
}

export interface PaperValidationSessionResultCreateResult {
  result: PaperValidationSessionResultItem;
  already_exists: boolean;
}

export const RECORD_PAPER_VALIDATION_OBSERVATION = "RECORD_PAPER_VALIDATION_OBSERVATION";
export const RECORD_PAPER_VALIDATION_OUTCOME = "RECORD_PAPER_VALIDATION_OUTCOME";

export interface SetupAlertDraftCreateResult {
  draft: PaperValidationDraftItem;
  already_exists: boolean;
}

export interface AlertDeliveryStatusResponse {
  delivery_enabled: boolean;
  webhook_enabled: boolean;
  telegram_enabled: boolean;
  email_enabled: boolean;
  push_enabled: boolean;
  webhook_configured: boolean;
  telegram_configured?: boolean;
  effective_external_enabled: boolean;
  channels: string[];
  channel_statuses?: ChannelProviderStatus[];
  paper_only: boolean;
  limitations?: string[];
}

export interface ChannelProviderStatus {
  channel: string;
  env_enabled: boolean;
  user_enabled: boolean;
  configured: boolean;
  available: boolean;
  status_label: string;
}

export type TelegramPolicySeverity = "INFO" | "WATCH" | "ACTION" | "CRITICAL";
export type TelegramPolicyEventType =
  | "SETUP"
  | "RISK"
  | "PAPER_TRADE_OPENED"
  | "PAPER_TRADE_CLOSED"
  | "STOP"
  | "PARTIAL_PROFIT"
  | "DAILY_REVIEW"
  | "OTHER"
  | "SFP_SWEEP_DETECTED"
  | "SFP_RECLAIM_FORMING"
  | "SFP_CONFIRMED"
  | "SFP_INVALIDATED"
  | "SFP_EXPIRED"
  | "SFP_BLOCKED_BY_RISK";

/** Backend Policy V2: nested PATCH is a full replacement, null lists allow all. */
export interface TelegramNotificationPolicyV2 {
  schema_version: 2;
  strategy_subscriptions: string[] | null;
  symbol_subscriptions: string[] | null;
  setup_stages: string[] | null;
  event_types: TelegramPolicyEventType[] | null;
  severities: TelegramPolicySeverity[] | null;
  minimum_severity: TelegramPolicySeverity;
  minimum_quality: number | string | null;
  forming_alerts: boolean;
  confirmed_alerts: boolean;
  risk_alerts: boolean;
  paper_trade_opened: boolean;
  paper_trade_closed: boolean;
  stop_event: boolean;
  partial_profit_event: boolean;
  daily_review_event: boolean;
  cooldown_seconds: number;
  duplicate_suppression_seconds: number;
  quiet_hours: { start: string; end: string; timezone: string } | null;
}

export interface NotificationPreferences {
  telegram_policy?: TelegramNotificationPolicyV2 | null;
  in_app_enabled: boolean;
  webhook_enabled: boolean;
  telegram_enabled: boolean;
  min_severity: string;
  enabled_alert_types?: string[] | null;
  quiet_hours_enabled?: boolean;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  timezone?: string;
  digest_mode?: string;
  telegram_chat_id?: string | null;
  using_defaults?: boolean;
}

export interface NotificationTestResult {
  success: boolean;
  message: string;
  channels_attempted: string[];
  channels_succeeded: string[];
  channels_skipped: string[];
  errors: Record<string, string>;
  paper_only: boolean;
  test_label: string;
}

export interface TelegramEnrollmentStartResponse {
  challenge_id: string;
  expires_at: string;
  token: string;
  bot_id: string;
}

export interface MarketWatcherStatus {
  env_enabled: boolean;
  effective_enabled: boolean;
  watched_symbols: string[];
  last_scan_at?: string | null;
  paper_only: boolean;
  real_trading_enabled: boolean;
}

export type WatcherMonitoringRuntimeState =
  | "RUNNING"
  | "STOPPED"
  | "DEGRADED"
  | "STALE"
  | "BLOCKED";

export interface WatcherConfigFlags {
  market_watcher_enabled: boolean;
  watcher_orchestration_enabled: boolean;
  worker_enabled: boolean;
  market_watcher_bridge_enabled: boolean;
  market_watcher_bridge_auto_tick: boolean;
  telegram_alerts_enabled: boolean;
  telegram_interaction_enabled: boolean;
  automatic_telegram_delivery_enabled: boolean;
  telegram_paper_activation_armed?: boolean;
  telegram_inbound_mode?: string;
  telegram_network_permitted?: boolean;
}

export interface PaperMonitoringPosture {
  paper_only: true;
  execution_mode: string;
  real_trading_enabled: boolean;
  kill_switch_blocked: boolean;
  kill_switch_reason_code?: string | null;
  telegram_enabled: boolean;
  watcher_config_enabled: boolean;
  runtime_evidence: boolean;
}

export interface WatcherApprovedStrategy {
  strategy_id: string;
  strategy_version_id: string;
  name: string;
  lifecycle_state: string;
  compiled: boolean;
}

export interface WatcherSetupAssessmentSummary {
  assessment_id: string;
  candidate_id: string;
  state: string;
  strategy_version_id: string;
  instrument: string;
  timeframe: string;
  valid_until: string;
  live_executable: false;
}

export interface WatcherDetectedCandidateSummary {
  count: number;
  conditions: string[];
  last_scan_at?: string | null;
  source: "market_watcher_scan";
}

export interface WatcherCanonicalCandidateSummary {
  candidate_id: string;
  assessment_id: string;
  state: string;
  instrument: string;
  timeframe: string;
  strategy_version_id: string;
  created_at: string;
}

export interface WatcherMarketFreshness {
  status: "fresh" | "stale" | "degraded" | "unavailable" | "replay" | "unknown";
  observed_at?: string | null;
  symbol?: string | null;
  data_freshness?: string | null;
  quote_max_age_seconds?: number;
  stale_after_minutes?: number | null;
  legacy_scanner_stale_after_minutes?: number | null;
  quote_fresh?: boolean;
  trade_stream_fresh?: boolean;
  closed_candle_final?: boolean | null;
  historical_evidence_valid?: boolean | null;
  setup_lifetime_expired?: boolean | null;
  usable_as_current_market_price?: boolean;
  presentation?: string | null;
  availability?: string | null;
}

export interface WatcherProviderHealthItem {
  name: string;
  kind: string;
  health: string;
  using_fallback: boolean;
  is_mock: boolean;
  detail?: string | null;
  error_message?: string | null;
}

export interface WatcherLeaseHealth {
  scan_scope: string;
  owner_id?: string | null;
  lease_epoch: number;
  fencing_token: number;
  expires_at?: string | null;
  last_beat_at?: string | null;
  seconds_since_beat?: number | null;
  fenced: boolean;
  heartbeat_fresh: boolean;
  orchestration_state?: string | null;
  reason_code?: string | null;
}

export interface WatcherWorkerHealth {
  name: string;
  worker_enabled: boolean;
  heartbeat_live: boolean;
  last_beat_at?: string | null;
  status?: string | null;
  paused?: boolean | null;
  detail?: string | null;
}

export interface WatcherRecentError {
  source: string;
  message: string;
  at?: string | null;
  reason_code?: string | null;
}

export interface WatcherMonitoringSnapshot {
  watcher_status: WatcherMonitoringRuntimeState;
  paper_monitoring_status: WatcherMonitoringRuntimeState;
  reason_code: string;
  block_reasons: string[];
  warnings: string[];
  paper_posture: PaperMonitoringPosture;
  config_flags: WatcherConfigFlags;
  symbols_monitored: string[];
  approved_strategies: WatcherApprovedStrategy[];
  last_scan_at?: string | null;
  last_scan_status?: string | null;
  next_scan_at?: string | null;
  next_scan_basis?: "worker_interval" | "lease_ttl" | "bridge_interval" | "paper_poll" | null;
  market_freshness: WatcherMarketFreshness;
  provider_health: WatcherProviderHealthItem[];
  setup_assessments: WatcherSetupAssessmentSummary[];
  scanner_candidates: WatcherDetectedCandidateSummary;
  canonical_candidates: WatcherCanonicalCandidateSummary[];
  leases: WatcherLeaseHealth[];
  worker: WatcherWorkerHealth;
  recent_errors: WatcherRecentError[];
  limitations: string[];
  generated_at: string;
  paper_only: true;
}

export interface MarketWatcherSummary {
  scanner_enabled: boolean;
  manual_scan_available: boolean;
  worker_enabled: boolean;
  worker_running: boolean;
  symbols_supported: string[];
  timeframes_supported: string[];
  detectors_enabled: string[];
  detector_versions: Record<string, string>;
  last_scan_at?: string | null;
  last_scan_status?: "ok" | "blocked" | "degraded" | null;
  last_scan_alerts_created: number;
  last_scan_alerts_deduped: number;
  last_scan_candidate_count: number;
  last_scan_conditions_found: string[];
  last_scan_symbols: string[];
  last_scan_timeframes: string[];
  last_scan_dry_run?: boolean | null;
  last_scan_error?: string | null;
  paper_only: boolean;
  readiness: "ready" | "degraded" | "blocked";
  warnings: string[];
  generated_at: string;
}

export interface MarketWatcherScanSummary {
  id: string;
  organization_id: string;
  scanned_at: string;
  status: "ok" | "blocked" | "degraded";
  error?: string | null;
  alerts_created: number;
  alerts_deduped: number;
  candidate_count: number;
  conditions_found: string[];
  symbols: string[];
  timeframes: string[];
  detectors_enabled: string[];
  detector_versions: Record<string, string>;
  dry_run: boolean;
  created_at: string;
}

export interface PaginatedMarketWatcherRecentScans {
  items: MarketWatcherScanSummary[];
  total: number;
  limit: number;
}

export interface MarketWatcherCandidate {
  symbol: string;
  timeframe: string;
  condition: string;
  message: string;
  severity: string;
  metrics: Record<string, unknown>;
  direction?: string | null;
  confidence?: number | null;
  reason?: string | null;
  trigger_level?: string | number | null;
  invalidation_level?: string | number | null;
  source?: string;
  detector_version?: string | null;
  created_alert_id?: string | null;
  deduped?: boolean;
}

export interface MarketWatcherScanRequest {
  confirm: string;
  create_in_app_alerts_confirm?: string | null;
  symbols?: string[];
  timeframes?: string[];
  dry_run?: boolean;
}

export interface MarketWatcherScanResult {
  scanned_at: string;
  env_enabled: boolean;
  effective_enabled: boolean;
  symbols_scanned: number;
  observations_created: number;
  setup_signals: string[];
  decisions: string[];
  paper_only: boolean;
  dry_run: boolean;
  status: "ok" | "blocked" | "degraded";
  candidates: MarketWatcherCandidate[];
  alerts_created: number;
  alerts_deduped: number;
  error?: string | null;
}

export interface MarketWatcherObservation {
  id: string;
  symbol: string;
  exchange: string;
  timeframe: string;
  observed_at: string;
  status: string;
  data_freshness?: string | null;
  related_strategy_id?: string | null;
  related_paper_validation_run_id?: string | null;
}

export interface PaginatedMarketWatcherObservations {
  items: MarketWatcherObservation[];
  total: number;
  limit: number;
  offset: number;
}

export interface MarketWatcherBridgeStatus {
  env_enabled: boolean;
  auto_tick_enabled: boolean;
  effective_enabled: boolean;
  last_tick_at?: string | null;
  last_tick_status?: string | null;
  decisions_last_tick: number;
  scans_triggered_last_tick: number;
  paper_only: boolean;
  real_trading_enabled: boolean;
}

export interface MarketWatcherBridgeDecision {
  id: string;
  decision: string;
  symbol?: string | null;
  reason?: string | null;
  blockers: string[];
  triggered_scan_id?: string | null;
  created_alert_id?: string | null;
  created_at: string;
}

export interface MarketWatcherBridgeTickResult {
  ticked_at: string;
  env_enabled: boolean;
  effective_enabled: boolean;
  observations_processed: number;
  scans_triggered: number;
  decisions: string[];
  paper_only: boolean;
}

export interface PaginatedMarketWatcherBridgeHistory {
  items: MarketWatcherBridgeDecision[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaperAlertSummary {
  total: number;
  unread: number;
  by_type: Record<string, number>;
  by_severity: Record<string, number>;
}

export interface PaginatedBacktestRuns {
  items: BacktestRun[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaginatedUserStrategies {
  items: UserStrategy[];
  total: number;
  limit: number;
  offset: number;
}

export interface ManualChartLevel {
  id: string;
  symbol: string;
  exchange: string;
  timeframe?: Timeframe | null;
  level_type: ManualLevelType;
  price?: string | null;
  price_low?: string | null;
  price_high?: string | null;
  label?: string | null;
  notes?: string | null;
  enabled: boolean;
}

export interface PaginatedManualChartLevels {
  items: ManualChartLevel[];
  total: number;
  limit: number;
  offset: number;
}

export interface PositionSizingResult {
  entry_price: string;
  invalidation_level: string;
  stop_loss_distance: string;
  account_balance: string;
  max_risk_percent: string;
  maximum_acceptable_loss: string;
  notional_position_size: string;
  leverage_limit: string;
  leverage_recommendation: string;
  risk_reward_ratio?: number | null;
  required_breakeven_win_rate?: number | null;
  confidence_score: number;
  confidence_adjusted_size: string;
  worst_case_scenario: string;
  final_recommendation: PreTradeRecommendation;
  planned_loss_amount: string;
}

export interface PreTradeAnalyzeResponse {
  symbol: string;
  exchange: string;
  direction_considered?: TradeDirection | null;
  bullish_factors: string[];
  bearish_factors: string[];
  market_regime: string;
  trend_alignment_score: number;
  volume_confirmation_score: number;
  funding_risk_score: number;
  setup_confidence_score: number;
  risk_reward?: number | null;
  suggested_entry_zone?: Record<string, string> | null;
  suggested_stop_loss?: string | null;
  invalidation: string[];
  tp_levels: Record<string, string>[];
  runner_logic: string[];
  position_size?: PositionSizingResult | null;
  leverage_recommendation?: string | null;
  final_recommendation: PreTradeRecommendation;
  limitations: string[];
}

export interface RunnerAnalysis {
  early_exit_flag?: boolean | null;
  missed_profit_estimate?: string | null;
  max_favorable_excursion_after_exit?: string | null;
  max_adverse_excursion_after_exit?: string | null;
  would_runner_have_helped?: boolean | null;
  tp2_would_have_hit?: boolean | null;
  tp3_would_have_hit?: boolean | null;
  runner_invalidation_would_have_hit?: boolean | null;
  recommended_lesson?: string | null;
  confidence?: string;
  limitations?: string[];
}

export interface StopLossAnalysis {
  stop_violation_flag?: boolean | null;
  planned_loss?: string | null;
  actual_loss?: string | null;
  avoidable_loss_estimate?: string | null;
  lesson?: string | null;
  future_restriction_suggestion?: string | null;
  limitations?: string[];
}

export interface HumanVsSystemComparison {
  trade_id: string;
  symbol?: string | null;
  entry_quality_delta_pct?: number | null;
  exit_quality_delta?: string | null;
  size_discipline_delta_pct?: number | null;
  leverage_discipline_delta?: string | null;
  stop_loss_discipline_delta?: string | null;
  planned_loss_vs_actual?: string | null;
  early_exit_flag?: boolean | null;
  missed_runner?: RunnerAnalysis | null;
  emotional_mistake_classification?: string[];
  rule_violation_cost_estimate?: string | null;
  plan_adherence_score: number;
  plan_adherence: {
    entry_followed_plan: number;
    size_respected_risk: number;
    stop_loss_respected: number;
    profit_taking_followed: number;
    emotion_controlled: number;
    journal_completed: number;
  };
  system_would_have_done?: string | null;
  backtest_context?: string | null;
  stop_loss_analysis?: StopLossAnalysis | null;
  entry_delta_pct?: number | null;
  exit_delta?: string | null;
  size_delta_pct?: number | null;
  leverage_delta?: string | null;
  stop_behavior_delta?: string | null;
  missed_runner_profit_placeholder?: string | null;
  emotion_tags: string[];
  notes: string[];
  limitations?: string[];
}

export interface StrategyTestability {
  strategy_id: string;
  score: number;
  band: string;
  ready_for_backtest: boolean;
  missing_fields: { field_key: string; label: string; severity?: string }[];
  unsupported_rule_types?: string[];
  ambiguous_conditions?: string[];
  not_backtestable_reason?: string | null;
  suggested_edits?: string[];
  has_structured_rules: boolean;
  structured_rules?: StructuredRules | null;
  limitations?: string[];
}

export interface RuleCondition {
  timeframe?: string | null;
  indicator?: string | null;
  operator?: string | null;
  value?: string | number | null;
  lookback_candles?: number | null;
  confirmation_required?: boolean;
}

export interface StructuredRules {
  primary_timeframe?: string | null;
  entry_rules: {
    trigger_type: string;
    direction?: string;
    conditions?: RuleCondition[];
    notes?: string | null;
  }[];
  exit_rules: {
    rule_type: string;
    value?: string | number | null;
    r_multiple?: string | number | null;
    size_fraction?: number | null;
    conditions?: RuleCondition[];
    notes?: string | null;
  }[];
  no_trade_rules: {
    rule_type: string;
    threshold?: string | number | null;
    conditions?: RuleCondition[];
    notes?: string | null;
  }[];
}

export interface ProposedRuleUpdate {
  summary: string;
  structured_rules_patch?: StructuredRules | null;
  create_new_version?: boolean;
  attach_to_strategy?: boolean;
}

export interface LessonCandidate {
  id: string;
  organization_id: string;
  user_id: string;
  source_type: string;
  source_id?: string | null;
  related_strategy_id?: string | null;
  related_trade_id?: string | null;
  related_journal_entry_id?: string | null;
  lesson_text: string;
  mistake_type: string;
  severity: string;
  confidence?: string | null;
  status: string;
  proposed_rule_update?: ProposedRuleUpdate | null;
  accepted_rule_update?: ProposedRuleUpdate | null;
  reviewer_notes?: string | null;
  analysis_metadata?: Record<string, unknown> | null;
  created_at: string;
  reviewed_at?: string | null;
}

export interface PaginatedLessonCandidates {
  items: LessonCandidate[];
  total: number;
  limit: number;
  offset: number;
}

export interface ExchangeInstrumentSummary {
  symbol: string;
  inst_id: string;
  active?: boolean | null;
  probe_ok: boolean;
}

export interface ExchangeLeverageSummary {
  inst_id: string;
  margin_mode: string;
  leverage?: string | null;
  probe_ok: boolean;
}

export interface ExchangeDiagnosticsSummary {
  exchange_mode: string;
  execution_mode: string;
  real_trading_enabled: boolean;
  demo_active: boolean;
  provider_health?: string | null;
  worker_enabled: boolean;
  telegram_enabled: boolean;
  position_mode?: string | null;
  leverage?: ExchangeLeverageSummary | null;
  instrument?: ExchangeInstrumentSummary | null;
  venue_positions_count?: number | null;
  last_exchange_order_status?: string | null;
  last_demo_mirror_result?: string | null;
  last_demo_mirror_error_code?: string | null;
  last_demo_mirror_error_message?: string | null;
  last_cancel_status?: string | null;
  readiness: "ready" | "degraded" | "blocked";
  warnings: string[];
  generated_at: string;
}

export interface QuietHoursSummary {
  enabled: boolean;
  start?: string | null;
  end?: string | null;
  timezone: string;
  source: string;
}

export interface AlertRoutingSummary {
  alerts_enabled: boolean;
  telegram_enabled: boolean;
  telegram_configured: boolean;
  telegram_chat_configured: boolean;
  manual_test_available: boolean;
  last_test_alert_at?: string | null;
  last_test_alert_status?: string | null;
  telegram_alert_delivery_available: boolean;
  telegram_delivered_count: number;
  telegram_failed_count: number;
  telegram_last_delivery_at?: string | null;
  telegram_last_delivery_status?: string | null;
  webhook_enabled: boolean;
  external_delivery_enabled: boolean;
  paper_only: boolean;
  quiet_hours: QuietHoursSummary;
  severity_filters: string[];
  last_alert_created_at?: string | null;
  last_alert_status?: string | null;
  pending_alerts_count: number;
  delivered_alerts_count: number;
  failed_alerts_count: number;
  market_watcher_configured: boolean;
  market_watcher_running: boolean;
  bridge_enabled: boolean;
  bridge_running: boolean;
  bridge_last_tick_at?: string | null;
  bridge_last_decision?: string | null;
  bridge_last_error?: string | null;
  worker_enabled: boolean;
  worker_running: boolean;
  readiness: "ready" | "degraded" | "blocked";
  automatic_telegram_delivery_ready: boolean;
  automatic_delivery_blockers: string[];
  eligible_pending_telegram_count: number;
  already_delivered_telegram_count: number;
  next_delivery_preview_count: number;
  delivery_limits: {
    max_preview_limit: number;
    default_preview_limit: number;
    max_automatic_batch_limit: number;
  };
  dry_run_supported: boolean;
  warnings: string[];
  generated_at: string;
}

export interface AlertDeliveryPreviewItem {
  alert_id: string;
  alert_type: string;
  severity: string;
  message_preview: string;
  created_at: string;
  status: "eligible" | "skipped" | "already_delivered";
  reason?: string | null;
}

export interface AlertDeliveryPreviewResponse {
  channel: string;
  eligible_count: number;
  skipped_count: number;
  already_delivered_count: number;
  items: AlertDeliveryPreviewItem[];
  warnings: string[];
  generated_at: string;
}

export interface TelegramAlertDeliveryResponse {
  status: "sent" | "already_delivered" | "skipped_not_configured" | "blocked" | "failed_redacted";
  alert_id: string;
  channel: string;
  sent_at?: string | null;
  delivery_id?: string | null;
  error_code?: string | null;
  error_message?: string | null;
}

export interface TelegramTestAlertResponse {
  status: "sent" | "skipped_not_configured" | "blocked" | "failed_redacted";
  telegram_configured: boolean;
  chat_configured: boolean;
  paper_only: boolean;
  external_delivery_enabled: boolean;
  sent_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
}

export interface LessonSourceMetadata {
  lesson_id: string;
  mistake_type: string;
  accepted_lesson_text: string;
  rule_update_summary?: string | null;
  reviewer_notes?: string | null;
  created_at: string;
}

export interface UserStrategyVersion {
  id: string;
  strategy_id: string;
  version: number;
  card: Record<string, unknown>;
  validation_status: string;
  backtest_status: string;
  paper_validation_status: string;
  lesson_source_metadata?: LessonSourceMetadata | null;
  created_at: string;
}

export interface PaginatedUserStrategyVersions {
  items: UserStrategyVersion[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaperEligibilityReport {
  strategy_id: string;
  status: string;
  paper_eligible: boolean;
  testability_score: number;
  blockers: string[];
  eligibility_reasons: string[];
  latest_backtest?: {
    trade_count: number;
    win_rate: number;
    profit_factor: number;
    expectancy: string;
    max_drawdown_pct: number;
    recommendation?: string | null;
  } | null;
  accepted_lessons: LessonCandidate[];
  unresolved_lesson_candidates: LessonCandidate[];
  paper_validation_recommendation?: string | null;
  recommendation: string;
  real_trading_enabled: boolean;
  limitations: string[];
}

// --------------------------------------------------------------------------
// Journal statistics (AT-031) — deterministic aggregates over journal trades
// --------------------------------------------------------------------------

export type JournalStatsGroupBy =
  | "overall"
  | "setup"
  | "setup_version"
  | "strategy"
  | "strategy_version"
  | "symbol"
  | "timeframe"
  | "market_regime"
  | "source"
  | "entry_method"
  | "rule_compliance"
  | "execution_actor";

/** AT-033: how a canonical journal trade record was created. */
export type JournalEntryMethod = "manual" | "auto" | "import" | "backfill";

export type JournalTradeSource =
  | "manual_demo_test"
  | "manual"
  | "paper_execution"
  | "paper_validation"
  | "backtest"
  | "imported"
  | "system";

export type MarketRegime =
  | "trending_up"
  | "trending_down"
  | "ranging"
  | "volatile"
  | "quiet"
  | "unknown";

export type TradeRuleCompliance = "compliant" | "partial" | "violated" | "unassessed";
export type ExecutionActor = "human" | "system";
export type SampleConfidence = "insufficient" | "low" | "moderate" | "high";

export interface JournalStatsWarning {
  code: string;
  message: string;
}

export interface JournalTradeStatsMetrics {
  trade_count: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate: number | null;
  pnl_sample_count: number;
  net_pnl_total: string | null;
  gross_pnl_total: string | null;
  expectancy: string | null;
  average_winner: string | null;
  average_loser: string | null;
  profit_factor: number | null;
  r_sample_count: number;
  average_r: number | null;
  cost_sample_count: number;
  fees_total: string | null;
  funding_total: string | null;
  slippage_total: string | null;
  total_costs: string | null;
  mfe_sample_count: number;
  average_mfe_amount: string | null;
  mae_sample_count: number;
  average_mae_amount: string | null;
  capture_sample_count: number;
  available_profit_total: string | null;
  realized_on_available_total: string | null;
  average_realized_vs_available_pct: number | null;
  confidence: SampleConfidence;
  warnings: JournalStatsWarning[];
}

export interface JournalStatsBucket {
  key: string;
  group_id?: string | null;
  label: string;
  metrics: JournalTradeStatsMetrics;
}

export interface JournalStatsFilters {
  source?: JournalTradeSource | null;
  entry_method?: JournalEntryMethod | null;
  symbol?: string | null;
  timeframe?: string | null;
  market_regime?: MarketRegime | null;
  setup_id?: string | null;
  user_strategy_id?: string | null;
  strategy_version_id?: string | null;
  rule_compliance?: TradeRuleCompliance | null;
  execution_actor?: ExecutionActor | null;
  date_from?: string | null;
  date_to?: string | null;
}

export interface JournalStatsResponse {
  group_by: JournalStatsGroupBy;
  filters: JournalStatsFilters;
  overall: JournalTradeStatsMetrics;
  buckets: JournalStatsBucket[];
  total_buckets: number;
  limit: number;
  offset: number;
  truncated: boolean;
  max_rows: number;
  generated_at: string;
}

export type JournalStatsParams = {
  group_by?: JournalStatsGroupBy;
  source?: JournalTradeSource;
  entry_method?: JournalEntryMethod;
  symbol?: string;
  timeframe?: string;
  market_regime?: MarketRegime;
  setup_id?: string;
  user_strategy_id?: string;
  strategy_version_id?: string;
  rule_compliance?: TradeRuleCompliance;
  execution_actor?: ExecutionActor;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
};

// --------------------------------------------------------------------------
// Journal bulk import (AT-033) — dedup, dry-run, reconciliation
// --------------------------------------------------------------------------

export type JournalImportMode = "dry_run" | "commit";

export type JournalImportRowOutcome =
  | "created"
  | "would_create"
  | "duplicate"
  | "invalid";

export type JournalImportBatchStatus = "dry_run" | "committed" | "failed";

/** One row submitted for import; matches backend JournalImportRow fields. */
export interface JournalImportRowInput {
  symbol: string;
  timeframe: string;
  direction: string;
  status?: string;
  exchange?: string;
  strategy_label?: string;
  entry_price?: string;
  entry_time?: string;
  exit_price?: string;
  exit_time?: string;
  exit_reason?: string;
  size?: string;
  leverage?: string;
  fees?: string;
  funding?: string;
  slippage?: string;
  gross_pnl?: string;
  net_pnl?: string;
  result?: string;
  notes?: string;
  tags?: string[];
  external_ref?: string;
}

export interface JournalImportRequestBody {
  mode: JournalImportMode;
  source_label?: string | null;
  rows: JournalImportRowInput[];
}

export interface JournalImportRowResult {
  index: number;
  outcome: JournalImportRowOutcome;
  external_ref?: string | null;
  journal_trade_id?: string | null;
  errors: string[];
}

export interface JournalImportResult {
  mode: JournalImportMode;
  committed: boolean;
  batch_id: string | null;
  total_rows: number;
  created_count: number;
  duplicate_count: number;
  invalid_count: number;
  results: JournalImportRowResult[];
}

export interface JournalImportBatch {
  id: string;
  organization_id: string;
  user_id: string;
  status: JournalImportBatchStatus;
  source_label?: string | null;
  total_rows: number;
  created_count: number;
  duplicate_count: number;
  invalid_count: number;
  row_report: JournalImportRowResult[];
  created_at: string;
}

export interface PaginatedJournalImportBatches {
  items: JournalImportBatch[];
  total: number;
  limit: number;
  offset: number;
}

export interface WatcherWatchlistSlot {
  position: number;
  symbol: string;
  enabled: boolean;
}

export interface WatcherWatchlistConfiguration {
  revision: number;
  updated_at: string;
  max_enabled: number;
  slots: WatcherWatchlistSlot[];
  paper_only: boolean;
}

export interface WatcherSymbolRuntimeStatus {
  configuration_revision: number;
  observed_at: string | null;
  position: number;
  symbol: string;
  enabled: boolean;
  market_source: string;
  freshness: string;
  last_successful_scan: string | null;
  last_failed_scan: string | null;
  setup_state: string;
  strategy_matches: string[];
  alert_state: string;
  error_state: string | null;
}

export interface WatcherWatchlistStatus {
  configuration_revision: number;
  observed_at: string;
  stale_after_seconds: number;
  paper_only: boolean;
  real_trading_enabled: boolean;
  symbols: WatcherSymbolRuntimeStatus[];
}
