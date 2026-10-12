import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import DashboardPage from "./page";
import { api } from "@/lib/api";

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "dashboard-reader" }, organization: { id: "dashboard-tenant" } }),
}));

vi.mock("@/components/activity/useNativeActivity", () => ({
  useNativeActivity: () => ({
    page: null, busy: false, error: "Native activity unavailable", refresh: vi.fn(), more: vi.fn(),
  }),
}));
vi.mock("@/components/dashboard/useDemoAccountSnapshot", () => ({
  useDemoAccountSnapshot: () => ({
    account: null, busy: false, error: "Account unavailable", status: "unavailable", hasData: false, load: vi.fn(),
  }),
}));

vi.mock("@/contexts/AppContext", () => ({
  useSafetyPosture: () => ({ executionMode: "paper", realTradingEnabled: false }),
}));
vi.mock("@/components/dashboard/AttentionCard", () => ({ AttentionCard: () => null }));
vi.mock("@/components/dashboard/DailyReviewCard", () => ({ DailyReviewCard: () => null }));
vi.mock("@/lib/api", () => ({
  api: {
    performance: { portfolio: vi.fn().mockRejectedValue(new Error("unavailable")) },
    positions: { list: vi.fn() },
    journal: {
      list: vi.fn(),
      listTrades: vi.fn().mockResolvedValue({
        total: 1, limit: 8, offset: 0,
        items: [{ id: "canonical-trade", symbol: "BTCUSDT", direction: "long", status: "open", net_pnl: null }],
      }),
      statistics: vi.fn().mockRejectedValue(new Error("unavailable")),
    },
    dashboard: { summary: vi.fn().mockResolvedValue({
      safety: { execution_mode: "paper", real_trading_enabled: false },
      open_paper_trades_summary: {
        total_count: 1,
        items: [{ journal_trade_id: "canonical-trade", symbol: "BTCUSDT", direction: "long", status: "open", unrealized_pnl: null }],
      },
    }) },
    marketWatcher: { monitoring: vi.fn().mockRejectedValue(new Error("unavailable")) },
    canonical: { getMarketStatus: vi.fn().mockRejectedValue(new Error("unavailable")) },
    alerts: { list: vi.fn().mockRejectedValue(new Error("unavailable")) },
  },
}));

afterEach(cleanup);

it("uses the configured native account without substituting generic Journal positions", async () => {
  render(<DashboardPage />);
  await waitFor(() => expect(api.dashboard.summary).toHaveBeenCalledOnce());
  expect(screen.getByTestId("dashboard-open-count")).toHaveTextContent("—");
  expect(api.positions.list).not.toHaveBeenCalled();
  expect(api.journal.list).not.toHaveBeenCalled();
  expect(screen.queryByTestId("dashboard-open-positions")).not.toBeInTheDocument();
  expect(screen.queryByTestId("dashboard-recent-trades")).not.toBeInTheDocument();
});
