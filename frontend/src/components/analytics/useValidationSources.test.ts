import { act, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderHook } from "@/test/private-query";
import { api } from "@/lib/api";

import type { AnalyticsFilterParams } from "./filterValidation";
import { useValidationSources } from "./useValidationSources";

vi.mock("@/lib/api", () => ({
  api: {
    learningAnalytics: {
      summary: vi.fn(),
      setupPerformance: vi.fn(),
      setupRanking: vi.fn(),
    },
    strategyQuality: {
      summary: vi.fn(),
    },
  },
}));

const baseState = {
  tab: "validation" as const,
  dateFrom: "2026-01-01",
  dateTo: "2026-01-31",
  symbol: "BTCUSDT",
  timeframe: "1h",
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
  journal: { group_by: "overall", symbol: "BTCUSDT", timeframe: "1h" },
  portfolio: { timezone: "UTC", symbol: "BTCUSDT", timeframe: "1h" },
  ruleComplianceJournal: { group_by: "rule_compliance", limit: 20 },
  comparison: {},
  analyticsWindow: { start_date: "2026-01-01", end_date: "2026-01-31" },
  learningWindow: { start_date: "2026-01-01", end_date: "2026-01-31" },
  validation: {
    start_date: "2026-01-01",
    end_date: "2026-01-31",
    min_sample: 5,
    dimension: "condition",
  },
  strategyQuality: {
    start_date: "2026-01-01",
    end_date: "2026-01-31",
    min_sample: 5,
  },
  state: baseState,
};

const summaryResponse = {
  organization_id: "org",
  date_range: {},
  min_sample: 5,
  funnel: {
    alerts: 0,
    drafts: 0,
    candidates: 0,
    run_plans: 0,
    run_sessions: 1,
    completed_sessions: 1,
    cancelled_sessions: 0,
    results: 1,
  },
  total_sessions: 1,
  completed_sessions: 1,
  cancelled_sessions: 0,
  results_count: 1,
  outcome_distribution: [{ outcome: "success", count: 1, rate: 1 }],
  rates: {},
  observations: { total_observations: 0, by_kind: {} },
  lessons_count: 0,
};

describe("useValidationSources", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("loads four sources independently and omits unsupported filters", async () => {
    vi.mocked(api.learningAnalytics.summary).mockResolvedValue(summaryResponse as never);
    vi.mocked(api.learningAnalytics.setupPerformance).mockResolvedValue({
      ...summaryResponse,
      dimension: "condition",
      groups: [],
    } as never);
    vi.mocked(api.learningAnalytics.setupRanking).mockResolvedValue({
      ...summaryResponse,
      dimension: "condition",
      note: "note",
      ranked: [],
    } as never);
    vi.mocked(api.strategyQuality.summary).mockResolvedValue({
      organization_id: "org",
      date_range: {},
      min_sample: 5,
      note: "note",
      total_detectors: 0,
      detectors_with_data: 0,
      total_results: 0,
      by_trust_tier: [],
      by_verdict: [],
      ranked: [],
      warnings: [],
    } as never);

    const { result } = renderHook(() => useValidationSources(params, true));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(api.learningAnalytics.summary).toHaveBeenCalledWith({
      start_date: "2026-01-01",
      end_date: "2026-01-31",
      min_sample: 5,
    }, { signal: expect.any(AbortSignal) });
    expect(api.learningAnalytics.setupPerformance).toHaveBeenCalledWith({
      start_date: "2026-01-01",
      end_date: "2026-01-31",
      min_sample: 5,
      dimension: "condition",
    }, { signal: expect.any(AbortSignal) });
    const summaryCall = vi.mocked(api.learningAnalytics.summary).mock.calls[0]?.[0] ?? {};
    expect(summaryCall).not.toHaveProperty("symbol");
    expect(summaryCall).not.toHaveProperty("timeframe");
    expect(summaryCall).not.toHaveProperty("setup_id");
    expect(summaryCall).not.toHaveProperty("source");
    expect(summaryCall).not.toHaveProperty("rule_compliance");
    expect(result.current.summary?.available).toBe(true);
  });

  it("keeps sibling sources when one fails and supports independent retry", async () => {
    vi.mocked(api.learningAnalytics.summary).mockRejectedValue(new Error("summary down"));
    vi.mocked(api.learningAnalytics.setupPerformance).mockResolvedValue({
      organization_id: "org",
      date_range: {},
      min_sample: 5,
      dimension: "condition",
      groups: [],
    } as never);
    vi.mocked(api.learningAnalytics.setupRanking).mockResolvedValue({
      organization_id: "org",
      date_range: {},
      min_sample: 5,
      dimension: "condition",
      note: "note",
      ranked: [],
    } as never);
    vi.mocked(api.strategyQuality.summary).mockResolvedValue({
      organization_id: "org",
      date_range: {},
      min_sample: 5,
      note: "note",
      total_detectors: 1,
      detectors_with_data: 0,
      total_results: 0,
      by_trust_tier: [],
      by_verdict: [],
      ranked: [],
      warnings: [],
    } as never);

    const { result } = renderHook(() => useValidationSources(params, true));
    await waitFor(() => expect(result.current.setupPerformance?.available).toBe(true));
    expect(result.current.summary?.available).toBe(false);
    expect(result.current.strategyQuality?.available).toBe(true);

    vi.mocked(api.learningAnalytics.summary).mockResolvedValue(summaryResponse as never);
    await act(async () => {
      await result.current.reloadSummary();
    });
    await waitFor(() => expect(result.current.summary?.available).toBe(true));
  });

  it("ignores stale responses when request keys change", async () => {
    let resolveFirst: (value: unknown) => void = () => undefined;
    vi.mocked(api.learningAnalytics.summary).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }) as never,
    );
    vi.mocked(api.learningAnalytics.setupPerformance).mockResolvedValue({
      organization_id: "org",
      date_range: {},
      min_sample: 5,
      dimension: "condition",
      groups: [],
    } as never);
    vi.mocked(api.learningAnalytics.setupRanking).mockResolvedValue({
      organization_id: "org",
      date_range: {},
      min_sample: 5,
      dimension: "condition",
      note: "note",
      ranked: [],
    } as never);
    vi.mocked(api.strategyQuality.summary).mockResolvedValue({
      organization_id: "org",
      date_range: {},
      min_sample: 5,
      note: "note",
      total_detectors: 0,
      detectors_with_data: 0,
      total_results: 0,
      by_trust_tier: [],
      by_verdict: [],
      ranked: [],
      warnings: [],
    } as never);

    const { result, rerender } = renderHook(
      ({ next }: { next: AnalyticsFilterParams }) => useValidationSources(next, true),
      { initialProps: { next: params } },
    );

    const newer = {
      ...params,
      validation: { ...params.validation, min_sample: 10 },
      strategyQuality: { ...params.strategyQuality, min_sample: 10 },
      state: { ...params.state, minSample: 10 },
    };
    vi.mocked(api.learningAnalytics.summary).mockResolvedValue({
      ...summaryResponse,
      min_sample: 10,
      results_count: 99,
    } as never);
    rerender({ next: newer });

    await waitFor(() => expect(result.current.summary?.data?.results_count).toBe(99));
    resolveFirst({ ...summaryResponse, results_count: 1 });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.summary?.data?.results_count).toBe(99);
  });

  it("does not fetch when disabled", async () => {
    renderHook(() => useValidationSources(params, false));
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.learningAnalytics.summary).not.toHaveBeenCalled();
  });
});
