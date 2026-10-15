vi.mock("@/components/settings/ManualDemoActivity", () => ({
  ManualDemoActivity: () => <div>Manual history fixture</div>,
}));
vi.mock("@/components/settings/ManualDemoTest", () => ({
  ManualDemoTest: () => <div>Manual demo fixture</div>,
}));
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "./page";
import type { TraderDashboardData } from "@/components/dashboard/TraderDashboardView";
import { failedSource } from "@/components/workflows/sourceResult";
import { UNAVAILABLE } from "@/lib/format";

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
  useSafetyPosture: () => ({
    executionMode: "paper",
    realTradingEnabled: false,
  }),
}));

function failedDashboard(): TraderDashboardData {
  return {
    portfolio: failedSource("portfolio down"),
    journal: failedSource("journal down"),
    strategyStats: failedSource("stats down"),
    summary: failedSource("summary down"),
    watcher: failedSource("watcher down"),
    market: failedSource("market down"),
    alerts: failedSource("alerts down"),
  };
}

const asyncState: {
  data: TraderDashboardData | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
} = {
  data: failedDashboard(),
  loading: false,
  error: null,
  reload: vi.fn(),
};

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: () => asyncState,
}));

afterEach(() => {
  cleanup();
  asyncState.data = failedDashboard();
});

describe("Trader dashboard unavailable sources", () => {
  it("renders unavailable figures instead of zeros", () => {
    render(<DashboardPage />);
    fireEvent.change(
      screen.getByRole("combobox", { name: "Dashboard account" }),
      { target: { value: "simulator" } },
    );
    expect(screen.getByTestId("dashboard-equity")).toHaveTextContent(
      UNAVAILABLE,
    );
    expect(screen.getByTestId("dashboard-pnl")).toHaveTextContent(UNAVAILABLE);
    expect(screen.getByTestId("dashboard-win-rate")).toHaveTextContent(
      UNAVAILABLE,
    );
    expect(screen.getByTestId("dashboard-equity")).not.toHaveTextContent(
      "0.00",
    );
    expect(
      screen.getByTestId("dashboard-strategy-performance"),
    ).toHaveTextContent("Closed paper strategy performance unavailable");
    expect(screen.getByTestId("dashboard-watcher-status")).toHaveTextContent(
      "Unavailable",
    );
    expect(screen.getByTestId("dashboard-market-evidence")).toHaveTextContent(
      "Unavailable",
    );
    expect(screen.getByTestId("dashboard-alerts")).toHaveTextContent(
      "Alerts unavailable",
    );
    expect(screen.queryByText("No open positions")).not.toBeInTheDocument();
    expect(screen.getByTestId("daily-review-unavailable")).toBeInTheDocument();
    expect(
      screen.queryByTestId("daily-review-content"),
    ).not.toBeInTheDocument();
  });

  it("never substitutes unrelated empty Journal positions for unavailable native account data", () => {
    render(<DashboardPage />);
    expect(screen.getByTestId("dashboard-open-count")).toHaveTextContent(
      UNAVAILABLE,
    );
    expect(
      screen.queryByText("No native open positions at this snapshot."),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("dashboard-strategy-performance"),
    ).not.toBeInTheDocument();
  });
});
