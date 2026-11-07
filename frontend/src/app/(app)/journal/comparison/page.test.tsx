import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import JournalComparisonPage from "@/app/(app)/journal/comparison/page";
import { api } from "@/lib/api";
import type {
  ComparisonScorecard,
  DecisionQualityMetrics,
  JournalComparisonCohortResult,
  JournalComparisonResponse,
  JournalTradeStatsMetrics,
} from "@/lib/api/types";

const metrics = (overrides: Partial<JournalTradeStatsMetrics>): JournalTradeStatsMetrics => ({
  trade_count: 0,
  wins: 0,
  losses: 0,
  breakeven: 0,
  win_rate: null,
  pnl_sample_count: 0,
  net_pnl_total: null,
  gross_pnl_total: null,
  expectancy: null,
  average_winner: null,
  average_loser: null,
  profit_factor: null,
  r_sample_count: 0,
  average_r: null,
  cost_sample_count: 0,
  fees_total: null,
  funding_total: null,
  slippage_total: null,
  total_costs: null,
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
  ...overrides,
});

const decisionQuality = (
  overrides: Partial<DecisionQualityMetrics> = {},
): DecisionQualityMetrics => ({
  timing_sample_count: 0,
  average_entry_timing_pct: null,
  early_exit_sample_count: 0,
  early_exit_count: null,
  early_exit_rate: null,
  missed_profit_sample_count: 0,
  average_missed_profit: null,
  average_capture_pct: null,
  warnings: [],
  ...overrides,
});

const cohort = (
  name: JournalComparisonCohortResult["cohort"],
  sampleCount: number,
  overrides: Partial<JournalComparisonCohortResult> = {},
): JournalComparisonCohortResult => ({
  cohort: name,
  sample_count: sampleCount,
  truncated: false,
  metrics: metrics({
    trade_count: sampleCount,
    confidence: sampleCount >= 20 ? "moderate" : "low",
  }),
  ...overrides,
});

const scorecard = (
  actor: ComparisonScorecard["actor"],
  sampleCount: number,
  overrides: Partial<ComparisonScorecard> = {},
): ComparisonScorecard => ({
  actor,
  sample_count: sampleCount,
  truncated: false,
  metrics: metrics({
    trade_count: sampleCount,
    wins: Math.floor(sampleCount / 2),
    losses: Math.ceil(sampleCount / 2),
    win_rate: sampleCount > 0 ? 0.5 : null,
    pnl_sample_count: sampleCount,
    net_pnl_total: "120",
    confidence: "low",
  }),
  decision_quality: decisionQuality({
    timing_sample_count: sampleCount,
    average_entry_timing_pct: 2.5,
    early_exit_sample_count: sampleCount,
    early_exit_count: 1,
    early_exit_rate: sampleCount > 0 ? 1 / sampleCount : null,
    warnings: [
      {
        code: "partial_timing_data",
        message: "Entry timing computable on 8 of 12 trades.",
      },
    ],
  }),
  ...overrides,
});

const sampleComparison: JournalComparisonResponse = {
  filters: { symbol: "BTCUSDT" },
  cohorts: [
    cohort("human", 8),
    cohort("paper_system", 4),
    cohort("backtest", 2),
  ],
  scorecards: [scorecard("human", 8), scorecard("system", 6)],
  by_entry_method: [
    {
      key: "manual",
      group_id: null,
      label: "manual",
      sample_count: 8,
      metrics: metrics({ trade_count: 8, confidence: "low" }),
    },
  ],
  by_source: [
    {
      key: "manual",
      group_id: null,
      label: "manual",
      sample_count: 8,
      metrics: metrics({ trade_count: 8, confidence: "low" }),
    },
  ],
  rule_compliance: [
    {
      key: "compliant",
      group_id: null,
      label: "compliant",
      sample_count: 6,
      metrics: metrics({ trade_count: 6, confidence: "low" }),
    },
  ],
  decision_quality: decisionQuality({
    timing_sample_count: 10,
    average_entry_timing_pct: 3.2,
    early_exit_sample_count: 10,
    early_exit_count: 2,
    early_exit_rate: 0.2,
    missed_profit_sample_count: 5,
    average_missed_profit: "45",
    average_capture_pct: 62.5,
    warnings: [
      {
        code: "low_sample",
        message: "Only 14 closed trade(s); treat decision-quality metrics as anecdotal.",
      },
    ],
  }),
  breakdowns: [
    {
      dimension: "setup",
      buckets: [
        {
          key: "setup-a",
          group_id: "11111111-1111-1111-1111-111111111111",
          label: "Sweep reversal",
          sample_count: 6,
          metrics: metrics({ trade_count: 6, confidence: "low" }),
        },
      ],
    },
  ],
  links: {
    journal_trades_path: "/journal",
    journal_statistics_path: "/journal/statistics?symbol=BTCUSDT",
    journal_comparison_path: "/journal/comparison?symbol=BTCUSDT",
    backtests_path: "/backtests",
    research_validation_path: "/research-validation",
    paper_validation_candidates_path: "/paper-validation/candidates",
  },
  confidence: "low",
  warnings: [
    {
      code: "low_sample",
      message: "Only 14 closed trade(s); treat decision-quality metrics as anecdotal.",
    },
  ],
  max_rows: 5000,
  generated_at: "2026-07-25T10:00:00Z",
  note: "Record-only human-vs-system performance and decision-quality comparison (AT-036).",
};

let asyncState: {
  data: JournalComparisonResponse | null;
  loading: boolean;
  error: string | null;
};

const mockReload = vi.fn();

const search = { params: new URLSearchParams() };
vi.mock("next/navigation", () => ({
  useSearchParams: () => search.params,
}));
vi.mock("@/hooks/useResolvedTimezone", () => ({
  useResolvedTimezone: () => ({ timezone: "Europe/Paris", source: "risk_settings", ready: true }),
}));

const loaders: { last: (() => Promise<unknown>) | null } = { last: null };
vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: (loader: () => Promise<unknown>) => (loaders.last = loader, {
    data: asyncState.data,
    loading: asyncState.loading,
    error: asyncState.error,
    reload: mockReload,
  }),
}));

describe("JournalComparisonPage day bounds", () => {
  afterEach(() => {
    cleanup();
    search.params = new URLSearchParams();
    vi.restoreAllMocks();
  });

  it("sends date bounds in the shared resolved timezone, not UTC", async () => {
    asyncState = { data: null, loading: true, error: null };
    search.params = new URLSearchParams("date_from=2026-07-01&date_to=2026-07-31");
    const comparison = vi.spyOn(api.journal, "comparison").mockResolvedValue(sampleComparison);
    render(<JournalComparisonPage />);
    expect(screen.getByTestId("cmp-date-timezone")).toHaveTextContent("Days in Europe/Paris (risk settings)");
    await loaders.last?.();
    expect(comparison).toHaveBeenCalledWith(
      expect.objectContaining({ date_from: "2026-06-30T22:00:00Z", date_to: "2026-07-31T21:59:59Z" }),
    );
  });
});

describe("JournalComparisonPage AT-036", () => {
  beforeEach(() => {
    asyncState = {
      data: sampleComparison,
      loading: false,
      error: null,
    };
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders loading state", () => {
    asyncState = { data: null, loading: true, error: null };
    render(<JournalComparisonPage />);
    expect(screen.getByText(/loading human vs system comparison/i)).toBeInTheDocument();
  });

  it("renders empty state when no closed trades", () => {
    asyncState = {
      data: {
        ...sampleComparison,
        cohorts: [
          cohort("human", 0),
          cohort("paper_system", 0),
          cohort("backtest", 0),
        ],
        scorecards: [scorecard("human", 0), scorecard("system", 0)],
      },
      loading: false,
      error: null,
    };
    render(<JournalComparisonPage />);
    expect(screen.getByText("No closed journal trades")).toBeInTheDocument();
    expect(
      screen.getByText(/close canonical journal trades across human, paper-system, or backtest/i),
    ).toBeInTheDocument();
  });

  it("renders error state with retry", () => {
    asyncState = { data: null, loading: false, error: "Comparison failed" };
    render(<JournalComparisonPage />);
    expect(screen.getByText("Comparison failed")).toBeInTheDocument();
  });

  it("renders cohort cards, scorecards, and decision quality", () => {
    render(<JournalComparisonPage />);
    expect(screen.getByRole("heading", { name: "Human vs System" })).toBeInTheDocument();
    expect(screen.getByText("Cohorts (AT-034)")).toBeInTheDocument();
    expect(screen.getByText("Actor scorecards")).toBeInTheDocument();
    expect(screen.getByText("Overall decision quality")).toBeInTheDocument();
    expect(screen.getAllByText("Human").length).toBeGreaterThan(0);
    expect(screen.getAllByText("System").length).toBeGreaterThan(0);
    expect(screen.getByText("Sweep reversal")).toBeInTheDocument();
    expect(screen.getAllByText(/Only 14 closed trade\(s\)/).length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/Entry timing computable on 8 of 12 trades/i).length,
    ).toBeGreaterThan(0);
  });

  it("renders related navigation links", () => {
    render(<JournalComparisonPage />);
    expect(screen.getByRole("link", { name: "Journal trades" })).toHaveAttribute("href", "/journal");
    expect(screen.getByRole("link", { name: "Journal statistics" })).toHaveAttribute(
      "href",
      "/journal/statistics?symbol=BTCUSDT",
    );
    expect(screen.getByRole("link", { name: "Research validation" })).toHaveAttribute(
      "href",
      "/research-validation",
    );
    expect(screen.getByRole("link", { name: "Paper validation queue" })).toHaveAttribute(
      "href",
      "/paper-validation/candidates",
    );
  });
});
