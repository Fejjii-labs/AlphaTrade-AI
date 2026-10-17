import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PrivateQueryFixture } from "@/test/private-query";
import AnalyticsPage from "@/app/(app)/analytics/page";
import type {
  JournalStatsResponse,
  PaperPortfolioResponse,
  SetupEvidenceResponse,
} from "@/lib/api/types";
import type { SourceResult } from "@/components/workflows";
import type { AnalyticsFilterState } from "@/components/analytics/useAnalyticsFilters";

const {
  performanceChartsMounted,
  setupsChartsMounted,
  behaviourChartsMounted,
  validationChartsMounted,
  comparisonChartsMounted,
} = vi.hoisted(() => ({
  performanceChartsMounted: vi.fn(),
  setupsChartsMounted: vi.fn(),
  behaviourChartsMounted: vi.fn(),
  validationChartsMounted: vi.fn(),
  comparisonChartsMounted: vi.fn(),
}));

const mockReload = vi.fn();
const mockSetupReload = vi.fn();
const mockReloadStrategies = vi.fn();
const mockSetTab = vi.fn();
const mockApplyDraft = vi.fn();
const mockApplyPreset = vi.fn();
const mockClear = vi.fn();
const mockCleanup = vi.fn();
const mockSetGroupBy = vi.fn();
const mockSetBucketOffset = vi.fn();
const mockSetDimension = vi.fn();

const journalData: JournalStatsResponse = {
  group_by: "overall",
  filters: {},
  overall: {
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
    confidence: "moderate",
    warnings: [],
  },
  buckets: [],
  total_buckets: 0,
  limit: 50,
  offset: 0,
  truncated: false,
  max_rows: 5000,
  generated_at: "2026-07-25T12:00:00Z",
};

const setupJournalData: JournalStatsResponse = {
  ...journalData,
  group_by: "setup_version",
  buckets: [
    {
      key: "11111111-1111-1111-1111-111111111111",
      group_id: "11111111-1111-1111-1111-111111111111",
      label: "Breakout",
      metrics: {
        ...journalData.overall,
        trade_count: 8,
        win_rate: 0.75,
        expectancy: "15.00",
        confidence: "high",
      },
    },
    {
      key: "unassigned",
      group_id: null,
      label: "Unassigned",
      metrics: {
        ...journalData.overall,
        trade_count: 4,
        win_rate: 0.25,
        expectancy: null,
        confidence: "insufficient",
      },
    },
  ],
  total_buckets: 2,
  limit: 20,
  offset: 0,
};

const evidenceData: SetupEvidenceResponse = {
  items: [],
  generated_at: "2026-07-25T12:00:00Z",
  note: "none",
};

const portfolioData: PaperPortfolioResponse = {
  safety: {
    execution_mode: "paper",
    paper_only: true,
    real_trading_enabled: false,
    disclaimer: "Paper only",
  },
  account: {
    starting_balance: "10000",
    current_equity: "10150.25",
    cumulative_realized_pnl: "150.25",
    unrealized_pnl: "0",
    open_trade_count: 0,
    closed_trade_count: 12,
    as_of: "2026-07-25T12:00:00Z",
    limitations: [],
  },
  metrics: {
    trade_count: 12,
    wins: 7,
    losses: 5,
    breakeven: 0,
    win_rate: 0.5833,
    net_pnl: "150.25",
    gross_profit: "200",
    gross_loss: "-50",
    total_fees: "5",
    total_funding: "0",
    avg_win: "40",
    avg_loss: "-20",
    expectancy: "12.52",
    profit_factor: 1.6,
    avg_r_multiple: 1.2,
    max_drawdown: "25",
    max_drawdown_pct: 0.0025,
    avg_duration_seconds: 3600,
    violations: 0,
    equity_curve: [],
  },
  open_exposure: {
    open_trade_count: 0,
    proposal_flow_count: 0,
    paper_validation_count: 0,
    unrealized_pnl_total: "0",
    notional_exposure: "0",
    limitations: [],
  },
  equity_curve: [
    {
      index: 0,
      timestamp: "2026-07-01T00:00:00Z",
      equity: "10000",
      cumulative_realized_pnl: "0",
      unrealized_pnl: "0",
      event: "start",
    },
    {
      index: 1,
      timestamp: "2026-07-25T12:00:00Z",
      equity: "10150.25",
      cumulative_realized_pnl: "150.25",
      unrealized_pnl: "0",
      event: "trade_close",
    },
  ],
  daily_series: [
    {
      date: "2026-07-25",
      starting_equity: "10000",
      ending_equity: "10150.25",
      daily_pnl: "150.25",
      daily_drawdown: "0",
      daily_drawdown_pct: 0,
      trades_closed: 12,
    },
  ],
  breakdowns: {
    by_symbol: [],
    by_setup: [],
    by_timeframe: [],
    by_strategy: [],
    by_source: [],
    by_detector: [],
  },
  trend: {
    label: "improving",
    window_days: 30,
    recent_net_pnl: "150.25",
    prior_net_pnl: "0",
    rationale: "Recent window positive",
  },
  date_range: null,
  filters_applied: {
    start_date: null,
    end_date: null,
    source: "all",
    symbol: null,
    setup: null,
    timeframe: null,
    timezone: "UTC",
  },
};

function ok<T>(data: T): SourceResult<T> {
  return { data, available: true, error: null, fallbackUsed: false };
}

function failed<T>(error = "down"): SourceResult<T> {
  return { data: null, available: false, error, fallbackUsed: false };
}

const emptyScoped = {
  setupId: null,
  userStrategyId: null,
  strategyVersionId: null,
  journalSource: null,
  ruleCompliance: null,
  marketRegime: null,
  minSample: 5,
  dimension: "condition" as const,
} as const;

let filterState: AnalyticsFilterState = {
  tab: "overview",
  dateFrom: null,
  dateTo: null,
  symbol: null,
  timeframe: null,
  portfolioSource: null,
  groupBy: "setup",
  bucketOffset: 0,
  ...emptyScoped,
  ignoredParams: [],
};

let sourcesState = {
  journal: ok(journalData),
  portfolio: ok(portfolioData),
  loading: false,
  bothFailed: false,
  partialData: false,
  loadedFilterKey: "key",
};

let setupSourcesState = {
  journal: ok(setupJournalData),
  evidence: ok(evidenceData),
  strategies: [] as Array<{ id: string; name: string }>,
  strategiesError: null as string | null,
  strategiesLoading: false,
  strategiesLoaded: false,
  loading: false,
  loadedFilterKey: "setup-key",
};

vi.mock("@/components/analytics/useAnalyticsFilters", () => ({
  useAnalyticsFilters: () => ({
    state: filterState,
    apiParams: {
      journal: { group_by: "overall" },
      portfolio: { timezone: "UTC" },
      ruleComplianceJournal: { group_by: "rule_compliance", limit: 20 },
      comparison: {},
      analyticsWindow: {},
      learningWindow: {},
      validation: { dimension: "condition", min_sample: 5 },
      strategyQuality: { min_sample: 5 },
      state: filterState,
    },
    setupApiParams: {
      journal: { group_by: filterState.groupBy, setup_id: filterState.setupId ?? undefined },
      evidence: { setup_id: filterState.setupId ?? undefined },
      state: filterState,
    },
    setTab: mockSetTab,
    applyDraft: mockApplyDraft,
    applyDatePreset: mockApplyPreset,
    setGroupBy: mockSetGroupBy,
    setBucketOffset: mockSetBucketOffset,
    setDimension: mockSetDimension,
    clearFilters: mockClear,
    cleanupIgnoredParams: mockCleanup,
  }),
  buildAnalyticsApiParams: vi.fn(),
}));

vi.mock("@/components/analytics/useAnalyticsSources", () => ({
  useAnalyticsSources: () => ({
    ...sourcesState,
    reload: mockReload,
  }),
}));

vi.mock("@/components/analytics/useSetupAnalyticsSources", () => ({
  useSetupAnalyticsSources: () => ({
    ...setupSourcesState,
    reload: mockSetupReload,
    reloadStrategies: mockReloadStrategies,
  }),
}));

vi.mock("@/components/analytics/AnalyticsCharts", async () => {
  const daily = await import("@/components/analytics/DailyPnlChart");
  const cumulative = await import("@/components/analytics/CumulativePnlChart");
  const win = await import("@/components/analytics/SetupWinRateChart");
  const expectancy = await import("@/components/analytics/SetupExpectancyChart");
  return {
    DailyPnlChart: daily.DailyPnlChart,
    CumulativePnlChart: cumulative.CumulativePnlChart,
    SetupWinRateChart: win.SetupWinRateChart,
    SetupExpectancyChart: expectancy.SetupExpectancyChart,
    RuleComplianceChart: () => <div data-testid="rule-compliance-chart" />,
    ComparisonChart: () => <div data-testid="comparison-chart" />,
  };
});

vi.mock("@/components/analytics/PerformanceCharts", async () => {
  const actual = await vi.importActual<typeof import("@/components/analytics/PerformanceCharts")>(
    "@/components/analytics/PerformanceCharts",
  );
  return {
    PerformanceCharts: (props: ComponentProps<typeof actual.PerformanceCharts>) => {
      performanceChartsMounted();
      return <actual.PerformanceCharts {...props} />;
    },
  };
});

vi.mock("@/components/analytics/SetupsCharts", async () => {
  const actual = await vi.importActual<typeof import("@/components/analytics/SetupsCharts")>(
    "@/components/analytics/SetupsCharts",
  );
  return {
    SetupsCharts: (props: ComponentProps<typeof actual.SetupsCharts>) => {
      setupsChartsMounted();
      return <actual.SetupsCharts {...props} />;
    },
  };
});

vi.mock("@/components/analytics/BehaviourCharts", () => ({
  BehaviourCharts: () => {
    behaviourChartsMounted();
    return <div data-testid="behaviour-charts">Behaviour charts</div>;
  },
}));

vi.mock("@/components/analytics/ValidationCharts", () => ({
  ValidationCharts: () => {
    validationChartsMounted();
    return <div data-testid="validation-charts">Validation charts</div>;
  },
}));

vi.mock("@/components/analytics/ComparisonCharts", () => ({
  ComparisonCharts: () => {
    comparisonChartsMounted();
    return <div data-testid="comparison-charts">Comparison charts</div>;
  },
}));

vi.mock("@/contexts/AppContext", () => ({
  useSafetyPosture: () => ({
    executionMode: "paper",
    realTradingEnabled: false,
    providerMode: "fallback",
  }),
  useAppContext: () => ({ health: { version: "test" } }),
}));

describe("AnalyticsPage PR1–PR4 integration", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    filterState = {
      tab: "overview",
      dateFrom: null,
      dateTo: null,
      symbol: null,
      timeframe: null,
      portfolioSource: null,
      groupBy: "setup",
      bucketOffset: 0,
      ...emptyScoped,
      ignoredParams: [],
    };
    sourcesState = {
      journal: ok(journalData),
      portfolio: ok(portfolioData),
      loading: false,
      bothFailed: false,
      partialData: false,
      loadedFilterKey: "key",
    };
    setupSourcesState = {
      journal: ok(setupJournalData),
      evidence: ok(evidenceData),
      strategies: [],
      strategiesError: null,
      strategiesLoading: false,
      strategiesLoaded: false,
      loading: false,
      loadedFilterKey: "setup-key",
    };
    vi.clearAllMocks();
    performanceChartsMounted.mockClear();
    setupsChartsMounted.mockClear();
    behaviourChartsMounted.mockClear();
    validationChartsMounted.mockClear();
    comparisonChartsMounted.mockClear();
  });

  it("keeps all six tabpanels in the DOM with overview active by default", () => {
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });

    const overviewTab = screen.getByRole("tab", { name: "Overview" });
    const performanceTab = screen.getByRole("tab", { name: "Performance" });
    const setupsTab = screen.getByRole("tab", { name: "Setups" });
    const behaviourTab = screen.getByRole("tab", { name: "Behaviour" });
    const validationTab = screen.getByRole("tab", { name: "Validation" });
    const comparisonTab = screen.getByRole("tab", { name: "Comparison" });

    expect(overviewTab).toHaveAttribute("aria-selected", "true");
    expect(performanceTab).toHaveAttribute("aria-selected", "false");
    expect(setupsTab).toHaveAttribute("aria-selected", "false");
    expect(behaviourTab).toHaveAttribute("aria-selected", "false");
    expect(validationTab).toHaveAttribute("aria-selected", "false");
    expect(comparisonTab).toHaveAttribute("aria-selected", "false");

    const tabs = [
      overviewTab,
      performanceTab,
      setupsTab,
      behaviourTab,
      validationTab,
      comparisonTab,
    ];
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Overview",
      "Performance",
      "Setups",
      "Behaviour",
      "Validation",
      "Comparison",
    ]);

    for (const tab of tabs) {
      const panelId = tab.getAttribute("aria-controls");
      expect(document.getElementById(panelId!)).toBeTruthy();
    }

    // Non-overview tabs expose an h2 so heading order does not skip to chart h3s (FP2-217).
    const performancePanel = document.getElementById(performanceTab.getAttribute("aria-controls")!);
    expect(performancePanel).toHaveAttribute("hidden");
    expect(
      within(performancePanel!).getByRole("heading", {
        level: 2,
        name: "Performance",
        hidden: true,
      }),
    ).toBeInTheDocument();
    for (const [tab, label] of [
      [setupsTab, "Setups"],
      [behaviourTab, "Behaviour"],
      [validationTab, "Validation"],
      [comparisonTab, "Comparison"],
    ] as const) {
      const panel = document.getElementById(tab.getAttribute("aria-controls")!);
      expect(
        within(panel!).getByRole("heading", { level: 2, name: label, hidden: true }),
      ).toBeInTheDocument();
    }
    expect(document.getElementById(setupsTab.getAttribute("aria-controls")!)).toHaveAttribute(
      "hidden",
    );
    expect(document.getElementById(behaviourTab.getAttribute("aria-controls")!)).toHaveAttribute(
      "hidden",
    );
    expect(document.getElementById(validationTab.getAttribute("aria-controls")!)).toHaveAttribute(
      "hidden",
    );
    expect(document.getElementById(comparisonTab.getAttribute("aria-controls")!)).toHaveAttribute(
      "hidden",
    );
  });

  it("does not mount performance/setups/behaviour/validation/comparison charts while overview is active", () => {
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(performanceChartsMounted).not.toHaveBeenCalled();
    expect(setupsChartsMounted).not.toHaveBeenCalled();
    expect(behaviourChartsMounted).not.toHaveBeenCalled();
    expect(validationChartsMounted).not.toHaveBeenCalled();
    expect(comparisonChartsMounted).not.toHaveBeenCalled();
  });

  it("mounts behaviour charts when the behaviour tab is active", () => {
    filterState = { ...filterState, tab: "behaviour" };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(behaviourChartsMounted).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("behaviour-charts")).toBeInTheDocument();
  });

  it("mounts validation charts only when the validation tab is active", () => {
    filterState = { ...filterState, tab: "validation" };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(validationChartsMounted).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("validation-charts")).toBeInTheDocument();
    expect(behaviourChartsMounted).not.toHaveBeenCalled();
    expect(comparisonChartsMounted).not.toHaveBeenCalled();
  });

  it("mounts comparison charts when the comparison tab is active", () => {
    filterState = { ...filterState, tab: "comparison" };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(comparisonChartsMounted).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("comparison-charts")).toBeInTheDocument();
    expect(validationChartsMounted).not.toHaveBeenCalled();
  });

  it("still mounts behaviour charts when overview/performance sources both fail", () => {
    filterState = { ...filterState, tab: "behaviour" };
    sourcesState = {
      journal: failed(),
      portfolio: failed(),
      loading: false,
      bothFailed: true,
      partialData: false,
      loadedFilterKey: "key",
    };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("behaviour-charts")).toBeInTheDocument();
    expect(screen.queryByTestId("error-state")).not.toBeInTheDocument();
  });

  it("renders overview stats from available sources", () => {
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("analytics-page")).toBeInTheDocument();
    expect(screen.getByTestId("overview-stats")).toBeInTheDocument();
    expect(screen.getByText("Realised P&L")).toBeInTheDocument();
    expect(screen.getByTestId("overview-tile-trend")).toHaveTextContent("Improving");
  });

  it("shows partial banner when one source fails", () => {
    sourcesState = {
      journal: failed("journal down"),
      portfolio: ok(portfolioData),
      loading: false,
      bothFailed: false,
      partialData: true,
      loadedFilterKey: "key",
    };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("analytics-partial-data")).toHaveTextContent(/partial analytics data/i);
    expect(screen.getByTestId("overview-journal-error")).toBeInTheDocument();
  });

  it("shows full error when both sources fail and keeps all tabpanels mounted", () => {
    sourcesState = {
      journal: failed(),
      portfolio: failed(),
      loading: false,
      bothFailed: true,
      partialData: false,
      loadedFilterKey: "key",
    };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("error-state")).toHaveTextContent(/both failed/i);
    expect(screen.queryByTestId("overview-stats")).not.toBeInTheDocument();
    const tablist = screen.getByRole("tablist", { name: "Analytics sections" });
    expect(within(tablist).getAllByRole("tab")).toHaveLength(6);
    for (const tab of within(tablist).getAllByRole("tab")) {
      const panel = document.getElementById(tab.getAttribute("aria-controls")!);
      expect(panel).toBeTruthy();
      expect(panel).toHaveAttribute("role", "tabpanel");
    }
  });

  it("renders performance charts without zero fabrication on portfolio failure", () => {
    filterState = { ...filterState, tab: "performance" };
    sourcesState = {
      journal: ok(journalData),
      portfolio: failed("portfolio down"),
      loading: false,
      bothFailed: false,
      partialData: true,
      loadedFilterKey: "key",
    };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("daily-pnl-chart-error")).toBeInTheDocument();
    expect(screen.getByTestId("cumulative-pnl-chart-error")).toBeInTheDocument();
    expect(screen.queryByTestId("daily-pnl-chart-plot")).not.toBeInTheDocument();
    expect(screen.queryByTestId("cumulative-pnl-chart-plot")).not.toBeInTheDocument();
  });

  it("wires tab aria-controls to tabpanels and aria-labelledby back to tabs", () => {
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    const tablist = screen.getByRole("tablist", { name: "Analytics sections" });
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs).toHaveLength(6);
    for (const tab of tabs) {
      const panelId = tab.getAttribute("aria-controls");
      expect(panelId).toBeTruthy();
      const panel = document.getElementById(panelId!);
      expect(panel).toBeTruthy();
      expect(panel).toHaveAttribute("role", "tabpanel");
      expect(panel?.getAttribute("aria-labelledby")).toBe(tab.id);
      expect(panel?.hasAttribute("hidden")).toBe(tab.getAttribute("aria-selected") !== "true");
    }
  });

  it("uses filter bar actions", () => {
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    fireEvent.click(screen.getByTestId("analytics-preset-30d"));
    expect(mockApplyPreset).toHaveBeenCalledWith("30d");
    fireEvent.click(screen.getByTestId("analytics-clear-filters"));
    expect(mockClear).toHaveBeenCalled();
  });

  it("shows tab-level stale state on overview when all available sources are stale", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-25T12:00:00Z"));
    sourcesState = {
      journal: ok({ ...journalData, generated_at: "2026-07-25T11:00:00Z" }),
      portfolio: ok({
        ...portfolioData,
        account: { ...portfolioData.account, as_of: "2026-07-25T11:00:00Z" },
      }),
      loading: false,
      bothFailed: false,
      partialData: false,
      loadedFilterKey: "key",
    };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("overview-stale-state")).toHaveTextContent(/stale for this view/i);
    vi.useRealTimers();
  });

  it("treats future-skewed portfolio as unavailable on performance tab", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-25T12:00:00Z"));
    filterState = { ...filterState, tab: "performance" };
    sourcesState = {
      journal: ok(journalData),
      portfolio: ok({
        ...portfolioData,
        account: { ...portfolioData.account, as_of: "2026-07-25T12:05:00Z" },
      }),
      loading: false,
      bothFailed: false,
      partialData: false,
      loadedFilterKey: "key",
    };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("daily-pnl-chart-error")).toHaveTextContent(/clock-skewed/i);
    expect(screen.getByTestId("cumulative-pnl-chart-error")).toHaveTextContent(/clock-skewed/i);
    expect(screen.queryByTestId("daily-pnl-chart-plot")).not.toBeInTheDocument();
    expect(screen.queryByTestId("cumulative-pnl-chart-plot")).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("mounts setups charts only when the setups tab is active", () => {
    filterState = {
      ...filterState,
      tab: "setups",
      groupBy: "setup_version",
      setupId: "11111111-1111-1111-1111-111111111111",
    };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(setupsChartsMounted).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("setups-charts")).toBeInTheDocument();
    expect(screen.getByTestId("setup-win-rate-chart")).toBeInTheDocument();
    expect(screen.getByTestId("setup-expectancy-chart")).toBeInTheDocument();
    expect(screen.getByTestId("setup-bucket-table")).toBeInTheDocument();
    expect(screen.getByTestId("setup-evidence-panel")).toBeInTheDocument();
    expect(screen.getByTestId("analytics-setup-id")).toHaveValue(
      "11111111-1111-1111-1111-111111111111",
    );
  });

  it("keeps setups tabpanels mounted when both setups sources fail and shows both errors", () => {
    filterState = { ...filterState, tab: "setups" };
    setupSourcesState = {
      ...setupSourcesState,
      journal: failed("journal down"),
      evidence: failed("evidence down"),
    };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    const setupsTab = screen.getByRole("tab", { name: "Setups" });
    const panel = document.getElementById(setupsTab.getAttribute("aria-controls")!);
    expect(panel).toBeTruthy();
    expect(panel).not.toHaveAttribute("hidden");
    expect(screen.getByTestId("setup-win-rate-chart-error")).toBeInTheDocument();
    expect(screen.getByTestId("setup-evidence-panel-error")).toBeInTheDocument();
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  });

  it("shows unassigned and No P&L data on setups tab", () => {
    filterState = { ...filterState, tab: "setups", groupBy: "setup_version" };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("setup-win-rate-row-unassigned")).toBeInTheDocument();
    expect(screen.getByTestId("setup-expectancy-row-unassigned")).toHaveTextContent("No P&L data");
  });

  it("wires grouping toggle through push handler", () => {
    filterState = { ...filterState, tab: "setups" };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    fireEvent.click(screen.getByTestId("setup-group-strategy"));
    expect(mockSetGroupBy).toHaveBeenCalledWith("strategy");
  });

  it("shows strategies loading without treating it as confirmed empty", () => {
    filterState = { ...filterState, tab: "setups" };
    setupSourcesState = {
      ...setupSourcesState,
      strategiesLoading: true,
      strategiesLoaded: false,
      strategies: [],
    };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("analytics-strategies-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("analytics-strategies-empty")).not.toBeInTheDocument();
  });

  it("shows strategies failure with independent retry and does not collapse charts", () => {
    filterState = { ...filterState, tab: "setups" };
    setupSourcesState = {
      ...setupSourcesState,
      strategiesError: "strategies down",
      strategiesLoaded: false,
      strategies: [],
    };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("analytics-strategies-error")).toHaveTextContent(/strategies down/i);
    expect(screen.getByTestId("setup-win-rate-chart-plot")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("analytics-strategies-retry"));
    expect(mockReloadStrategies).toHaveBeenCalled();
  });

  it("shows successful empty strategies message after load", () => {
    filterState = { ...filterState, tab: "setups" };
    setupSourcesState = {
      ...setupSourcesState,
      strategiesLoaded: true,
      strategiesLoading: false,
      strategies: [],
    };
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("analytics-strategies-empty")).toHaveTextContent(
      /No strategies available/i,
    );
  });

  it("does not use page-level overflow-x-hidden", () => {
    render(<AnalyticsPage />, { wrapper: PrivateQueryFixture });
    expect(screen.getByTestId("analytics-page").className).not.toMatch(/overflow-x-hidden/);
  });
});
