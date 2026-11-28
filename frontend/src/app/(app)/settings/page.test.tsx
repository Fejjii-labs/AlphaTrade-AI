import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import SettingsPage from "./page";
import AlertsSettingsPage from "./alerts/page";
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
const logout = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ ...identity, logout }),
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

it("shows Account as email, workspace and log out only", async () => {
  render(<SettingsPage />);
  expect(screen.getByRole("heading", { level: 1, name: "Account" })).toBeInTheDocument();
  expect(screen.getByText("owner@example.com")).toBeVisible();
  expect(screen.getByText("Alpha Org")).toBeVisible();
  for (const gone of ["Team", "Billing & usage", "Exchange connections", "Diagnostics", "Help & Guide"])
    expect(screen.queryByRole("link", { name: gone })).not.toBeInTheDocument();
  expect(screen.queryByText(/verif/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Set up paper account" })).not.toBeInTheDocument();
  expect(api.execution.paperAccountStatus).not.toHaveBeenCalled();
  expect(api.marketWatcher.monitoring).not.toHaveBeenCalled();
  expect(api.risk.updateSettings).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Log out" }));
  expect(logout).toHaveBeenCalledOnce();
});
it.each([
  ["notifications", "/settings/alerts"],
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
it("shows the scanner from the worker heartbeat on Markets, not the old per-coin assignment status", async () => {
  render(<WatcherSettingsPage />);
  expect(screen.getByRole("heading", { name: "Markets" })).toBeInTheDocument();
  expect(await screen.findByLabelText("Enable slot 1")).toBeChecked();
  expect(screen.getByLabelText("Enable slot 2")).not.toBeChecked();
  expect(screen.getByTestId("scanner-status")).toHaveTextContent("Scanner status not reported yet");
  for (const gone of ["Stopped", "Stale", "Coin strategies", "Pending / unscanned"])
    expect(screen.queryByText(gone, { exact: false })).not.toBeInTheDocument();
  expect(api.marketWatcher.monitoring).not.toHaveBeenCalled();
});
it("says Scanning with the last scan time when the worker heartbeat is fresh", async () => {
  const beat = new Date(Date.now() - 120_000).toISOString();
  runtime.health = {
    ...runtime.health,
    worker_runtime: {
      available: true,
      watcher: { health_state: "RUNNING", activation_state: "running", last_scan_at: beat },
      paper_worker: { health_state: "RUNNING", activation_state: "running", heartbeat_at: beat },
    },
  } as HealthResponse;
  render(<WatcherSettingsPage />);
  expect(screen.getByTestId("scanner-status")).toHaveTextContent("Scanning · last scan 2 min ago");
  expect(screen.getByTestId("scanner-status")).toHaveAttribute("data-tone", "ok");
});
it("refreshes the Markets scanner status", async () => {
  render(<WatcherSettingsPage />);
  await screen.findByLabelText("Enable slot 1");
  fireEvent.click(screen.getByRole("button", { name: "Refresh markets" }));
  await waitFor(() => expect(runtime.refreshStatus).toHaveBeenCalledOnce());
});
it("shows the notification controls on Alerts without sending anything", async () => {
  render(<AlertsSettingsPage />);
  expect(screen.getByRole("heading", { level: 1, name: "Alerts" })).toBeInTheDocument();
  await waitFor(() => expect(api.notifications.preferences).toHaveBeenCalled());
  expect(api.notifications.sendTest).not.toHaveBeenCalled();
  expect(api.alerts.testTelegram).not.toHaveBeenCalled();
  expect(api.notifications.updatePreferences).not.toHaveBeenCalled();
});
it("keeps unknown account values unavailable", async () => {
  identity = { user: { id: "user-1", email: "", email_verified: true }, organization: { id: "org-1", name: "" } };
  render(<SettingsPage />);
  expect(screen.getAllByText("Unavailable").length).toBe(2);
});

it("clears market and account state before an authenticated account change reloads", async () => {
  const { rerender } = render(<><SettingsPage /><WatcherSettingsPage /></>);
  await screen.findByLabelText("Enable slot 1");
  vi.mocked(api.watcherWatchlist.configuration).mockImplementation(() => new Promise(() => {}));
  identity = {
    user: { id: "user-2", email: "reader@example.com", email_verified: true },
    organization: { id: "org-2", name: "Other Org" },
  };
  rerender(<><SettingsPage /><WatcherSettingsPage /></>);
  expect(screen.queryByLabelText("Enable slot 1")).not.toBeInTheDocument();
  expect(screen.getByText("reader@example.com")).toBeInTheDocument();
  expect(screen.queryByText("owner@example.com")).not.toBeInTheDocument();
  expect(api.risk.updateSettings).not.toHaveBeenCalled();
  expect(api.notifications.updatePreferences).not.toHaveBeenCalled();
});
