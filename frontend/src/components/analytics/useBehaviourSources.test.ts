import { act, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderHook } from "@/test/private-query";
import { api } from "@/lib/api";

import type { AnalyticsFilterParams } from "./filterValidation";
import {
  buildAnalyticsWindowFilterKey,
  buildLearningWindowFilterKey,
  buildRuleComplianceFilterKey,
} from "./filterValidation";
import { useBehaviourSources } from "./useBehaviourSources";

vi.mock("@/lib/api", () => ({
  api: {
    journal: { statistics: vi.fn() },
    analytics: { discipline: vi.fn(), riskBehavior: vi.fn() },
    learningAnalytics: { discipline: vi.fn() },
  },
}));

const baseState = {
  tab: "behaviour" as const,
  dateFrom: "2026-01-01",
  dateTo: null,
  symbol: null,
  timeframe: null,
  portfolioSource: null,
  setupId: null,
  userStrategyId: null,
  strategyVersionId: null,
  journalSource: null,
  ruleCompliance: null,
  marketRegime: null,
  groupBy: "setup" as const,
  bucketOffset: 0,
  minSample: 5,
  dimension: "condition" as const,
  ignoredParams: [],
};

const params: AnalyticsFilterParams = {
  journal: { group_by: "overall" },
  portfolio: { timezone: "UTC" },
  ruleComplianceJournal: { group_by: "rule_compliance", limit: 20 },
  comparison: {},
  analyticsWindow: { start_date: "2026-01-01" },
  learningWindow: { start_date: "2026-01-01" },
  validation: { dimension: "condition", min_sample: 5 },
  strategyQuality: { min_sample: 5 },
  state: baseState,
};

const ruleComplianceResponse = {
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
  generated_at: "2026-07-25T12:00:00Z",
};

const proposalResponse = {
  score: 80,
  grade: "B",
  positive_behaviors: [],
  negative_behaviors: [],
  improvement_suggestions: [],
};

const learningResponse = {
  organization_id: "org",
  date_range: {},
  min_sample: 5,
  sample_size: 8,
  insufficient_data: false,
  discipline_score: 70,
  discipline_grade: "C",
  discipline_breakdown: {},
  entry_breakdown: {},
  issue_frequency: {},
  positive_behaviors: [],
  negative_behaviors: [],
  improvement_suggestions: [],
};

const riskResponse = {
  risk_blocks_count: 0,
  daily_loss_warnings: 1,
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

function mockAllResolved() {
  vi.mocked(api.journal.statistics).mockResolvedValue(ruleComplianceResponse as never);
  vi.mocked(api.analytics.discipline).mockResolvedValue(proposalResponse as never);
  vi.mocked(api.learningAnalytics.discipline).mockResolvedValue(learningResponse as never);
  vi.mocked(api.analytics.riskBehavior).mockResolvedValue(riskResponse as never);
}

describe("useBehaviourSources", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders completed widgets while another source is still pending", async () => {
    let resolveSlowRule: (value: unknown) => void = () => undefined;
    const slowRule = new Promise((resolve) => {
      resolveSlowRule = resolve;
    });

    vi.mocked(api.journal.statistics).mockReturnValue(slowRule as never);
    vi.mocked(api.analytics.discipline).mockResolvedValue(proposalResponse as never);
    vi.mocked(api.learningAnalytics.discipline).mockResolvedValue(learningResponse as never);
    vi.mocked(api.analytics.riskBehavior).mockResolvedValue(riskResponse as never);

    const { result } = renderHook(() => useBehaviourSources(params, true));

    await waitFor(() => expect(result.current.proposalDiscipline?.available).toBe(true));
    expect(result.current.ruleComplianceLoading).toBe(true);
    expect(result.current.proposalDisciplineLoading).toBe(false);
    expect(result.current.learningDiscipline?.available).toBe(true);
    expect(result.current.riskBehavior?.available).toBe(true);

    await act(async () => {
      resolveSlowRule(ruleComplianceResponse);
      await slowRule;
    });

    await waitFor(() => expect(result.current.ruleComplianceLoading).toBe(false));
    expect(result.current.ruleCompliance?.available).toBe(true);
  });

  it("keeps sibling widgets available when one source fails", async () => {
    vi.mocked(api.journal.statistics).mockRejectedValue(new Error("rule compliance down"));
    vi.mocked(api.analytics.discipline).mockResolvedValue(proposalResponse as never);
    vi.mocked(api.learningAnalytics.discipline).mockResolvedValue(learningResponse as never);
    vi.mocked(api.analytics.riskBehavior).mockResolvedValue(riskResponse as never);

    const { result } = renderHook(() => useBehaviourSources(params, true));

    await waitFor(() => expect(result.current.proposalDisciplineLoading).toBe(false));

    expect(result.current.ruleCompliance?.available).toBe(false);
    expect(result.current.proposalDiscipline?.available).toBe(true);
    expect(result.current.learningDiscipline?.available).toBe(true);
    expect(result.current.riskBehavior?.available).toBe(true);
  });

  it("tracks independent retry loading per source", async () => {
    mockAllResolved();
    const { result } = renderHook(() => useBehaviourSources(params, true));
    await waitFor(() => expect(result.current.proposalDisciplineLoading).toBe(false));

    let resolveRetry: (value: unknown) => void = () => undefined;
    const retryPromise = new Promise((resolve) => {
      resolveRetry = resolve;
    });
    vi.mocked(api.analytics.discipline).mockReturnValueOnce(retryPromise as never);

    act(() => {
      void result.current.reloadProposalDiscipline();
    });

    await waitFor(() => expect(result.current.proposalDisciplineRetryLoading).toBe(true));
    expect(result.current.ruleComplianceRetryLoading).toBe(false);
    expect(result.current.learningDisciplineRetryLoading).toBe(false);
    expect(result.current.riskBehaviorRetryLoading).toBe(false);
    expect(result.current.proposalDiscipline?.available).toBe(true);

    await act(async () => {
      resolveRetry(proposalResponse);
      await retryPromise;
    });

    await waitFor(() => expect(result.current.proposalDisciplineRetryLoading).toBe(false));
  });

  it("drops stale responses when a newer request resolves first", async () => {
    let resolveSlow: (value: unknown) => void = () => undefined;
    const slow = new Promise((resolve) => {
      resolveSlow = resolve;
    });

    vi.mocked(api.journal.statistics)
      .mockReturnValueOnce(slow as never)
      .mockResolvedValue({
        ...ruleComplianceResponse,
        overall: { ...ruleComplianceResponse.overall, trade_count: 99 },
      } as never);
    vi.mocked(api.analytics.discipline).mockResolvedValue(proposalResponse as never);
    vi.mocked(api.learningAnalytics.discipline).mockResolvedValue(learningResponse as never);
    vi.mocked(api.analytics.riskBehavior).mockResolvedValue(riskResponse as never);

    const btcParams: AnalyticsFilterParams = {
      ...params,
      ruleComplianceJournal: { ...params.ruleComplianceJournal, symbol: "BTCUSDT" },
      state: { ...params.state, symbol: "BTCUSDT" },
    };
    const ethParams: AnalyticsFilterParams = {
      ...params,
      ruleComplianceJournal: { ...params.ruleComplianceJournal, symbol: "ETHUSDT" },
      state: { ...params.state, symbol: "ETHUSDT" },
    };

    const { result, rerender } = renderHook(
      ({ current }) => useBehaviourSources(current, true),
      { initialProps: { current: btcParams } },
    );

    rerender({ current: ethParams });
    await waitFor(() => expect(result.current.ruleComplianceLoading).toBe(false));
    expect(result.current.ruleCompliance?.data?.overall.trade_count).toBe(99);

    await act(async () => {
      resolveSlow({
        ...ruleComplianceResponse,
        overall: { ...ruleComplianceResponse.overall, trade_count: 1 },
      });
      await slow;
    });

    expect(result.current.ruleCompliance?.data?.overall.trade_count).toBe(99);
    expect(result.current.ruleComplianceLoadedKey).toBe(
      buildRuleComplianceFilterKey(ethParams.ruleComplianceJournal),
    );
  });

  it("reloads only rule compliance when symbol changes without touching date-only sources", async () => {
    mockAllResolved();

    const { result, rerender } = renderHook(
      ({ current }) => useBehaviourSources(current, true),
      { initialProps: { current: params } },
    );
    await waitFor(() => expect(result.current.ruleComplianceLoading).toBe(false));

    const proposalBefore = result.current.proposalDiscipline;
    const learningBefore = result.current.learningDiscipline;
    const riskBefore = result.current.riskBehavior;
    const analyticsKeyBefore = result.current.analyticsWindowKey;

    const next: AnalyticsFilterParams = {
      ...params,
      ruleComplianceJournal: {
        ...params.ruleComplianceJournal,
        symbol: "ETHUSDT",
      },
      state: { ...params.state, symbol: "ETHUSDT" },
    };

    rerender({ current: next });

    expect(result.current.ruleComplianceLoading).toBe(true);
    expect(result.current.ruleCompliance).toBeNull();
    expect(result.current.proposalDisciplineLoading).toBe(false);
    expect(result.current.learningDisciplineLoading).toBe(false);
    expect(result.current.riskBehaviorLoading).toBe(false);
    expect(result.current.proposalDiscipline).toBe(proposalBefore);
    expect(result.current.learningDiscipline).toBe(learningBefore);
    expect(result.current.riskBehavior).toBe(riskBefore);
    expect(result.current.analyticsWindowKey).toBe(analyticsKeyBefore);

    await waitFor(() => expect(result.current.ruleComplianceLoading).toBe(false));
    expect(api.journal.statistics).toHaveBeenLastCalledWith(
      expect.objectContaining({ symbol: "ETHUSDT" }),
      { signal: expect.any(AbortSignal) },
    );
    expect(api.analytics.discipline).toHaveBeenCalledTimes(1);
    expect(api.learningAnalytics.discipline).toHaveBeenCalledTimes(1);
    expect(api.analytics.riskBehavior).toHaveBeenCalledTimes(1);
  });

  it("reloads date-filtered endpoints when dates change", async () => {
    mockAllResolved();

    const { result, rerender } = renderHook(
      ({ current }) => useBehaviourSources(current, true),
      { initialProps: { current: params } },
    );
    await waitFor(() => expect(result.current.ruleComplianceLoading).toBe(false));

    vi.clearAllMocks();
    mockAllResolved();

    const next: AnalyticsFilterParams = {
      ...params,
      ruleComplianceJournal: {
        ...params.ruleComplianceJournal,
        date_from: "2026-02-01T00:00:00Z",
      },
      analyticsWindow: { start_date: "2026-02-01" },
      learningWindow: { start_date: "2026-02-01" },
      validation: { dimension: "condition", min_sample: 5 },
      strategyQuality: { min_sample: 5 },
      state: { ...params.state, dateFrom: "2026-02-01" },
    };

    rerender({ current: next });
    await waitFor(() => expect(result.current.ruleComplianceLoading).toBe(false));

    expect(api.journal.statistics).toHaveBeenCalledWith(
      expect.objectContaining({ date_from: "2026-02-01T00:00:00Z" }),
      { signal: expect.any(AbortSignal) },
    );
    expect(api.analytics.discipline).toHaveBeenCalledWith(
      expect.objectContaining({ start_date: "2026-02-01" }),
      { signal: expect.any(AbortSignal) },
    );
    expect(api.learningAnalytics.discipline).toHaveBeenCalledWith(
      expect.objectContaining({ start_date: "2026-02-01" }),
      { signal: expect.any(AbortSignal) },
    );
    expect(api.analytics.riskBehavior).toHaveBeenCalledWith(
      expect.objectContaining({ start_date: "2026-02-01" }),
      { signal: expect.any(AbortSignal) },
    );
    expect(result.current.ruleComplianceLoadedKey).toBe(
      buildRuleComplianceFilterKey(next.ruleComplianceJournal),
    );
    expect(result.current.proposalDisciplineLoadedKey).toBe(
      buildAnalyticsWindowFilterKey(next.analyticsWindow),
    );
    expect(result.current.learningDisciplineLoadedKey).toBe(
      buildLearningWindowFilterKey(next.learningWindow),
    );
  });
});
