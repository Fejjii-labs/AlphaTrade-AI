import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import SettingsPage from "./page";
import NotificationSettingsPage from "./notifications/page";
import WatcherSettingsPage from "./watcher/page";
import { api } from "@/lib/api";
import type {
  HealthResponse,
  ProviderStatusResponse,
  UserRiskSettings,
} from "@/lib/api/types";
import { makeWatcherMonitoringSnapshot } from "@/lib/watcher-monitoring-fixtures";

const runtime = {
  health: null as HealthResponse | null,
  providers: null as ProviderStatusResponse | null,
  loading: false,
  refreshStatus: vi.fn(),
};
let identity = {
  user: { id: "user-1", email: "owner@example.com", email_verified: true },
  organization: { id: "org-1", name: "Alpha Org" },
};
const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => identity,
}));
vi.mock("@/contexts/AppContext", () => ({
  useAppContext: () => runtime,
  useSafetyPosture: () => ({
    executionMode: runtime.health?.execution_mode ?? null,
    realTradingEnabled: runtime.health?.real_trading_enabled ?? null,
    postureKnown: runtime.health !== null,
  }),
}));

const risk: UserRiskSettings = {
  organization_id: "org-1",
  user_id: "user-1",
  daily_loss_limit: "75.25",
  daily_target: null,
  max_trades_per_day: 4,
  max_risk_per_trade_percent: "0.75",
  default_account_balance: "10000.00",
  timezone: "Europe/Berlin",
  green_day_protection_enabled: true,
  one_loss_stop_enabled: false,
  overtrading_guard_enabled: true,
  notes: null,
  using_defaults: false,
  timezone_fallback: false,
};
const snapshot = makeWatcherMonitoringSnapshot({
  watcher_status: "STALE",
  paper_monitoring_status: "STOPPED",
  paper_posture: {
    ...makeWatcherMonitoringSnapshot().paper_posture,
    watcher_config_enabled: true,
  },
  approved_strategies: [
    {
      strategy_id: "s1",
      strategy_version_id: "active-v2",
      name: "Breakout",
      lifecycle_state: "active",
      compiled: true,
    },
    {
      strategy_id: "s1",
      strategy_version_id: "approved-v3",
      name: "Breakout",
      lifecycle_state: "approved",
      compiled: true,
    },
  ],
  market_freshness: { status: "replay", observed_at: "2026-10-01T10:00:00Z" },
  scanner_candidates: {
    count: 3,
    conditions: [],
    source: "market_watcher_scan",
  },
});
const slots = [
  { position: 1, symbol: "BTCUSDT", enabled: true },
  { position: 2, symbol: "ETHUSDT", enabled: false },
];

beforeEach(() => {
  identity = {
    user: { id: "user-1", email: "owner@example.com", email_verified: true },
    organization: { id: "org-1", name: "Alpha Org" },
  };
  vi.restoreAllMocks();
  runtime.health = {
    execution_mode: "paper",
    real_trading_enabled: false,
    telegram_network_permitted: false,
  } as HealthResponse;
  runtime.providers = {
    generated_at: "2026-10-01T10:00:00Z",
    providers: [
      {
        name: "Replay",
        kind: "market_data",
        health: "healthy",
        is_mock: true,
        using_fallback: false,
      },
    ],
  };
  runtime.refreshStatus = vi.fn().mockResolvedValue(undefined);
  vi.spyOn(api.execution, "paperAccountStatus").mockResolvedValue({
    account: null,
    can_register: true,
  });
  vi.spyOn(api.marketWatcher, "monitoring").mockResolvedValue(snapshot);
  vi.spyOn(api.strategies, "listVersions").mockResolvedValue({
    items: [
      {
        id: "active-v2",
        strategy_id: "s1",
        version: 2,
        card: {},
        validation_status: "approved",
        backtest_status: "passed",
        paper_validation_status: "completed",
        created_at: "2026-10-01T10:00:00Z",
      },
      {
        id: "approved-v3",
        strategy_id: "s1",
        version: 3,
        card: {},
        validation_status: "approved",
        backtest_status: "passed",
        paper_validation_status: "completed",
        created_at: "2026-10-01T10:00:00Z",
      },
    ],
    total: 2,
    limit: 50,
    offset: 0,
  });
  vi.spyOn(api.risk, "settings").mockResolvedValue(risk);
  vi.spyOn(api.risk, "updateSettings").mockRejectedValue(
    new Error("Risk must remain read only"),
  );
  vi.spyOn(api.notifications, "preferences").mockResolvedValue({
    in_app_enabled: true,
    webhook_enabled: false,
    telegram_enabled: false,
    min_severity: "warning",
  });
  vi.spyOn(api.notifications, "updatePreferences").mockResolvedValue({
    in_app_enabled: true,
    webhook_enabled: false,
    telegram_enabled: false,
    min_severity: "critical",
  });
  vi.spyOn(api.notifications, "sendTest").mockRejectedValue(
    new Error("Must not deliver"),
  );
  vi.spyOn(api.alerts, "testTelegram").mockRejectedValue(
    new Error("Must not deliver"),
  );
  vi.spyOn(api.alerts, "deliverPending").mockRejectedValue(
    new Error("Must not deliver"),
  );
  vi.spyOn(api.alerts, "deliveryStatus").mockResolvedValue({
    delivery_enabled: false,
    webhook_enabled: false,
    telegram_enabled: false,
    email_enabled: false,
    push_enabled: false,
    webhook_configured: false,
    effective_external_enabled: false,
    channels: [],
    paper_only: true,
    channel_statuses: [
      {
        channel: "telegram",
        env_enabled: false,
        user_enabled: false,
        configured: true,
        available: false,
        status_label: "disabled",
      },
    ],
  });
  vi.spyOn(api.watcherWatchlist, "configuration").mockResolvedValue({
    revision: 7,
    slots,
    max_enabled: 5,
    paper_only: true,
    updated_at: "2026-10-01T10:00:00Z",
  });
  vi.spyOn(api.watcherWatchlist, "status").mockResolvedValue({
    configuration_revision: 7,
    observed_at: new Date().toISOString(),
    stale_after_seconds: 90,
    paper_only: true,
    real_trading_enabled: false,
    symbols: [],
  });
});

afterEach(() => {
  cleanup();
  window.location.hash = "";
  router.replace.mockClear();
});

it("shows the Account section flat, with Team and Billing reachable and no link row", async () => {
  render(<SettingsPage />);
  expect(screen.getByRole("heading", { name: "Account" })).toBeInTheDocument();
  expect(screen.getByText("owner@example.com")).toBeVisible();
  expect(screen.getByText("Alpha Org")).toBeVisible();
  expect(screen.getByRole("link", { name: "Team" })).toHaveAttribute("href", "/settings/team");
  expect(screen.getByRole("link", { name: "Billing & usage" })).toHaveAttribute("href", "/settings/billing");
  for (const gone of ["Exchange connections", "Diagnostics", "Help & Guide"])
    expect(screen.queryByRole("link", { name: gone })).not.toBeInTheDocument();
  for (const moved of ["Market Monitoring", "Notifications", "Account & System"])
    expect(screen.queryByText(moved, { selector: "summary" })).not.toBeInTheDocument();
  expect(screen.getByText("System", { selector: "summary" }).closest("details")).not.toHaveAttribute("open");
  expect(screen.queryByText("Manual demo preparation & history")).not.toBeInTheDocument();
  expect(api.marketWatcher.monitoring).not.toHaveBeenCalled();
  expect(api.risk.updateSettings).not.toHaveBeenCalled();
});
it("keeps paper account setup in Account", async () => {
  vi.spyOn(api.execution, "registerPaperAccount").mockResolvedValue({
    account: {
      id: "paper-account",
      name: "Paper account",
      execution_mode: "PAPER",
      account_mode: "NET",
      enabled: true,
    },
    created: true,
  });
  render(<SettingsPage />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Set up paper account" }),
  );
  expect(await screen.findByText("paper-account")).toBeInTheDocument();
  expect(api.execution.registerPaperAccount).toHaveBeenCalledExactlyOnceWith();
  expect(api.risk.updateSettings).not.toHaveBeenCalled();
});
it.each([
  ["notifications", "/settings/notifications"],
  ["markets", "/settings/watcher"],
])("forwards the old /settings#%s anchor to %s", (hash, target) => {
  window.location.hash = hash;
  render(<SettingsPage />);
  expect(router.replace).toHaveBeenCalledExactlyOnceWith(target);
});
it("stays on Account for the old #account-system anchor", () => {
  window.location.hash = "account-system";
  render(<SettingsPage />);
  expect(router.replace).not.toHaveBeenCalled();
});
it("shows monitoring state on Watcher without treating watchlist enablement as running", async () => {
  render(<WatcherSettingsPage />);
  expect(screen.getByRole("heading", { name: "Watcher" })).toBeInTheDocument();
  expect(await screen.findByLabelText("Enable slot 1")).toBeChecked();
  expect(screen.getByLabelText("Enable slot 2")).not.toBeChecked();
  expect(screen.getByText("STOPPED")).toBeInTheDocument();
  expect(screen.queryByText("Running")).not.toBeInTheDocument();
});
it("refreshes Watcher monitoring", async () => {
  render(<WatcherSettingsPage />);
  await screen.findByText("STOPPED");
  const previous = vi.mocked(api.marketWatcher.monitoring).mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Refresh watcher" }));
  await waitFor(() =>
    expect(api.marketWatcher.monitoring).toHaveBeenCalledTimes(previous + 1),
  );
});
it("shows the notification controls on Notifications without sending anything", async () => {
  render(<NotificationSettingsPage />);
  expect(screen.getByRole("heading", { name: "Notifications" })).toBeInTheDocument();
  await waitFor(() => expect(api.notifications.preferences).toHaveBeenCalled());
  expect(api.notifications.sendTest).not.toHaveBeenCalled();
  expect(api.alerts.testTelegram).not.toHaveBeenCalled();
  expect(api.notifications.updatePreferences).not.toHaveBeenCalled();
});
it("refreshes system status from Account", async () => {
  render(<SettingsPage />);
  fireEvent.click(screen.getByRole("button", { name: "Refresh system status" }));
  expect(runtime.refreshStatus).toHaveBeenCalledOnce();
});
it("keeps unknown system values unavailable", async () => {
  runtime.health = null;
  runtime.providers = null;
  render(<SettingsPage />);
  expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
});

it("clears monitoring and account state before an authenticated account change reloads", async () => {
  const { rerender } = render(<><SettingsPage /><WatcherSettingsPage /></>);
  await screen.findByText("STOPPED");
  vi.mocked(api.marketWatcher.monitoring).mockImplementation(() => new Promise(() => {}));
  identity = {
    user: { id: "user-2", email: "reader@example.com", email_verified: true },
    organization: { id: "org-2", name: "Other Org" },
  };
  rerender(<><SettingsPage /><WatcherSettingsPage /></>);
  expect(screen.queryByText("STOPPED")).not.toBeInTheDocument();
  expect(screen.getByText("reader@example.com")).toBeInTheDocument();
  expect(screen.queryByText("owner@example.com")).not.toBeInTheDocument();
  expect(api.risk.updateSettings).not.toHaveBeenCalled();
  expect(api.notifications.updatePreferences).not.toHaveBeenCalled();
});
