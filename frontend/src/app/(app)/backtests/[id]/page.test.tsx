import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import BacktestRunDetailPage from "./page";
import type {
  BacktestJournalResult,
  BacktestRun,
  BacktestVerifyResult,
  JournalComparisonResponse,
  SetupEvidenceResponse,
} from "@/lib/api/types";
import { expectNoIdentifiers } from "@/test/no-identifiers";

const RUN_ID = "11111111-1111-1111-1111-111111111111";

const completedRun: BacktestRun = {
  id: RUN_ID,
  strategy_id: "22222222-2222-2222-2222-222222222222",
  strategy_version_id: "33333333-3333-3333-3333-333333333333",
  organization_id: "org-1",
  user_id: "user-1",
  status: "completed",
  assumptions: {
    symbol: "BTCUSDT",
    exchange: "binance",
    timeframe: "4h",
    initial_capital: "10000",
    fees_bps: "4",
    slippage_bps: "5",
    risk_per_trade_pct: "1",
  },
  result: {
    metrics: {
      trade_count: 3,
      win_rate: 0.67,
      profit_factor: 2.1,
      expectancy: "40",
      max_drawdown_pct: 5.5,
      average_win: "120",
      average_loss: "60",
      largest_win: "200",
      largest_loss: "90",
      consecutive_losses: 1,
      average_time_in_trade_bars: 4,
      total_fees: "12",
      total_slippage: "6",
      total_funding: "1",
      net_pnl: "120",
      return_pct: 1.2,
      ending_equity: "10120",
      equity_curve: [
        { timestamp: "2024-01-01T00:00:00Z", equity: "10000" },
        { timestamp: "2024-02-01T00:00:00Z", equity: "10120" },
      ],
      symbol: "BTCUSDT",
      timeframe: "4h",
    },
    recommendation: "backtested",
    split_metrics: [
      {
        split_label: "in_sample",
        split_index: 0,
        start_time: "2024-01-01T00:00:00Z",
        end_time: "2024-03-01T00:00:00Z",
        trade_count: 2,
        win_rate: 0.5,
        profit_factor: 1.8,
        expectancy: "30",
        net_pnl: "60",
        max_drawdown_pct: 4,
      },
      {
        split_label: "out_of_sample",
        split_index: 0,
        start_time: "2024-03-01T00:00:00Z",
        end_time: "2024-06-01T00:00:00Z",
        trade_count: 1,
        win_rate: 1,
        profit_factor: 3,
        expectancy: "60",
        net_pnl: "60",
        max_drawdown_pct: 2,
      },
    ],
    oos_metrics: {
      split_label: "out_of_sample",
      split_index: 0,
      start_time: "2024-03-01T00:00:00Z",
      end_time: "2024-06-01T00:00:00Z",
      trade_count: 1,
      win_rate: 1,
      profit_factor: 3,
      expectancy: "60",
      net_pnl: "60",
      max_drawdown_pct: 2,
    },
    dataset_summary: {
      dataset_hash: "abcdef1234567890abcdef1234567890",
      candle_count: 500,
      gap_count: 0,
      stale_count: 1,
      source_counts: { binance: 500 },
    },
    data_quality: "ok",
    limitations: ["Limited sample size"],
    note: "Historical simulation only — not a guarantee of future performance. Real trading remains disabled.",
    result_hash: "hash-stored",
    engine_version: "engine-v2",
  },
  config_hash: "config-hash",
  engine_version: "engine-v2",
  result_hash: "hash-stored",
  processed_bars: 500,
  total_bars: 500,
  created_at: "2026-07-24T10:00:00Z",
  updated_at: "2026-07-24T10:05:00Z",
};

const mockGet = vi.fn();
const mockListTrades = vi.fn();
const mockVerify = vi.fn();
const mockJournalTrades = vi.fn();
const mockComparison = vi.fn();
const mockSetupEvidence = vi.fn();
const mockResearchBacktestStatus = vi.fn();
const mockResearchPromote = vi.fn();

let currentRun: BacktestRun = completedRun;

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: RUN_ID }),
}));

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: () => ({
    data: currentRun,
    loading: false,
    error: null,
    reload: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  api: {
    backtests: {
      get: (...args: unknown[]) => mockGet(...args),
      listTrades: (...args: unknown[]) => mockListTrades(...args),
      cancel: vi.fn(),
      verify: (...args: unknown[]) => mockVerify(...args),
      journalTrades: (...args: unknown[]) => mockJournalTrades(...args),
    },
    journal: {
      comparison: (...args: unknown[]) => mockComparison(...args),
      setupEvidence: (...args: unknown[]) => mockSetupEvidence(...args),
    },
    researchValidation: {
      backtestStatus: (...args: unknown[]) => mockResearchBacktestStatus(...args),
      promote: (...args: unknown[]) => mockResearchPromote(...args),
    },
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  PROMOTE_RESEARCH_VALIDATION_CANDIDATE: "PROMOTE_RESEARCH_VALIDATION_CANDIDATE",
}));

const verifyResult: BacktestVerifyResult = {
  run_id: RUN_ID,
  result_hash_stored: "hash-stored",
  result_hash_recomputed: "hash-stored",
  match: true,
  dataset_ok: true,
  detail: "Deterministic replay matched.",
};

const dryRunResult: BacktestJournalResult = {
  run_id: RUN_ID,
  dry_run: true,
  committed: false,
  total_rows: 3,
  created_count: 0,
  duplicate_count: 1,
  invalid_count: 0,
  results: [
    { index: 0, outcome: "would_create", errors: [] },
    { index: 1, outcome: "would_create", errors: [] },
    { index: 2, outcome: "duplicate", errors: [] },
  ],
};

const comparisonResponse: JournalComparisonResponse = {
  filters: { strategy_id: completedRun.strategy_id },
  cohorts: [
    {
      cohort: "human",
      sample_count: 5,
      truncated: false,
      metrics: {
        trade_count: 5,
        wins: 3,
        losses: 2,
        breakeven: 0,
        win_rate: 0.6,
        pnl_sample_count: 5,
        net_pnl_total: "200",
        gross_pnl_total: null,
        expectancy: "40",
        average_winner: null,
        average_loser: null,
        profit_factor: 1.5,
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
        confidence: "low",
        warnings: [],
      },
    },
    {
      cohort: "paper_system",
      sample_count: 2,
      truncated: false,
      metrics: {
        trade_count: 2,
        wins: 1,
        losses: 1,
        breakeven: 0,
        win_rate: 0.5,
        pnl_sample_count: 2,
        net_pnl_total: "50",
        gross_pnl_total: null,
        expectancy: "25",
        average_winner: null,
        average_loser: null,
        profit_factor: 1.2,
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
        warnings: [{ code: "low_sample", message: "Only 2 trades." }],
      },
    },
    {
      cohort: "backtest",
      sample_count: 3,
      truncated: false,
      metrics: {
        trade_count: 3,
        wins: 2,
        losses: 1,
        breakeven: 0,
        win_rate: 2 / 3,
        pnl_sample_count: 3,
        net_pnl_total: "120",
        gross_pnl_total: null,
        expectancy: "40",
        average_winner: null,
        average_loser: null,
        profit_factor: 2.1,
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
        confidence: "low",
        warnings: [],
      },
    },
  ],
  scorecards: [],
  by_entry_method: [],
  by_source: [],
  rule_compliance: [],
  decision_quality: {
    timing_sample_count: 0,
    average_entry_timing_pct: null,
    early_exit_sample_count: 0,
    early_exit_count: null,
    early_exit_rate: null,
    missed_profit_sample_count: 0,
    average_missed_profit: null,
    average_capture_pct: null,
    warnings: [],
  },
  breakdowns: [],
  links: {
    journal_trades_path: "/journal",
    journal_statistics_path: "/journal/statistics",
    journal_comparison_path: "/journal/comparison",
    backtests_path: "/backtests",
    research_validation_path: "/research-validation",
    paper_validation_candidates_path: "/paper-validation/candidates",
  },
  confidence: "low",
  warnings: [],
  max_rows: 5000,
  generated_at: "2026-07-24T10:00:00Z",
  note: "Advisory only — never feeds execution or risk decisions.",
};

const evidenceResponse: SetupEvidenceResponse = {
  items: [
    {
      strategy_id: completedRun.strategy_id,
      strategy_version_id: completedRun.strategy_version_id!,
      strategy_name: "Test strategy",
      version: 2,
      tier: "tier2",
      measured: {
        oos_trade_count: 1,
        oos_profit_factor: 3,
        confirm_trade_count: 2,
        total_backtest_trades: 3,
      },
      thresholds: {
        tier1_oos_min_trades: 10,
        tier1_oos_min_profit_factor: 1.5,
        tier1_min_confirm_trades: 5,
        tier2_min_trades: 20,
        tier2_oos_min_trades: 5,
        tier2_oos_min_profit_factor: 1.2,
      },
      note: "Advisory only — never feeds execution or risk decisions.",
    },
  ],
  generated_at: "2026-07-24T10:00:00Z",
  note: "Advisory only — never feeds execution or risk decisions.",
};

describe("BacktestRunDetailPage", () => {
  beforeEach(() => {
    currentRun = completedRun;
    mockListTrades.mockResolvedValue({
      items: [
        {
          entry_time: "2024-01-02T00:00:00Z",
          exit_time: "2024-01-03T00:00:00Z",
          direction: "long",
          entry_price: "65000",
          exit_price: "66000",
          stop_loss: "64000",
          size: "0.1",
          fees: "4",
          slippage_cost: "2",
          gross_pnl: "100",
          net_pnl: "94",
          tp_hit_status: "tp1",
          exit_reason: "take_profit",
          mfe_amount: "120",
          mae_amount: "20",
          capture_pct: "78.3",
          funding_cost: "0.5",
          split_label: "in_sample",
        },
      ],
      total: 1,
      limit: 25,
      offset: 0,
    });
    mockVerify.mockResolvedValue(verifyResult);
    mockJournalTrades.mockResolvedValue(dryRunResult);
    mockComparison.mockResolvedValue(comparisonResponse);
    mockSetupEvidence.mockResolvedValue(evidenceResponse);
    mockResearchBacktestStatus.mockResolvedValue({
      evidence: {
        backtest_run_id: RUN_ID,
        strategy_id: completedRun.strategy_id,
        strategy_version_id: completedRun.strategy_version_id,
        strategy_name: "Test strategy",
        version: 2,
        status: "completed",
        evidence_tier: "tier2",
        sample_size: 1,
        oos_trade_count: 1,
        eligible_for_promotion: true,
        warnings: [],
      },
      links: {
        backtest_run_id: RUN_ID,
        strategy_id: completedRun.strategy_id,
        strategy_version_id: completedRun.strategy_version_id,
      },
      generated_at: "2026-07-24T10:00:00Z",
    });
    mockResearchPromote.mockResolvedValue({});
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders completed run metrics, splits, verify, and journal dry-run", async () => {
    render(<BacktestRunDetailPage />);

    expect(screen.getByTestId("backtest-run-detail")).toBeInTheDocument();
    expect(screen.getByTestId("backtest-status-badge")).toHaveTextContent("completed");
    expect(screen.getByTestId("backtest-metrics")).toBeInTheDocument();
    expect(screen.getByText("Split breakdown")).toBeInTheDocument();
    expect(screen.getByTestId("backtest-oos-summary")).toBeInTheDocument();
    expect(screen.getByTestId("backtest-equity-chart")).toBeInTheDocument();

    await waitFor(() => expect(mockListTrades).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /Re-run verify/i }));
    await waitFor(() => expect(mockVerify).toHaveBeenCalledWith(RUN_ID));
    expect(await screen.findByText(/Match: yes/)).toBeInTheDocument();
    expect(screen.getByText(/Dataset OK: yes/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Dry-run import/i }));
    await waitFor(() =>
      expect(mockJournalTrades).toHaveBeenCalledWith(RUN_ID, { dry_run: true }),
    );
    expect(await screen.findByTestId("journal-dry-run-summary")).toHaveTextContent("Would create: 2");
    expectNoIdentifiers();
  });

  it.each(["completed", "cancelled"] as const)("renders %s SFP research without trade returns", (status) => {
    currentRun = {
      ...completedRun,
      status,
      result: {
        metrics: null,
        trades: [],
        recommendation: "needs_review",
        replay: {
          mode: "sfp_research",
          strategy_version_id: "sfp-version-001",
          parameter_hash: "sfp-parameters",
          improvement_claim: false,
          samples: [{split_label: "out_of_sample", status: "insufficient_sample", candle_count: 6, setup_count: 1, candidate_count: 1, lifecycle_counts: {sweeps: 1, reclaims: 1}}],
          missing_evidence: ["funding"],
          stale_evidence: ["cvd"],
          research_buckets: [{split_label: "out_of_sample", symbol: "BTCUSDT", timeframe: "15m", direction: "long", level_type: "swing_low", strategy_version_id: "sfp-version-001", quality_bucket: "measured_6_of_8", regime: "efficiency_lt_1_3", setup_count: 1, confirmed_count: 1}],
        },
      },
    };
    render(<BacktestRunDetailPage />);
    expect(screen.getByTestId("sfp-replay-research")).toBeInTheDocument();
    expect(screen.getByText(/Trade returns are unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/insufficient sample/)).toBeInTheDocument();
    expect(screen.getByText("swing_low")).toBeInTheDocument();
    expect(screen.getByText("Missing evidence: funding")).toBeInTheDocument();
    expect(screen.getByText("Stale evidence: cvd")).toBeInTheDocument();
    expect(screen.queryByTestId("backtest-metrics")).not.toBeInTheDocument();
    expect(screen.queryByText("Net PnL")).not.toBeInTheDocument();
  });

  it("renders failed run state", () => {
    currentRun = {
      ...completedRun,
      status: "failed",
      result: null,
      error_message: "Dataset unavailable for requested range.",
    };
    render(<BacktestRunDetailPage />);
    expect(screen.getByTestId("backtest-status-badge")).toHaveTextContent("failed");
    expect(screen.getByText(/Dataset unavailable/)).toBeInTheDocument();
  });

  it("renders cancelled run without results", () => {
    currentRun = {
      ...completedRun,
      status: "cancelled",
      result: null,
    };
    render(<BacktestRunDetailPage />);
    expect(screen.getByTestId("backtest-status-badge")).toHaveTextContent("cancelled");
    expect(screen.getByText(/cancelled before producing results/)).toBeInTheDocument();
  });

  it("loads comparison and setup evidence when expanded", async () => {
    render(<BacktestRunDetailPage />);
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    await waitFor(() => expect(mockComparison).toHaveBeenCalled());
    expect(await screen.findByTestId("journal-comparison-cohorts")).toBeInTheDocument();
    expect(screen.getByText("Human")).toBeInTheDocument();
    expect(screen.getByText("Paper system")).toBeInTheDocument();
    expect(screen.getAllByText("Tier 2").length).toBeGreaterThan(0);
  });
});
