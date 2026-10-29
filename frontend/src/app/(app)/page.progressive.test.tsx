import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import DashboardPage from "./page";
import { api } from "@/lib/api";

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u" }, organization: { id: "o" } }),
}));
vi.mock("@/components/activity/useNativeActivity", () => ({
  useNativeActivity: () => ({ page: null, busy: false, error: null, refresh: vi.fn(), more: vi.fn() }),
}));
vi.mock("@/contexts/AppContext", () => ({
  useSafetyPosture: () => ({ executionMode: "paper", realTradingEnabled: false }),
}));
vi.mock("@/components/dashboard/AttentionCard", () => ({ AttentionCard: () => null }));
vi.mock("@/components/dashboard/DailyReviewCard", () => ({ DailyReviewCard: () => null }));
vi.mock("@/components/dashboard/BloFinDemoAccountCard", () => ({
  BloFinDemoAccountCard: () => <section data-testid="demo-card">demo</section>,
}));

let releaseWatcher: (value: unknown) => void = () => {};
vi.mock("@/lib/api", () => ({
  api: {
    performance: {
      portfolio: vi.fn().mockResolvedValue({
        account: { current_equity: "1000.50", open_trade_count: 3 },
        metrics: { trade_count: 0, win_rate: 0, net_pnl: "0" },
        breakdowns: { by_strategy: [] },
      }),
    },
    journal: {
      listTrades: vi.fn().mockResolvedValue({ total: 0, limit: 8, offset: 0, items: [] }),
      statistics: vi.fn().mockResolvedValue({ groups: [] }),
    },
    dashboard: {
      summary: vi.fn().mockResolvedValue({ safety: { execution_mode: "paper", real_trading_enabled: false } }),
    },
    // The slow endpoint: never resolves until the test releases it.
    marketWatcher: {
      monitoring: vi.fn(() => new Promise((resolve) => { releaseWatcher = resolve; })),
    },
    canonical: { getMarketStatus: vi.fn().mockRejectedValue(new Error("down")) },
    alerts: { list: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
  },
}));

afterEach(cleanup);

it("fires all requests in parallel and renders sections while a slow one is pending", async () => {
  render(<DashboardPage />);
  // Page shell and the account card mount immediately (no global skeleton gate).
  expect(screen.getByRole("heading", { level: 1, name: "Dashboard" })).toBeInTheDocument();
  expect(screen.getByTestId("demo-card")).toBeInTheDocument();
  expect(screen.queryByText(/loading dashboard/i)).not.toBeInTheDocument();
  for (const fn of [
    api.performance.portfolio, api.journal.listTrades, api.journal.statistics,
    api.dashboard.summary, api.marketWatcher.monitoring, api.canonical.getMarketStatus,
    api.alerts.list,
  ]) {
    expect(fn).toHaveBeenCalledTimes(1);
  }
  fireEvent.change(screen.getByRole("combobox", { name: "Dashboard account" }), {
    target: { value: "simulator" },
  });
  // Fast sections render while the watcher is still in flight.
  await waitFor(() =>
    expect(screen.getByTestId("dashboard-open-count")).toHaveTextContent("3"),
  );
  // Await each settled section instead of assuming microtask ordering.
  await waitFor(() =>
    expect(screen.getByTestId("dashboard-market-evidence")).toHaveTextContent("Unavailable"),
  );
  // The failed source is listed as unavailable; the pending watcher is not.
  await waitFor(() => expect(screen.getByText(/Unavailable:/)).toHaveTextContent(/market/i));
  expect(screen.getByText(/Unavailable:/)).not.toHaveTextContent(/watcher/i);
  expect(screen.getByTestId("dashboard-watcher-status")).toHaveTextContent("Loading…");
  expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled();

  await act(async () => {
    releaseWatcher({ paper_monitoring_status: "stopped" });
  });
  await waitFor(() =>
    expect(screen.getByTestId("dashboard-watcher-status")).not.toHaveTextContent("Loading…"),
  );
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled());
});
