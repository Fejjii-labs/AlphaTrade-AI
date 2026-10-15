import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SourceResult } from "@/components/workflows";
import type { JournalStatsResponse, SetupEvidenceResponse } from "@/lib/api/types";

import { SetupBucketTable } from "./SetupBucketTable";
import { SetupEvidencePanel } from "./SetupEvidencePanel";
import { SetupExpectancyChart } from "./SetupExpectancyChart";
import { SetupWinRateChart } from "./SetupWinRateChart";
import { SetupsCharts } from "./SetupsCharts";
import { containsCurrencySymbol } from "./format";

function metrics(overrides: Partial<JournalStatsResponse["overall"]> = {}) {
  return {
    trade_count: 12,
    wins: 7,
    losses: 5,
    breakeven: 0,
    win_rate: 0.5833,
    pnl_sample_count: 12,
    net_pnl_total: "150.25",
    gross_pnl_total: "200.00",
    expectancy: "12.52",
    average_winner: "40.00",
    average_loser: "-20.00",
    profit_factor: 1.6,
    r_sample_count: 10,
    average_r: 1.2,
    cost_sample_count: 12,
    fees_total: "5.00",
    funding_total: "0",
    slippage_total: "0",
    total_costs: "5.00",
    mfe_sample_count: 0,
    average_mfe_amount: null,
    mae_sample_count: 0,
    average_mae_amount: null,
    capture_sample_count: 0,
    available_profit_total: null,
    realized_on_available_total: null,
    average_realized_vs_available_pct: null,
    confidence: "moderate" as const,
    warnings: [],
    ...overrides,
  };
}

const SETUP_A = "11111111-1111-1111-1111-111111111111";
const SETUP_B = "22222222-2222-2222-2222-222222222222";
const STRATEGY_A = "33333333-3333-3333-3333-333333333333";

const evidenceData: SetupEvidenceResponse = {
  items: [
    {
      strategy_id: STRATEGY_A,
      strategy_version_id: "44444444-4444-4444-4444-444444444444",
      strategy_name: "Breakout",
      version: 2,
      tier: "tier2",
      measured: {
        oos_trade_count: 12,
        oos_profit_factor: 1.4,
        oos_expectancy: "3.50",
        confirm_trade_count: 4,
        confirm_expectancy: null,
        total_backtest_trades: 40,
      },
      thresholds: {
        tier1_oos_min_trades: 30,
        tier1_oos_min_profit_factor: 1.2,
        tier1_min_confirm_trades: 10,
        tier2_min_trades: 10,
        tier2_oos_min_trades: 10,
        tier2_oos_min_profit_factor: 1.1,
      },
      note: "Evidence note",
    },
  ],
  generated_at: "2026-07-25T12:00:00Z",
  note: "Paper evidence only",
};

function ok<T>(data: T): SourceResult<T> {
  return { data, available: true, error: null, fallbackUsed: false };
}

function failed<T>(error = "down"): SourceResult<T> {
  return { data: null, available: false, error, fallbackUsed: false };
}

/** Highest win rate is last after confidence ranking (insufficient first in source order). */
const rankedJournal: JournalStatsResponse = {
  group_by: "setup_version",
  filters: {},
  overall: metrics({ trade_count: 20 }),
  buckets: [
    {
      key: SETUP_A,
      group_id: SETUP_A,
      label: "Alpha",
      metrics: metrics({
        trade_count: 12,
        win_rate: 0.4,
        expectancy: "2.00",
        confidence: "high",
      }),
    },
    {
      key: SETUP_B,
      group_id: SETUP_B,
      label: "Beta",
      metrics: metrics({
        trade_count: 3,
        win_rate: 0.9,
        expectancy: "25.00",
        confidence: "insufficient",
      }),
    },
    {
      key: "unassigned",
      group_id: null,
      label: "Unassigned",
      metrics: metrics({
        trade_count: 5,
        win_rate: 0.1,
        expectancy: null,
        confidence: "insufficient",
      }),
    },
  ],
  total_buckets: 3,
  limit: 20,
  offset: 0,
  truncated: true,
  max_rows: 5000,
  generated_at: "2026-07-25T12:00:00Z",
};

vi.mock("@/components/analytics/AnalyticsCharts", async () => {
  const win = await import("@/components/analytics/SetupWinRateChart");
  const expectancy = await import("@/components/analytics/SetupExpectancyChart");
  return {
    SetupWinRateChart: win.SetupWinRateChart,
    SetupExpectancyChart: expectancy.SetupExpectancyChart,
  };
});

describe("Setup analytics charts", () => {
  afterEach(() => cleanup());

  it("aria-label uses highest win rate from complete set, not confidence-ranked first row", () => {
    render(<SetupWinRateChart source={ok(rankedJournal)} groupBy="setup_version" />);
    const plot = screen.getByTestId("setup-win-rate-chart-plot");
    expect(plot).toHaveAttribute("aria-label", expect.stringContaining("Beta"));
    expect(plot.getAttribute("aria-label")).toMatch(/90\.0%/);
    expect(plot.getAttribute("aria-label")).not.toMatch(/^.*Top Alpha/);
    expect(screen.getByTestId(`setup-win-rate-row-${SETUP_B}`)).toHaveTextContent(/insufficient/i);
    expect(screen.getByTestId("setup-win-rate-row-unassigned")).toBeInTheDocument();
  });

  it("aria-label uses highest expectancy from all plottable rows including beyond mobile cap", () => {
    const many: JournalStatsResponse = {
      ...rankedJournal,
      buckets: [
        ...Array.from({ length: 10 }, (_, index) => {
          const id = `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
          return {
            key: id,
            group_id: id,
            label: `Low ${index}`,
            metrics: metrics({
              trade_count: 20 - index,
              win_rate: 0.5,
              expectancy: "1.00",
              confidence: "high" as const,
            }),
          };
        }),
        {
          key: SETUP_B,
          group_id: SETUP_B,
          label: "HiddenHigh",
          metrics: metrics({
            trade_count: 2,
            win_rate: 0.2,
            expectancy: "99.00",
            confidence: "insufficient",
          }),
        },
      ],
      total_buckets: 11,
    };
    render(<SetupExpectancyChart source={ok(many)} groupBy="setup_version" />);
    const plot = screen.getByTestId("setup-expectancy-chart-plot");
    expect(plot.getAttribute("aria-label")).toContain("HiddenHigh");
    expect(plot.getAttribute("aria-label")).toContain("+99.00");
  });

  it("renders null expectancy as No P&L data and never fabricates zero bars on error", () => {
    render(<SetupExpectancyChart source={ok(rankedJournal)} groupBy="setup_version" />);
    expect(screen.getByTestId("setup-expectancy-row-unassigned")).toHaveTextContent("No P&L data");
    const monetary = screen.getByTestId(`setup-expectancy-row-${SETUP_A}`).textContent ?? "";
    expect(containsCurrencySymbol(monetary)).toBe(false);

    cleanup();
    render(<SetupExpectancyChart source={failed("journal down")} groupBy="setup" />);
    expect(screen.getByTestId("setup-expectancy-chart-error")).toBeInTheDocument();
    expect(screen.queryByTestId("setup-expectancy-chart-plot")).not.toBeInTheDocument();
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  });

  it("setup_version links use group_id as setup_id", () => {
    render(
      <SetupBucketTable
        source={ok(rankedJournal)}
        groupBy="setup_version"
        bucketOffset={0}
        onPageChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId(`setup-bucket-stats-link-${SETUP_A}`)).toHaveAttribute(
      "href",
      `/journal/statistics?setup_id=${SETUP_A}`,
    );
    expect(screen.getByTestId(`setup-bucket-filter-link-${SETUP_A}`)).toHaveAttribute(
      "href",
      expect.stringContaining(`setup_id=${SETUP_A}`),
    );
  });

  it("setup name grouping does not emit setup_id links", () => {
    const byName: JournalStatsResponse = {
      ...rankedJournal,
      group_by: "setup",
      buckets: [
        {
          key: "Breakout",
          group_id: null,
          label: "Breakout",
          metrics: metrics({ trade_count: 8, confidence: "high" }),
        },
        {
          key: "unassigned",
          group_id: null,
          label: "Unassigned",
          metrics: metrics({ trade_count: 1, expectancy: null, confidence: "insufficient" }),
        },
      ],
    };
    render(
      <SetupBucketTable
        source={ok(byName)}
        groupBy="setup"
        bucketOffset={0}
        onPageChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("setup-bucket-stats-link-Breakout")).toHaveAttribute(
      "href",
      "/journal/statistics",
    );
    expect(screen.getByTestId("setup-bucket-filter-link-Breakout").getAttribute("href")).not.toContain(
      "setup_id=",
    );
    expect(screen.getByTestId("setup-bucket-exact-note-Breakout")).toHaveTextContent(
      /Setup version grouping/i,
    );
    expect(screen.getByTestId("setup-bucket-row-unassigned")).toBeInTheDocument();
  });

  it("strategy grouping links use user_strategy_id from group_id", () => {
    const byStrategy: JournalStatsResponse = {
      ...rankedJournal,
      group_by: "strategy",
      buckets: [
        {
          key: STRATEGY_A,
          group_id: STRATEGY_A,
          label: "Breakout",
          metrics: metrics({ trade_count: 8, confidence: "high" }),
        },
      ],
    };
    render(
      <SetupBucketTable
        source={ok(byStrategy)}
        groupBy="strategy"
        bucketOffset={0}
        onPageChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId(`setup-bucket-stats-link-${STRATEGY_A}`)).toHaveAttribute(
      "href",
      `/journal/statistics?user_strategy_id=${STRATEGY_A}`,
    );
    const filterHref = screen.getByTestId(`setup-bucket-filter-link-${STRATEGY_A}`).getAttribute("href");
    expect(filterHref).toContain(`user_strategy_id=${STRATEGY_A}`);
    expect(filterHref).not.toContain("setup_id=");
  });

  it("keeps colliding labels distinct by key/group_id", () => {
    const colliding: JournalStatsResponse = {
      ...rankedJournal,
      buckets: [
        {
          key: SETUP_A,
          group_id: SETUP_A,
          label: "Breakout",
          metrics: metrics({ trade_count: 8, confidence: "high" }),
        },
        {
          key: SETUP_B,
          group_id: SETUP_B,
          label: "Breakout",
          metrics: metrics({ trade_count: 6, confidence: "moderate" }),
        },
      ],
    };
    render(
      <SetupBucketTable
        source={ok(colliding)}
        groupBy="setup_version"
        bucketOffset={0}
        onPageChange={vi.fn()}
      />,
    );
    const table = screen.getByTestId("setup-bucket-data-table");
    // key and group_id columns both show the UUID for setup_version rows
    expect(within(table).getAllByText(SETUP_A).length).toBeGreaterThanOrEqual(1);
    expect(within(table).getAllByText(SETUP_B).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId(`setup-bucket-row-${SETUP_A}`)).toBeInTheDocument();
    expect(screen.getByTestId(`setup-bucket-row-${SETUP_B}`)).toBeInTheDocument();
  });

  it("isolates evidence from journal: journal fail + evidence success still renders evidence", () => {
    render(
      <SetupsCharts
        source={failed("journal down")}
        evidence={ok(evidenceData)}
        groupBy="setup_version"
        bucketOffset={0}
        onGroupByChange={vi.fn()}
        onPageChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("setup-win-rate-chart-error")).toBeInTheDocument();
    expect(screen.getByTestId("setup-bucket-table-error")).toBeInTheDocument();
    expect(screen.getByTestId("setup-evidence-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("setup-evidence-panel-error")).not.toBeInTheDocument();
    expect(screen.getByTestId(`setup-evidence-item-${STRATEGY_A}`)).toBeInTheDocument();
  });

  it("isolates evidence from journal: journal success + evidence failure keeps charts", () => {
    render(
      <SetupsCharts
        source={ok(rankedJournal)}
        evidence={failed("evidence down")}
        groupBy="setup_version"
        bucketOffset={0}
        onGroupByChange={vi.fn()}
        onPageChange={vi.fn()}
        onRetryEvidence={vi.fn()}
      />,
    );
    expect(screen.getByTestId("setup-win-rate-chart-plot")).toBeInTheDocument();
    expect(screen.getByTestId("setup-evidence-panel-error")).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId("setup-evidence-panel-error")).getByRole("button"));
  });

  it("shows both source failures when journal and evidence fail", () => {
    render(
      <SetupsCharts
        source={failed("journal down")}
        evidence={failed("evidence down")}
        groupBy="setup"
        bucketOffset={0}
        onGroupByChange={vi.fn()}
        onPageChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId("setup-win-rate-chart-error")).toBeInTheDocument();
    expect(screen.getByTestId("setup-evidence-panel-error")).toBeInTheDocument();
  });

  it("shows loading skeleton without empty state for evidence", () => {
    render(<SetupEvidencePanel evidence={null} loading />);
    expect(screen.getByTestId("loading-state")).toBeInTheDocument();
    expect(screen.queryByText(/No setup-evidence rows/i)).not.toBeInTheDocument();
  });

  it("shows truncated pager disclosure", () => {
    const onPageChange = vi.fn();
    const paged: JournalStatsResponse = {
      ...rankedJournal,
      total_buckets: 45,
      limit: 20,
      offset: 20,
    };
    render(
      <SetupBucketTable
        source={ok(paged)}
        groupBy="setup_version"
        bucketOffset={20}
        onPageChange={onPageChange}
      />,
    );
    expect(screen.getByTestId("setup-bucket-pager")).toHaveTextContent(/Showing 21–40 of 45/);
    fireEvent.click(screen.getByTestId("setup-bucket-prev"));
    expect(onPageChange).toHaveBeenCalledWith(0);
  });

  it("uses strategy wording when group_by is strategy", () => {
    const emptyJournal: JournalStatsResponse = {
      ...rankedJournal,
      group_by: "strategy",
      overall: metrics({ trade_count: 0 }),
      buckets: [],
      total_buckets: 0,
    };
    render(<SetupWinRateChart source={ok(emptyJournal)} groupBy="strategy" />);
    expect(screen.getByText(/Which strategies win most often/i)).toBeInTheDocument();
    expect(screen.getByTestId("setup-win-rate-chart-source")).toHaveTextContent(/strategy win rate/i);
    expect(screen.getByText(/No closed trades have a recorded strategy/i)).toBeInTheDocument();

    cleanup();
    const strategyJournal: JournalStatsResponse = {
      ...rankedJournal,
      group_by: "strategy",
    };
    render(<SetupWinRateChart source={ok(strategyJournal)} groupBy="strategy" />);
    expect(screen.getByTestId("setup-win-rate-chart-plot")).toHaveAttribute(
      "aria-label",
      expect.stringContaining("Strategy win-rate chart"),
    );
  });

  it("renders Journal statistics link in Setups empty states", () => {
    const emptyJournal: JournalStatsResponse = {
      ...rankedJournal,
      overall: metrics({ trade_count: 0 }),
      buckets: [],
      total_buckets: 0,
    };
    render(<SetupWinRateChart source={ok(emptyJournal)} groupBy="setup" />);
    expect(screen.getByTestId("setup-win-rate-empty-journal-link")).toHaveAttribute(
      "href",
      "/journal/statistics",
    );
  });

  it("setup evidence panel shows only evidence API filters, not journal statistics filters", () => {
    render(
      <SetupEvidencePanel
        evidence={ok(evidenceData)}
        evidenceFiltersSummary={`Setup ${SETUP_A} · Strategy ${STRATEGY_A}`}
        evidenceLimitationNote="Active dates, symbol filter(s) apply to journal statistics only — not setup evidence."
      />,
    );
    const panel = screen.getByTestId("setup-evidence-panel");
    expect(panel).toHaveTextContent(`Setup ${SETUP_A}`);
    expect(panel).toHaveTextContent(`Strategy ${STRATEGY_A}`);
    expect(panel).not.toHaveTextContent("dates all time");
    expect(panel).not.toHaveTextContent("symbol BTCUSDT");
    expect(panel).not.toHaveTextContent("group setup");
    expect(screen.getByTestId("limitations-state")).toHaveTextContent(
      /journal statistics only — not setup evidence/i,
    );
  });

  it("uses compact-view toggle wording instead of metric-ranked top label", () => {
    const many: JournalStatsResponse = {
      ...rankedJournal,
      buckets: Array.from({ length: 10 }, (_, index) => {
        const id = `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
        return {
          key: id,
          group_id: id,
          label: `Row ${index}`,
          metrics: metrics({
            trade_count: 20 - index,
            win_rate: 0.5,
            expectancy: "1.00",
            confidence: "high" as const,
          }),
        };
      }),
      total_buckets: 10,
    };
    render(<SetupWinRateChart source={ok(many)} groupBy="setup_version" />);
    fireEvent.click(screen.getByTestId("setup-win-rate-show-all"));
    expect(screen.getByTestId("setup-win-rate-show-all")).toHaveTextContent(/Show compact view/i);
    expect(screen.getByTestId("setup-win-rate-show-all")).not.toHaveTextContent(/Show top/i);

    cleanup();
    render(<SetupExpectancyChart source={ok(many)} groupBy="setup_version" />);
    fireEvent.click(screen.getByTestId("setup-expectancy-show-all"));
    expect(screen.getByTestId("setup-expectancy-show-all")).toHaveTextContent(/Show compact view/i);
    expect(screen.getByTestId("setup-expectancy-show-all")).not.toHaveTextContent(/Show top/i);
  });
  it("offers first-page recovery for an empty later page outside hidden table children", () => {
    const onPageChange = vi.fn();
    render(<SetupBucketTable source={ok({ ...rankedJournal, buckets: [], total_buckets: 0, offset: 40 })}
      groupBy="setup" bucketOffset={40} onPageChange={onPageChange} />);
    expect(screen.queryByTestId("setup-bucket-data-table")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "First page" }));
    expect(onPageChange).toHaveBeenCalledWith(0);
  });
  it("keeps unavailable evidence distinct from an empty later page", () => {
    render(<SetupBucketTable source={failed("Source unavailable")} groupBy="setup" bucketOffset={40} onPageChange={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "First page" })).not.toBeInTheDocument();
    expect(screen.getByText("Source unavailable")).toBeInTheDocument();
  });

});
