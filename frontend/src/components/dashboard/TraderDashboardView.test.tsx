import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readNativeActivity } from "@/lib/api/blofin-activity";
import { demoAccountApi, type DashboardDemoAccount } from "@/lib/api/dashboard-demo-account";
import { activityOrganization, activityPage } from "@/test/native-activity-fixtures";
import { describeSafetyPosture } from "@/components/workflows/safetyPostureDisplay";
import { TraderDashboardView, type TraderDashboardData } from "./TraderDashboardView";

let identity = { user: { id: "reader-1" }, organization: { id: activityOrganization } };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => identity }));
vi.mock("@/lib/api/blofin-activity", () => ({ readNativeActivity: vi.fn() }));
vi.mock("@/lib/api/dashboard-demo-account", () => ({ demoAccountApi: { latest: vi.fn(), refresh: vi.fn() } }));
vi.mock("./AttentionCard", () => ({ AttentionCard: () => null }));
vi.mock("./DailyReviewCard", () => ({ DailyReviewCard: () => null }));
const performanceInstances = vi.hoisted(() => ({ next: 0 }));
vi.mock("./performance/PerformanceDashboard", async () => {
  const { useState } = await import("react");
  return {
    PerformanceDashboard: ({ scope }: { scope?: string }) => {
      const [instance] = useState(() => (performanceInstances.next += 1));
      return <span data-testid="performance-instance" data-scope={scope}>{instance}</span>;
    },
  };
});
vi.mock("@/components/settings/ManualDemoTest", () => ({ ManualDemoTest: () => null }));

const unavailable = { available: false, data: null, error: null, fallbackUsed: false };
const data: TraderDashboardData = { portfolio: unavailable, summary: unavailable,
  watcher: unavailable, market: unavailable, alerts: unavailable,
  journal: { available: true, error: null, fallbackUsed: false, data: { items: [{
    id: "internal-simulated", symbol: "SIMULATED", timeframe: "1h", direction: "long", status: "closed", result: "win", source: "paper_execution", exchange: "BLOFIN_DEMO",
  }], total: 1, limit: 50, offset: 0 } } };
const account: DashboardDemoAccount = { venue: "BLOFIN_DEMO", read_only: true, account_id: "account-one", status: "ok", can_refresh: false,
  snapshot_id: "synthetic", synced_at: "2026-10-10T10:00:00Z", expires_at: null, total_equity_usd: "1234.50",
  refresh_error: null, last_attempt_at: null, balances: [], positions: [], balances_truncated: false,
  positions_truncated: false, position_count: 0, message: "Saved native snapshot" };

beforeEach(() => {
  identity = { user: { id: "reader-1" }, organization: { id: activityOrganization } };
  vi.mocked(readNativeActivity).mockReset().mockResolvedValue(activityPage());
  vi.mocked(demoAccountApi.latest).mockReset().mockResolvedValue(account);
});
afterEach(cleanup);

describe("Dashboard native integration", () => {
  it("shows simplified native activity and keeps internal simulator records out of BloFin", async () => {
    render(<TraderDashboardView data={data} posture={describeSafetyPosture("paper", false)} />);
    await screen.findByTestId("native-activity-row");
    expect(screen.getByTestId("native-activity-last-synced")).toBeInTheDocument();
    expect(screen.queryByTestId("native-activity-statistics")).not.toBeInTheDocument();
    expect(screen.queryByText("History coverage & age")).not.toBeInTheDocument();
    expect(screen.queryByText(/SIMULATED/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Paper" }));
    expect(screen.queryByTestId("native-activity")).not.toBeInTheDocument();
    expect(screen.getByText(/SIMULATED/)).toBeInTheDocument();
    expect(demoAccountApi.refresh).not.toHaveBeenCalled();
  });

  it("clears both native history and balance caches when the authenticated account context changes", async () => {
    const { rerender } = render(<TraderDashboardView data={data} posture={describeSafetyPosture("paper", false)} />);
    await screen.findByTestId("native-activity-row");
    expect(screen.getByTestId("dashboard-equity")).toHaveTextContent("1,234.5");
    identity = { ...identity, user: { id: "reader-2" } };
    vi.mocked(readNativeActivity).mockResolvedValue(activityPage({ items: [] }));
    vi.mocked(demoAccountApi.latest).mockResolvedValue({ ...account, account_id: "account-two", status: "inactive", total_equity_usd: null });
    rerender(<TraderDashboardView data={data} posture={describeSafetyPosture("paper", false)} />);
    expect(screen.queryByTestId("native-activity-row")).not.toBeInTheDocument();
    expect(screen.getByTestId("dashboard-equity")).not.toHaveTextContent("1,234.5");
    await screen.findByText("No fills yet.");
  });

  it("remounts the performance dashboard when the account context changes", () => {
    const view = () => <TraderDashboardView data={data} posture={describeSafetyPosture("paper", false)} />;
    const { rerender } = render(view());
    const first = screen.getByTestId("performance-instance").textContent;
    rerender(view());
    expect(screen.getByTestId("performance-instance").textContent).toBe(first);
    identity = { ...identity, user: { id: "reader-2" } };
    rerender(view());
    expect(screen.getByTestId("performance-instance").textContent).not.toBe(first);
  });

  it("drives performance and the account block from one venue control", async () => {
    render(<TraderDashboardView data={data} posture={describeSafetyPosture("paper", false)} />);
    expect(screen.queryByRole("combobox", { name: "Dashboard account" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("radiogroup")).toHaveLength(1);
    expect(screen.getByTestId("performance-instance")).toHaveAttribute("data-scope", "demo");
    await screen.findByTestId("native-activity-row");
    fireEvent.click(screen.getByRole("radio", { name: "Paper" }));
    expect(screen.getByTestId("performance-instance")).toHaveAttribute("data-scope", "paper");
    expect(screen.queryByTestId("native-activity")).not.toBeInTheDocument();
    expect(screen.getByText("Internal simulator")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Other trade scopes" }), { target: { value: "backtest" } });
    expect(screen.getByTestId("performance-instance")).toHaveAttribute("data-scope", "backtest");
    expect(screen.queryByText("Internal simulator")).not.toBeInTheDocument();
    expect(screen.getByTestId("dashboard-venue-no-account")).toHaveTextContent("Backtest trades");
  });

  it("keeps the header to the title and its two actions", () => {
    render(<TraderDashboardView data={data} posture={describeSafetyPosture("paper", false)} />);
    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Agent" })).toHaveAttribute("href", "/agent");
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
    expect(screen.queryByText(/Journal performance, BloFin demo account/)).not.toBeInTheDocument();
  });

  it("disables Real unless real trading is verifiably enabled", () => {
    const { rerender } = render(<TraderDashboardView data={data} posture={describeSafetyPosture("paper", false)} />);
    expect(screen.getByRole("radio", { name: "Real" })).toBeDisabled();
    expect(screen.getByTestId("dashboard-venue-real-disabled")).toBeInTheDocument();
    rerender(<TraderDashboardView data={data} posture={describeSafetyPosture("paper", null)} realTradingEnabled={null} />);
    expect(screen.getByRole("radio", { name: "Real" })).toBeDisabled();
    rerender(<TraderDashboardView data={data} posture={describeSafetyPosture("paper", true)} realTradingEnabled />);
    expect(screen.getByRole("radio", { name: "Real" })).toBeEnabled();
  });
});
