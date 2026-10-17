import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsFilterParams } from "./filterValidation";
import {
  FRESHNESS_UNAVAILABLE_MESSAGE,
  NO_SERVER_FRESHNESS_TIMESTAMP_NOTE,
} from "./sourceFreshness";
import { BehaviourCharts } from "./BehaviourCharts";
import type { SourceResult } from "@/components/workflows";
import type {
  DisciplineAnalyticsResponse,
  DisciplineScoreResult,
  JournalStatsResponse,
  RiskBehaviorAnalytics,
} from "@/lib/api/types";

function ok<T>(data: T): SourceResult<T> {
  return { data, available: true, error: null, fallbackUsed: false };
}

const apiParams: AnalyticsFilterParams = {
  journal: { group_by: "overall" },
  portfolio: { timezone: "UTC" },
  ruleComplianceJournal: {
    group_by: "rule_compliance",
    limit: 20,
    date_from: "2026-01-01T00:00:00Z",
    date_to: "2026-01-31T23:59:59Z",
    symbol: "BTCUSDT",
  },
  comparison: {},
  analyticsWindow: { start_date: "2026-01-01", end_date: "2026-01-31" },
  learningWindow: { start_date: "2026-01-01", end_date: "2026-01-31" },
  validation: { dimension: "condition", min_sample: 5 },
  strategyQuality: { min_sample: 5 },
  state: {
    tab: "behaviour",
    dateFrom: "2026-01-01",
    dateTo: "2026-01-31",
    symbol: "BTCUSDT",
    timeframe: null,
    portfolioSource: null,
    setupId: null,
    userStrategyId: null,
    strategyVersionId: null,
    journalSource: null,
    ruleCompliance: null,
    marketRegime: null,
    groupBy: "setup",
    bucketOffset: 0,
    minSample: 5,
    dimension: "condition" as const,
    ignoredParams: [],
  },
};

const ruleComplianceData: JournalStatsResponse = {
  group_by: "rule_compliance",
  filters: {},
  overall: {
    trade_count: 1,
    wins: 1,
    losses: 0,
    breakeven: 0,
    win_rate: 1,
    pnl_sample_count: 1,
    net_pnl_total: "1",
    gross_pnl_total: "1",
    expectancy: "1",
    average_winner: "1",
    average_loser: null,
    profit_factor: null,
    r_sample_count: 0,
    average_r: null,
    cost_sample_count: 0,
    fees_total: "0",
    funding_total: "0",
    slippage_total: "0",
    total_costs: "0",
    mfe_sample_count: 0,
    average_mfe_amount: null,
    mae_sample_count: 0,
    average_mae_amount: null,
    capture_sample_count: 0,
    available_profit_total: null,
    realized_on_available_total: null,
    average_realized_vs_available_pct: null,
    confidence: "insufficient",
    warnings: [],
  },
  buckets: [],
  total_buckets: 0,
  limit: 20,
  offset: 0,
  truncated: false,
  max_rows: 5000,
  generated_at: "2026-07-25T12:05:00Z",
};

const proposal: DisciplineScoreResult = {
  score: 82,
  grade: "B",
  positive_behaviors: [],
  negative_behaviors: [],
  improvement_suggestions: [],
};

const learning: DisciplineAnalyticsResponse = {
  organization_id: "org",
  user_id: null,
  date_range: { start: null, end: null },
  min_sample: 5,
  sample_size: 12,
  insufficient_data: false,
  discipline_score: 71,
  discipline_grade: "C",
  discipline_breakdown: {},
  entry_breakdown: {},
  issue_frequency: {},
  positive_behaviors: [],
  negative_behaviors: [],
  improvement_suggestions: [],
};

const risk: RiskBehaviorAnalytics = {
  risk_blocks_count: 0,
  daily_loss_warnings: 0,
  green_day_warnings: 0,
  overtrading_warnings: 0,
  revenge_trading_warnings: 0,
  proposals_rejected: 0,
  proposals_needs_more_analysis: 0,
  paper_orders_rejected: 0,
  approval_pending_count: 0,
  approval_approved_count: 0,
  journal_completion_rate: 0.5,
  triggered_rules: {},
};

vi.mock("./useBehaviourSources", () => ({
  useBehaviourSources: vi.fn(),
}));

vi.mock("./AnalyticsCharts", async () => {
  const { RuleComplianceChart } = await import("./RuleComplianceChart");
  return { RuleComplianceChart };
});

import { useBehaviourSources } from "./useBehaviourSources";

function mockBehaviourSources(
  overrides: Partial<ReturnType<typeof useBehaviourSources>> = {},
): ReturnType<typeof useBehaviourSources> {
  return {
    refreshFailures: [],
    ruleCompliance: null,
    ruleComplianceLoading: false,
    ruleComplianceRetryLoading: false,
    proposalDiscipline: null,
    proposalDisciplineLoading: false,
    proposalDisciplineRetryLoading: false,
    learningDiscipline: null,
    learningDisciplineLoading: false,
    learningDisciplineRetryLoading: false,
    riskBehavior: null,
    riskBehaviorLoading: false,
    riskBehaviorRetryLoading: false,
    loading: false,
    ruleComplianceKey: "rule-key",
    analyticsWindowKey: "analytics-key",
    learningWindowKey: "learning-key",
    ruleComplianceLoadedKey: "rule-key",
    proposalDisciplineLoadedKey: "analytics-key",
    learningDisciplineLoadedKey: "learning-key",
    riskBehaviorLoadedKey: "analytics-key",
    reload: vi.fn(),
    reloadRuleCompliance: vi.fn(),
    reloadProposalDiscipline: vi.fn(),
    reloadLearningDiscipline: vi.fn(),
    reloadRiskBehavior: vi.fn(),
    ...overrides,
  };
}

describe("BehaviourCharts integration", () => {
  afterEach(() => cleanup());

  it("treats future-skewed rule compliance as unavailable while other widgets remain", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-25T12:00:00Z"));
    vi.mocked(useBehaviourSources).mockReturnValue(
      mockBehaviourSources({
        ruleCompliance: ok(ruleComplianceData),
        proposalDiscipline: ok(proposal),
        learningDiscipline: ok(learning),
        riskBehavior: ok(risk),
      }),
    );

    render(<BehaviourCharts apiParams={apiParams} enabled />);

    expect(screen.getByTestId("rule-compliance-chart-error")).toHaveTextContent(
      FRESHNESS_UNAVAILABLE_MESSAGE,
    );
    expect(screen.getByTestId("discipline-proposal-score")).toHaveTextContent("82");
    expect(screen.getByTestId("risk-behaviour-counters")).toBeInTheDocument();
    expect(screen.queryByTestId("discipline-proposal-card-error")).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("does not mark discipline or risk widgets stale when rule compliance is stale", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-25T12:00:00Z"));
    vi.mocked(useBehaviourSources).mockReturnValue(
      mockBehaviourSources({
        ruleCompliance: ok({
          ...ruleComplianceData,
          generated_at: "2026-07-25T11:00:00Z",
        }),
        proposalDiscipline: ok(proposal),
        learningDiscipline: ok(learning),
        riskBehavior: ok(risk),
      }),
    );

    render(<BehaviourCharts apiParams={apiParams} enabled />);

    expect(screen.getByTestId("rule-compliance-chart")).toHaveTextContent(/stale for this view/i);
    expect(screen.queryByTestId("discipline-proposal-card")).not.toHaveTextContent(
      /stale for this view/i,
    );
    expect(screen.queryByTestId("risk-behaviour-counters")).not.toHaveTextContent(
      /stale for this view/i,
    );
    vi.useRealTimers();
  });

  it("shows date-only provenance on discipline cards, not symbol/timeframe from URL", () => {
    vi.mocked(useBehaviourSources).mockReturnValue(
      mockBehaviourSources({
        ruleCompliance: ok({ ...ruleComplianceData, generated_at: "2026-07-25T12:00:00Z" }),
        proposalDiscipline: ok(proposal),
        learningDiscipline: ok(learning),
        riskBehavior: ok(risk),
      }),
    );

    render(<BehaviourCharts apiParams={apiParams} enabled />);

    const proposalCard = screen.getByTestId("discipline-proposal-card");
    expect(proposalCard).toHaveTextContent("dates 2026-01-01 → 2026-01-31");
    expect(proposalCard).not.toHaveTextContent("symbol BTCUSDT");
    expect(proposalCard).toHaveTextContent(NO_SERVER_FRESHNESS_TIMESTAMP_NOTE);

    const ruleCard = screen.getByTestId("rule-compliance-chart");
    expect(ruleCard).toHaveTextContent("Symbol BTCUSDT");
    expect(ruleCard).toHaveTextContent("dates 2026-01-01 → 2026-01-31");
  });

  it("returns null when not enabled", () => {
    vi.mocked(useBehaviourSources).mockReturnValue(mockBehaviourSources());

    const { container } = render(<BehaviourCharts apiParams={apiParams} enabled={false} />);
    expect(container).toBeEmptyDOMElement();
  });
});
