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
vi.mock("@/components/settings/ManualDemoTest", () => ({ ManualDemoTest: () => null }));

const unavailable = { available: false, data: null, error: null, fallbackUsed: false };
const data: TraderDashboardData = { portfolio: unavailable, strategyStats: unavailable, summary: unavailable,
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
  it("shows native activity/statistics and keeps internal simulator records out of BloFin", async () => {
    render(<TraderDashboardView data={data} posture={describeSafetyPosture("paper", false)} />);
    await screen.findByTestId("native-activity-row");
    expect(screen.getByTestId("native-activity-statistics")).toBeInTheDocument();
    expect(screen.queryByText(/SIMULATED/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Dashboard account" }), { target: { value: "simulator" } });
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
    await screen.findByText(/No stored fills/);
  });
});
