import type {
  JournalComparisonParams,
  JournalStatsParams,
  JournalTradeSource,
  LearningAnalyticsParams,
  MarketRegime,
  PaperPortfolioParams,
  PortfolioSourceFilter,
  SetupDimension,
  SetupEvidenceParams,
  StrategyQualityParams,
  TradeRuleCompliance,
} from "@/lib/api/types";

import { humanizeToken } from "@/lib/format";

import { isoDateOnly } from "./format";
import { VALIDATION_DIMENSION_LABELS } from "./validationDimensionCopy";

export type AnalyticsTab =
  | "overview"
  | "performance"
  | "setups"
  | "behaviour"
  | "validation"
  | "comparison";

export type SetupGroupBy = "setup" | "setup_version" | "strategy";

export type ValidationDimension = SetupDimension;

export const DEFAULT_VALIDATION_MIN_SAMPLE = 5;
export const DEFAULT_VALIDATION_DIMENSION: ValidationDimension = "condition";

export const VALIDATION_DIMENSION_OPTIONS: ValidationDimension[] = [
  "condition",
  "timeframe",
  "symbol",
  "direction",
  "confidence_bucket",
];

export type AnalyticsFilterState = {
  tab: AnalyticsTab;
  dateFrom: string | null;
  dateTo: string | null;
  symbol: string | null;
  timeframe: string | null;
  portfolioSource: PortfolioSourceFilter | null;
  /** Journal trade source — Setups / Behaviour / Comparison; never portfolio setup-evidence dates. */
  journalSource: JournalTradeSource | null;
  /** Journal setup-definition UUID only — never a Portfolio setup identity. */
  setupId: string | null;
  /** UserStrategy identity — journal statistics use user_strategy_id only. */
  userStrategyId: string | null;
  strategyVersionId: string | null;
  ruleCompliance: TradeRuleCompliance | null;
  marketRegime: MarketRegime | null;
  groupBy: SetupGroupBy;
  bucketOffset: number;
  /** Validation-tab sample gate (1–100). Default 5 when tab=validation. */
  minSample: number;
  /** Validation-tab LA setup-performance / ranking dimension. */
  dimension: ValidationDimension;
  ignoredParams: string[];
};

export type AnalyticsWindowParams = {
  start_date?: string;
  end_date?: string;
};

export type ValidationApiParams = LearningAnalyticsParams & {
  dimension: ValidationDimension;
};

export type AnalyticsFilterParams = {
  journal: JournalStatsParams;
  portfolio: PaperPortfolioParams;
  ruleComplianceJournal: JournalStatsParams;
  comparison: JournalComparisonParams;
  analyticsWindow: AnalyticsWindowParams;
  /** Dates only — Behaviour learning discipline must not inherit Validation min_sample. */
  learningWindow: LearningAnalyticsParams;
  /** Validation LA/SQ window: dates + min_sample (+ dimension for setup endpoints). */
  validation: ValidationApiParams;
  strategyQuality: StrategyQualityParams;
  state: AnalyticsFilterState;
};

export type SetupAnalyticsApiParams = {
  journal: JournalStatsParams;
  evidence: SetupEvidenceParams;
  state: AnalyticsFilterState;
};

const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_SYMBOL_LENGTH = 30;
const MAX_TIMEFRAME_LENGTH = 8;
export const SETUP_BUCKET_PAGE_SIZE = 20;

const VALID_TABS = new Set<AnalyticsTab>([
  "overview",
  "performance",
  "setups",
  "behaviour",
  "validation",
  "comparison",
]);
const VALID_DIMENSIONS = new Set<ValidationDimension>(VALIDATION_DIMENSION_OPTIONS);
const VALID_PP_SOURCES = new Set<PortfolioSourceFilter>([
  "all",
  "proposal_flow",
  "paper_validation",
]);
export const JOURNAL_TRADE_SOURCE_OPTIONS: JournalTradeSource[] = [
  "manual",
  "paper_execution",
  "paper_validation",
  "backtest",
  "imported",
  "system",
  "blofin_demo_manual",
];
const VALID_JOURNAL_SOURCES = new Set<JournalTradeSource>(JOURNAL_TRADE_SOURCE_OPTIONS);
const VALID_SETUP_GROUP_BY = new Set<SetupGroupBy>(["setup", "setup_version", "strategy"]);
const VALID_RULE_COMPLIANCE = new Set<TradeRuleCompliance>([
  "compliant",
  "partial",
  "violated",
  "unassessed",
]);
const VALID_MARKET_REGIMES = new Set<MarketRegime>([
  "trending_up",
  "trending_down",
  "ranging",
  "volatile",
  "quiet",
  "unknown",
]);

const SETUPS_TAB: AnalyticsTab = "setups";
const BEHAVIOUR_TAB: AnalyticsTab = "behaviour";
const VALIDATION_TAB: AnalyticsTab = "validation";
const COMPARISON_TAB: AnalyticsTab = "comparison";
const JOURNAL_IDENTITY_TABS = new Set<AnalyticsTab>([SETUPS_TAB, BEHAVIOUR_TAB, COMPARISON_TAB]);

/** Params never accepted as Portfolio identities on Analytics. */
const ALWAYS_UNSUPPORTED_PARAM_KEYS = ["portfolio_setup"] as const;

export function isValidIsoDate(value: string): boolean {
  if (!ISO_DATE_PATTERN.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  if (Number.isNaN(parsed.getTime())) return false;
  return isoDateOnly(parsed) === value;
}

export function isValidSymbol(value: string): boolean {
  const trimmed = value.trim();
  return trimmed.length > 0 && trimmed.length <= MAX_SYMBOL_LENGTH;
}

export function isValidTimeframe(value: string): boolean {
  const trimmed = value.trim();
  return trimmed.length > 0 && trimmed.length <= MAX_TIMEFRAME_LENGTH;
}

/** Journal setup-definition / UserStrategy UUID validator — display names are never accepted. */
export function isValidUuid(value: string): boolean {
  return UUID_PATTERN.test(value.trim());
}

/** @deprecated alias kept for PR3 tests — prefer isValidUuid */
export const isValidSetupDefinitionUuid = isValidUuid;

function parseTab(value: string | null): { tab: AnalyticsTab; ignored: boolean } {
  if (!value || value === "overview") return { tab: "overview", ignored: false };
  if (VALID_TABS.has(value as AnalyticsTab)) return { tab: value as AnalyticsTab, ignored: false };
  return { tab: "overview", ignored: true };
}

function toJournalDatetime(date: string, endOfDay: boolean): string {
  return endOfDay ? `${date}T23:59:59.999Z` : `${date}T00:00:00.000Z`;
}

function parseNonNegativeInt(value: string): number | null {
  if (!/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 0) return null;
  return parsed;
}

function parseMinSample(value: string): number | null {
  const result = validateMinSampleInput(value);
  return result.valid ? result.value : null;
}

export type MinSampleValidationResult =
  | { valid: true; value: number }
  | { valid: false; message: string };

/** Interactive Validation filter validation — rejects blank, zero, negative, non-integer, >100. */
export function validateMinSampleInput(value: string): MinSampleValidationResult {
  const trimmed = value.trim();
  if (!trimmed) {
    return { valid: false, message: "Enter an integer from 1 to 100." };
  }
  if (!/^\d+$/.test(trimmed)) {
    return { valid: false, message: "Min sample must be a whole number from 1 to 100." };
  }
  const parsed = Number(trimmed);
  if (!Number.isSafeInteger(parsed)) {
    return { valid: false, message: "Min sample must be a whole number from 1 to 100." };
  }
  if (parsed < 1) {
    return { valid: false, message: "Min sample must be at least 1." };
  }
  if (parsed > 100) {
    return { valid: false, message: "Min sample cannot exceed 100." };
  }
  return { valid: true, value: parsed };
}

function applySharedJournalStatsFilters(
  journal: JournalStatsParams,
  state: AnalyticsFilterState,
): void {
  if (state.dateFrom) journal.date_from = toJournalDatetime(state.dateFrom, false);
  if (state.dateTo) journal.date_to = toJournalDatetime(state.dateTo, true);
  if (state.symbol) journal.symbol = state.symbol;
  if (state.timeframe) journal.timeframe = state.timeframe;
}

function applyBehaviourJournalFilters(
  journal: JournalStatsParams,
  state: AnalyticsFilterState,
): void {
  applySharedJournalStatsFilters(journal, state);
  if (state.setupId) journal.setup_id = state.setupId;
  if (state.userStrategyId) journal.user_strategy_id = state.userStrategyId;
  if (state.strategyVersionId) journal.strategy_version_id = state.strategyVersionId;
  if (state.journalSource) journal.source = state.journalSource;
  if (state.ruleCompliance) journal.rule_compliance = state.ruleCompliance;
}

function applyComparisonFilters(
  comparison: JournalComparisonParams,
  state: AnalyticsFilterState,
): void {
  if (state.dateFrom) comparison.date_from = toJournalDatetime(state.dateFrom, false);
  if (state.dateTo) comparison.date_to = toJournalDatetime(state.dateTo, true);
  if (state.symbol) comparison.symbol = state.symbol;
  if (state.timeframe) comparison.timeframe = state.timeframe;
  if (state.setupId) comparison.setup_id = state.setupId;
  if (state.userStrategyId) comparison.strategy_id = state.userStrategyId;
  if (state.strategyVersionId) comparison.strategy_version_id = state.strategyVersionId;
  if (state.journalSource) comparison.source = state.journalSource;
  if (state.marketRegime) comparison.market_regime = state.marketRegime;
}

function applyDateWindow(
  target: AnalyticsWindowParams | LearningAnalyticsParams,
  state: AnalyticsFilterState,
): void {
  if (state.dateFrom) target.start_date = state.dateFrom;
  if (state.dateTo) target.end_date = state.dateTo;
}

/** Parse URL search params into validated filter state. Invalid values are ignored, never sent to APIs. */
export function parseAnalyticsSearchParams(searchParams: URLSearchParams): AnalyticsFilterState {
  const ignoredParams: string[] = [];
  const tabParam = searchParams.get("tab");
  const { tab, ignored: tabIgnored } = parseTab(tabParam);
  if (tabIgnored && tabParam) ignoredParams.push("tab");

  let dateFrom: string | null = null;
  const rawDateFrom = searchParams.get("date_from");
  if (rawDateFrom) {
    if (isValidIsoDate(rawDateFrom)) dateFrom = rawDateFrom;
    else ignoredParams.push("date_from");
  }

  let dateTo: string | null = null;
  const rawDateTo = searchParams.get("date_to");
  if (rawDateTo) {
    if (isValidIsoDate(rawDateTo)) dateTo = rawDateTo;
    else ignoredParams.push("date_to");
  }

  if (dateFrom && dateTo && dateFrom > dateTo) {
    ignoredParams.push("date_from", "date_to");
    dateFrom = null;
    dateTo = null;
  }

  let symbol: string | null = null;
  const rawSymbol = searchParams.get("symbol");
  if (rawSymbol) {
    if (isValidSymbol(rawSymbol)) symbol = rawSymbol.trim();
    else ignoredParams.push("symbol");
  }

  let timeframe: string | null = null;
  const rawTimeframe = searchParams.get("timeframe");
  if (rawTimeframe) {
    if (isValidTimeframe(rawTimeframe)) timeframe = rawTimeframe.trim();
    else ignoredParams.push("timeframe");
  }

  let portfolioSource: PortfolioSourceFilter | null = null;
  let journalSource: JournalTradeSource | null = null;
  const sourceParam = searchParams.get("source");
  if (sourceParam) {
    if (tab === "performance") {
      if (VALID_PP_SOURCES.has(sourceParam as PortfolioSourceFilter)) {
        portfolioSource = sourceParam as PortfolioSourceFilter;
      } else {
        ignoredParams.push("source");
      }
    } else if (JOURNAL_IDENTITY_TABS.has(tab)) {
      if (VALID_JOURNAL_SOURCES.has(sourceParam as JournalTradeSource)) {
        journalSource = sourceParam as JournalTradeSource;
      } else {
        ignoredParams.push("source");
      }
    } else {
      ignoredParams.push("source");
    }
  }

  let setupId: string | null = null;
  const rawSetupId = searchParams.get("setup_id");
  if (rawSetupId) {
    if (JOURNAL_IDENTITY_TABS.has(tab) && isValidUuid(rawSetupId)) {
      setupId = rawSetupId.trim().toLowerCase();
    } else {
      ignoredParams.push("setup_id");
    }
  }

  let userStrategyId: string | null = null;
  const rawStrategyId = searchParams.get("user_strategy_id");
  if (rawStrategyId) {
    if (JOURNAL_IDENTITY_TABS.has(tab) && isValidUuid(rawStrategyId)) {
      userStrategyId = rawStrategyId.trim().toLowerCase();
    } else {
      ignoredParams.push("user_strategy_id");
    }
  }

  let strategyVersionId: string | null = null;
  const rawStrategyVersionId = searchParams.get("strategy_version_id");
  if (rawStrategyVersionId) {
    if (
      (tab === BEHAVIOUR_TAB || tab === COMPARISON_TAB) &&
      isValidUuid(rawStrategyVersionId)
    ) {
      strategyVersionId = rawStrategyVersionId.trim().toLowerCase();
    } else {
      ignoredParams.push("strategy_version_id");
    }
  }

  let ruleCompliance: TradeRuleCompliance | null = null;
  const rawRuleCompliance = searchParams.get("rule_compliance");
  if (rawRuleCompliance) {
    if (tab === BEHAVIOUR_TAB && VALID_RULE_COMPLIANCE.has(rawRuleCompliance as TradeRuleCompliance)) {
      ruleCompliance = rawRuleCompliance as TradeRuleCompliance;
    } else {
      ignoredParams.push("rule_compliance");
    }
  }

  let marketRegime: MarketRegime | null = null;
  const rawMarketRegime = searchParams.get("market_regime");
  if (rawMarketRegime) {
    if (tab === COMPARISON_TAB && VALID_MARKET_REGIMES.has(rawMarketRegime as MarketRegime)) {
      marketRegime = rawMarketRegime as MarketRegime;
    } else {
      ignoredParams.push("market_regime");
    }
  }

  let groupBy: SetupGroupBy = "setup";
  const rawGroupBy = searchParams.get("group_by");
  if (rawGroupBy) {
    if (tab !== SETUPS_TAB) {
      ignoredParams.push("group_by");
    } else if (VALID_SETUP_GROUP_BY.has(rawGroupBy as SetupGroupBy)) {
      groupBy = rawGroupBy as SetupGroupBy;
    } else {
      ignoredParams.push("group_by");
    }
  }

  let bucketOffset = 0;
  const rawOffset = searchParams.get("offset");
  if (rawOffset) {
    if (tab !== SETUPS_TAB) {
      ignoredParams.push("offset");
    } else {
      const parsed = parseNonNegativeInt(rawOffset);
      if (parsed === null) ignoredParams.push("offset");
      else bucketOffset = parsed;
    }
  }

  let minSample = DEFAULT_VALIDATION_MIN_SAMPLE;
  const rawMinSample = searchParams.get("min_sample");
  if (rawMinSample) {
    if (tab !== VALIDATION_TAB) {
      ignoredParams.push("min_sample");
    } else {
      const parsed = parseMinSample(rawMinSample);
      if (parsed === null) ignoredParams.push("min_sample");
      else minSample = parsed;
    }
  }

  let dimension: ValidationDimension = DEFAULT_VALIDATION_DIMENSION;
  const rawDimension = searchParams.get("dimension");
  if (rawDimension) {
    if (tab !== VALIDATION_TAB) {
      ignoredParams.push("dimension");
    } else if (VALID_DIMENSIONS.has(rawDimension as ValidationDimension)) {
      dimension = rawDimension as ValidationDimension;
    } else {
      ignoredParams.push("dimension");
    }
  }

  for (const key of ALWAYS_UNSUPPORTED_PARAM_KEYS) {
    if (searchParams.get(key)) ignoredParams.push(key);
  }

  return {
    tab,
    dateFrom,
    dateTo,
    symbol,
    timeframe,
    portfolioSource,
    journalSource,
    setupId,
    userStrategyId,
    strategyVersionId,
    ruleCompliance,
    marketRegime,
    groupBy,
    bucketOffset,
    minSample,
    dimension,
    ignoredParams: [...new Set(ignoredParams)],
  };
}

/**
 * Shared overview/performance API params.
 * Journal setup_id is intentionally never applied here and never sent to portfolio.
 */
export function buildAnalyticsApiParams(state: AnalyticsFilterState): AnalyticsFilterParams {
  const journal: JournalStatsParams = { group_by: "overall" };
  const portfolio: PaperPortfolioParams = { timezone: "UTC" };
  const ruleComplianceJournal: JournalStatsParams = { group_by: "rule_compliance", limit: 20 };
  const comparison: JournalComparisonParams = {};
  const analyticsWindow: AnalyticsWindowParams = {};
  const learningWindow: LearningAnalyticsParams = {};
  const validation: ValidationApiParams = {
    dimension: state.dimension,
    min_sample: state.minSample,
  };
  const strategyQuality: StrategyQualityParams = {
    min_sample: state.minSample,
  };

  applySharedJournalStatsFilters(journal, state);

  if (state.dateFrom) portfolio.start_date = state.dateFrom;
  if (state.dateTo) portfolio.end_date = state.dateTo;
  if (state.symbol) portfolio.symbol = state.symbol;
  if (state.timeframe) portfolio.timeframe = state.timeframe;
  if (
    state.tab === "performance" &&
    state.portfolioSource &&
    state.portfolioSource !== "all"
  ) {
    portfolio.source = state.portfolioSource;
  }

  applyBehaviourJournalFilters(ruleComplianceJournal, state);
  applyComparisonFilters(comparison, state);
  applyDateWindow(analyticsWindow, state);
  applyDateWindow(learningWindow, state);
  applyDateWindow(validation, state);
  applyDateWindow(strategyQuality, state);

  return {
    journal,
    portfolio,
    ruleComplianceJournal,
    comparison,
    analyticsWindow,
    learningWindow,
    validation,
    strategyQuality,
    state,
  };
}

/** Setups-tab journal + evidence params. setup_id is journal UUID only. */
export function buildSetupAnalyticsApiParams(state: AnalyticsFilterState): SetupAnalyticsApiParams {
  const journal: JournalStatsParams = {
    group_by: state.groupBy,
    limit: SETUP_BUCKET_PAGE_SIZE,
    offset: state.bucketOffset,
  };
  applySharedJournalStatsFilters(journal, state);
  if (state.setupId) journal.setup_id = state.setupId;
  if (state.userStrategyId) journal.user_strategy_id = state.userStrategyId;
  if (state.journalSource) journal.source = state.journalSource;

  const evidence: SetupEvidenceParams = {};
  if (state.setupId) evidence.setup_id = state.setupId;
  if (state.userStrategyId) evidence.strategy_id = state.userStrategyId;

  return { journal, evidence, state };
}

export function buildFilterKey(params: AnalyticsFilterParams): string {
  return JSON.stringify({
    journal: params.journal,
    portfolio: params.portfolio,
    ruleComplianceJournal: params.ruleComplianceJournal,
    comparison: params.comparison,
    analyticsWindow: params.analyticsWindow,
    learningWindow: params.learningWindow,
    validation: params.validation,
    strategyQuality: params.strategyQuality,
  });
}

/** Request key for shared Overview sources: journal statistics + portfolio only. */
export function buildSharedAnalyticsFilterKey(params: AnalyticsFilterParams): string {
  return buildSharedJournalPortfolioKey(params.journal, params.portfolio);
}

export function buildSharedJournalPortfolioKey(
  journal: JournalStatsParams,
  portfolio: PaperPortfolioParams,
): string {
  return JSON.stringify({ journal, portfolio });
}

/** Request key for Behaviour rule-compliance journal statistics. */
export function buildRuleComplianceFilterKey(params: JournalStatsParams): string {
  return JSON.stringify(params);
}

/** Request key for proposal-flow discipline and risk-behaviour analytics window. */
export function buildAnalyticsWindowFilterKey(params: AnalyticsWindowParams): string {
  return JSON.stringify(params);
}

/** Request key for validation-session learning discipline. */
export function buildLearningWindowFilterKey(params: LearningAnalyticsParams): string {
  return JSON.stringify(params);
}

/** Request key for Validation-tab LA endpoints (dates + min_sample + optional dimension). */
export function buildValidationFilterKey(
  params: LearningAnalyticsParams & { dimension?: ValidationDimension },
): string {
  return JSON.stringify(params);
}

/** Request key for Validation-tab strategy-quality summary. */
export function buildStrategyQualityFilterKey(params: StrategyQualityParams): string {
  return JSON.stringify(params);
}

export function buildSetupFilterKey(params: SetupAnalyticsApiParams): string {
  return JSON.stringify({ journal: params.journal, evidence: params.evidence });
}

function summarizeDateRangeFromIso(
  dateFrom?: string | null,
  dateTo?: string | null,
): string {
  if (dateFrom || dateTo) {
    if (dateFrom && dateTo) return `dates ${dateFrom} → ${dateTo}`;
    if (dateFrom) return `from ${dateFrom}`;
    if (dateTo) return `until ${dateTo}`;
  }
  return "dates all time";
}

function summarizeJournalStatsParams(params: JournalStatsParams): string {
  const parts: string[] = [];
  parts.push(
    summarizeDateRangeFromIso(
      params.date_from?.slice(0, 10) ?? null,
      params.date_to?.slice(0, 10) ?? null,
    ),
  );
  if (params.symbol) parts.push(`Symbol ${params.symbol}`);
  if (params.timeframe) parts.push(`Timeframe ${params.timeframe}`);
  if (params.setup_id) parts.push(`Setup ${params.setup_id}`);
  if (params.user_strategy_id) parts.push(`Strategy ${params.user_strategy_id}`);
  if (params.strategy_version_id) {
    parts.push(`Strategy version ${params.strategy_version_id}`);
  }
  if (params.source) parts.push(`Journal source ${humanizeToken(params.source)}`);
  if (params.rule_compliance) {
    parts.push(`Compliance ${humanizeToken(params.rule_compliance)}`);
  }
  if (params.market_regime) parts.push(`Regime ${humanizeToken(params.market_regime)}`);
  if (params.group_by && params.group_by !== "overall") {
    parts.push(`Grouping ${humanizeToken(params.group_by)}`);
  }
  return parts.join(" · ");
}

function summarizeComparisonParams(params: JournalComparisonParams): string {
  const parts: string[] = [];
  parts.push(
    summarizeDateRangeFromIso(
      params.date_from?.slice(0, 10) ?? null,
      params.date_to?.slice(0, 10) ?? null,
    ),
  );
  if (params.symbol) parts.push(`Symbol ${params.symbol}`);
  if (params.timeframe) parts.push(`Timeframe ${params.timeframe}`);
  if (params.setup_id) parts.push(`Setup ${params.setup_id}`);
  if (params.strategy_id) parts.push(`Strategy ${params.strategy_id}`);
  if (params.strategy_version_id) {
    parts.push(`Strategy version ${params.strategy_version_id}`);
  }
  if (params.source) parts.push(`Journal source ${humanizeToken(params.source)}`);
  if (params.market_regime) parts.push(`Regime ${humanizeToken(params.market_regime)}`);
  return parts.join(" · ");
}

function summarizeDateWindowParams(
  params: AnalyticsWindowParams | LearningAnalyticsParams | StrategyQualityParams,
): string {
  return summarizeDateRangeFromIso(params.start_date ?? null, params.end_date ?? null);
}

function summarizeLearningAnalyticsParams(params: LearningAnalyticsParams): string {
  const parts: string[] = [summarizeDateWindowParams(params)];
  if (params.min_sample != null) parts.push(`Min sample ${params.min_sample}`);
  return parts.join(" · ");
}

/** Filter-bar summary for shared controls (not endpoint-specific provenance). */
export function formatAppliedFiltersSummary(state: AnalyticsFilterState): string {
  const parts: string[] = [];
  parts.push(summarizeDateRangeFromIso(state.dateFrom, state.dateTo));
  if (state.tab === VALIDATION_TAB) {
    parts.push(`Min sample ${state.minSample}`);
    parts.push(
      `Dimension ${VALIDATION_DIMENSION_LABELS[state.dimension] ?? humanizeToken(state.dimension)}`,
    );
    return parts.join(" · ");
  }
  if (state.symbol) parts.push(`Symbol ${state.symbol}`);
  if (state.timeframe) parts.push(`Timeframe ${state.timeframe}`);
  if (state.tab === "performance" && state.portfolioSource && state.portfolioSource !== "all") {
    parts.push(humanizeToken(state.portfolioSource));
  }
  if (state.tab === SETUPS_TAB) {
    parts.push(`Grouping ${humanizeToken(state.groupBy)}`);
    if (state.journalSource) parts.push(`Journal source ${humanizeToken(state.journalSource)}`);
    if (state.setupId) parts.push(`Setup ${state.setupId}`);
    if (state.userStrategyId) parts.push(`Strategy ${state.userStrategyId}`);
  }
  if (state.tab === BEHAVIOUR_TAB) {
    if (state.setupId) parts.push(`Setup ${state.setupId}`);
    if (state.userStrategyId) parts.push(`Strategy ${state.userStrategyId}`);
    if (state.journalSource) parts.push(`Journal source ${humanizeToken(state.journalSource)}`);
    if (state.ruleCompliance) parts.push(`Compliance ${humanizeToken(state.ruleCompliance)}`);
  }
  if (state.tab === COMPARISON_TAB) {
    if (state.setupId) parts.push(`Setup ${state.setupId}`);
    if (state.userStrategyId) parts.push(`Strategy ${state.userStrategyId}`);
    if (state.journalSource) parts.push(`Journal source ${humanizeToken(state.journalSource)}`);
    if (state.marketRegime) parts.push(`Regime ${humanizeToken(state.marketRegime)}`);
  }
  return parts.join(" · ");
}

/** Provenance for GET /journal/statistics payloads (includes user_strategy_id, never strategy_id). */
export function formatJournalStatsFiltersSummary(params: JournalStatsParams): string {
  return summarizeJournalStatsParams(params);
}

/** Provenance for GET /analytics/discipline and /analytics/risk-behavior (dates only). */
export function formatAnalyticsWindowFiltersSummary(params: AnalyticsWindowParams): string {
  return summarizeDateWindowParams(params);
}

/** Provenance for GET /learning-analytics/* — only dates (+ min_sample when sent). */
export function formatLearningAnalyticsFiltersSummary(params: LearningAnalyticsParams): string {
  return summarizeLearningAnalyticsParams(params);
}

/** Provenance for Validation setup-performance / ranking (dates + min_sample + dimension). */
export function formatValidationFiltersSummary(
  params: LearningAnalyticsParams & { dimension?: ValidationDimension },
): string {
  const parts: string[] = [summarizeLearningAnalyticsParams(params)];
  if (params.dimension) {
    parts.push(
      `Dimension ${VALIDATION_DIMENSION_LABELS[params.dimension] ?? humanizeToken(params.dimension)}`,
    );
  }
  return parts.join(" · ");
}

/** Provenance for GET /strategy-quality/summary. */
export function formatStrategyQualityFiltersSummary(params: StrategyQualityParams): string {
  return summarizeLearningAnalyticsParams(params);
}

/** Provenance for GET /journal/comparison (uses strategy_id query param). */
export function formatComparisonFiltersSummary(params: JournalComparisonParams): string {
  return summarizeComparisonParams(params);
}

/** Filters actually sent to GET /journal/setup-evidence (setup_id + strategy_id only). */
export function formatSetupEvidenceFiltersSummary(state: AnalyticsFilterState): string {
  const parts: string[] = [];
  if (state.setupId) parts.push(`Setup ${state.setupId}`);
  if (state.userStrategyId) parts.push(`Strategy ${state.userStrategyId}`);
  if (parts.length === 0) return "No setup or strategy filter";
  return parts.join(" · ");
}

/** Journal-statistics filters that do not apply to setup-evidence. */
export function listSetupEvidenceUnsupportedJournalFilters(
  state: AnalyticsFilterState,
): string[] {
  const unsupported: string[] = [];
  if (state.dateFrom || state.dateTo) unsupported.push("dates");
  if (state.symbol) unsupported.push("symbol");
  if (state.timeframe) unsupported.push("timeframe");
  if (state.journalSource) unsupported.push("journal source");
  if (state.groupBy !== "setup") unsupported.push("grouping");
  return unsupported;
}

export function formatSetupEvidenceLimitationNote(state: AnalyticsFilterState): string | null {
  const unsupported = listSetupEvidenceUnsupportedJournalFilters(state);
  if (unsupported.length === 0) return null;
  return `Active ${unsupported.join(", ")} filter(s) apply to journal statistics only — not setup evidence.`;
}

/** Tab-specific URL keys dropped on tab change (shared filters are preserved). */
export const TAB_SCOPED_PARAM_KEYS = [
  "source",
  "setup_id",
  "user_strategy_id",
  "strategy_version_id",
  "rule_compliance",
  "market_regime",
  "group_by",
  "offset",
  "portfolio_setup",
  "min_sample",
  "dimension",
] as const;
