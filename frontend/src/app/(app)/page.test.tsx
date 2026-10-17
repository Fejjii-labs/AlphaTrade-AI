vi.mock("@/components/settings/ManualDemoActivity", () => ({
  ManualDemoActivity: () => <div>Manual history fixture</div>,
}));
vi.mock("@/components/settings/ManualDemoTest", () => ({
  ManualDemoTest: () => <div>Manual demo fixture</div>,
}));
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "./page";
import { okSource } from "@/components/workflows/sourceResult";
import { formatCurrency, formatMonetary, UNAVAILABLE } from "@/lib/format";
import { makeWatcherMonitoringSnapshot } from "@/lib/watcher-monitoring-fixtures";
import type { TraderDashboardData } from "@/components/dashboard/TraderDashboardView";
import type {
  CanonicalMarketMonitorStatusRead,
  DashboardSummary,
  CanonicalJournalTradeListItem,
  JournalStatsResponse,
  PaginatedCanonicalJournalTrades,
  PaperAlert,
  PaperPortfolioResponse,
} from "@/lib/api/types";

const safetyPosture = {
  executionMode: "paper" as string | null,
  realTradingEnabled: false as boolean | null,
};

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "dashboard-reader" }, organization: { id: "dashboard-tenant" } }),
}));

vi.mock("@/components/activity/useNativeActivity", () => ({
  useNativeActivity: () => ({
    page: null, busy: false, error: "Native activity unavailable", refresh: vi.fn(), more: vi.fn(),
  }),
}));

vi.mock("@/contexts/AppContext", () => ({
  useSafetyPosture: () => safetyPosture,
}));

vi.mock("@/components/dashboard/BloFinDemoAccountCard", () => ({
  BloFinDemoAccountCard: ({ refreshKey }: { refreshKey: number }) => (
    <section data-testid="dashboard-demo-account" data-refresh-key={refreshKey}>
      BloFin demo account
    </section>
  ),
}));

function portfolio(
  tradeCount: number,
  winRate: number,
): PaperPortfolioResponse {
  return {
    account: { current_equity: "1000.50", open_trade_count: 3 },
    metrics: { trade_count: tradeCount, win_rate: winRate, net_pnl: "12.50" },
    breakdowns: {
      by_strategy: [
        {
          key: "HTF Pullback",
          metrics: { net_pnl: "12.50", trade_count: tradeCount },
        },
      ],
    },
  } as PaperPortfolioResponse;
}

function dashboardData(
  overrides: Partial<TraderDashboardData> = {},
): TraderDashboardData {
  const base: TraderDashboardData = {
    portfolio: okSource(portfolio(4, 0.5)),
    journal: okSource({
      items: [
        {
          id: "j1",
          symbol: "ETHUSDT",
          direction: "short",
          result: "loss",
          net_pnl: "-2.00",
          status: "closed",
          created_at: "2026-10-01T00:00:00Z",
        } as CanonicalJournalTradeListItem,
      ],
      total: 1,
      limit: 8,
      offset: 0,
    } as PaginatedCanonicalJournalTrades),
    strategyStats: okSource({
      buckets: [
        {
          key: "s1",
          label: "HTF Pullback",
          metrics: { trade_count: 4, win_rate: 0.5, net_pnl_total: "12.50" },
        },
      ],
    } as JournalStatsResponse),
    summary: okSource({
      safety: { execution_mode: "paper", real_trading_enabled: false },
      open_paper_trades_summary: {
        total_count: 1,
        items: [
          {
            journal_trade_id: "canonical-p1",
            symbol: "BTCUSDT",
            direction: "long",
            exchange: "PAPER_INTERNAL",
            account_id: "account-one",
            unrealized_pnl: null,
          },
        ],
      },
    } as DashboardSummary),
    watcher: okSource(makeWatcherMonitoringSnapshot()),
    market: okSource({
      symbol: "BTCUSDT",
      availability: "fresh",
    } as CanonicalMarketMonitorStatusRead),
    alerts: okSource({
      items: [
        {
          id: "a1",
          message: "Paper target reached",
          severity: "high",
          created_at: "2026-01-01",
        } as PaperAlert,
      ],
      total: 1,
    }),
  };
  return { ...base, ...overrides };
}

const asyncState: {
  data: TraderDashboardData | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
} = {
  data: dashboardData(),
  loading: false,
  error: null,
  reload: vi.fn(),
};

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: () => asyncState,
}));

afterEach(() => {
  cleanup();
  safetyPosture.executionMode = "paper";
  safetyPosture.realTradingEnabled = false;
  asyncState.data = dashboardData();
  asyncState.loading = false;
  asyncState.error = null;
});

describe("Trader dashboard", () => {
  it("refreshes the saved demo snapshot together with the Dashboard", () => {
    render(<DashboardPage />);
    expect(screen.getByTestId("dashboard-demo-account")).toHaveAttribute(
      "data-refresh-key",
      "0",
    );
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(screen.getByTestId("dashboard-demo-account")).toHaveAttribute(
      "data-refresh-key",
      "1",
    );
    expect(asyncState.reload).toHaveBeenCalled();
  });
  it("keeps Daily Review compact without duplicating the shell mode", () => {
    render(<DashboardPage />);
    expect(
      screen.queryByTestId("dashboard-paper-only"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("paper-mode-indicator"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Dashboard" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("daily-review-unavailable")).toBeInTheDocument();
  });
  it("retains the runtime conflict warning", () => {
    safetyPosture.realTradingEnabled = true;
    asyncState.data = dashboardData({
      summary: okSource({
        safety: { execution_mode: "paper", real_trading_enabled: true },
      } as DashboardSummary),
    });
    render(<DashboardPage />);
    expect(screen.getByTestId("dashboard-safety-conflict")).toHaveTextContent(
      /safety conflict/i,
    );
  });
  it("shows paper value, pnl, win rate, positions, and trader watcher status", () => {
    render(<DashboardPage />);
    fireEvent.change(
      screen.getByRole("combobox", { name: "Dashboard account" }),
      { target: { value: "simulator" } },
    );
    expect(screen.getByTestId("dashboard-equity")).toHaveTextContent(
      formatCurrency("1000.50"),
    );
    expect(screen.getByTestId("dashboard-pnl")).toHaveTextContent(
      formatMonetary("12.50"),
    );
    expect(screen.getByTestId("dashboard-win-rate")).toHaveTextContent("50.0%");
    expect(
      screen.getByTestId("dashboard-strategy-performance"),
    ).toHaveTextContent("HTF Pullback");
    expect(screen.getByTestId("dashboard-watcher-status")).toHaveTextContent(
      "Stopped",
    );
    expect(screen.getByTestId("dashboard-market-evidence")).toHaveTextContent(
      "Healthy",
    );
    expect(screen.getByTestId("dashboard-alerts")).toHaveTextContent(
      "Paper target reached",
    );
    expect(
      screen.queryByTestId("watcher-monitoring-card"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("RUNNING")).not.toBeInTheDocument();
  });

  it("does not present an unmeasured win rate as zero", () => {
    asyncState.data = dashboardData({ portfolio: okSource(portfolio(0, 0)) });
    render(<DashboardPage />);
    fireEvent.change(
      screen.getByRole("combobox", { name: "Dashboard account" }),
      { target: { value: "simulator" } },
    );
    expect(screen.getByTestId("dashboard-win-rate")).toHaveTextContent(
      UNAVAILABLE,
    );
    expect(screen.getByTestId("dashboard-win-rate")).toHaveTextContent(
      "No closed trades yet",
    );
    expect(screen.getByTestId("dashboard-win-rate")).not.toHaveTextContent(
      "0.0%",
    );
  });

  it("keeps unavailable daily status explicit and reports total open positions", () => {
    const data = dashboardData();
    asyncState.data = dashboardData({
      summary: okSource({
        ...data.summary.data!,
        open_paper_trades_summary: {
          ...data.summary.data!.open_paper_trades_summary!,
          total_count: 25,
        },
      }),
    });
    render(<DashboardPage />);
    fireEvent.change(
      screen.getByRole("combobox", { name: "Dashboard account" }),
      { target: { value: "simulator" } },
    );
    expect(screen.getByTestId("dashboard-open-count")).toHaveTextContent("3");
    expect(screen.getByTestId("dashboard-daily-status")).toHaveTextContent(
      "Daily status unavailable",
    );
    expect(screen.getByTestId("dashboard-daily-pnl")).toHaveTextContent(
      UNAVAILABLE,
    );
    expect(screen.getByTestId("dashboard-expectancy")).toHaveTextContent(
      "Expectancy unavailable",
    );
  });

  it("defaults to one BloFin account view and keeps simulator metrics behind explicit selection", () => {
    render(<DashboardPage />);
    expect(screen.getAllByTestId("dashboard-demo-account")).toHaveLength(1);
    expect(
      screen.queryByTestId("dashboard-strategy-performance"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("ETHUSDT")).not.toBeInTheDocument();
    fireEvent.change(
      screen.getByRole("combobox", { name: "Dashboard account" }),
      { target: { value: "simulator" } },
    );
    expect(
      screen.queryByTestId("dashboard-demo-account"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("dashboard-equity")).toHaveTextContent(
      formatCurrency("1000.50"),
    );
    expect(screen.getByTestId("dashboard-open-count")).toHaveTextContent("3");
    expect(
      screen.getByRole("link", { name: "Open simulator portfolio" }),
    ).toHaveAttribute("href", "/portfolio");
    expect(screen.queryByText("BTCUSDT · long")).not.toBeInTheDocument();
  });

  it("shows loading and error states", () => {
    asyncState.loading = true;
    asyncState.data = null;
    const { rerender } = render(<DashboardPage />);
    expect(screen.getByText(/loading dashboard/i)).toBeInTheDocument();
    asyncState.loading = false;
    asyncState.error = "Failed to load";
    rerender(<DashboardPage />);
    expect(screen.getByText("Failed to load")).toBeInTheDocument();
  });
});
