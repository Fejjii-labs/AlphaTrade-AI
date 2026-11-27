import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationSettingsPanel } from "./NotificationSettingsPanel";
import { telegramPolicyFixture } from "./settings/telegram-policy.fixture";
import { api } from "@/lib/api";
import { makeWatcherMonitoringSnapshot } from "@/lib/watcher-monitoring-fixtures";
import { appConfig } from "@/lib/config";
import type {
  NotificationPreferences,
  TelegramNotificationPolicyV2,
} from "@/lib/api/types";

/** The saved state after setup alerts were muted: event types emptied, the rest kept. */
const mutedPolicy = {
  ...telegramPolicyFixture,
  event_types: [],
  strategy_subscriptions: null,
  symbol_subscriptions: null,
  minimum_quality: 50,
  quiet_hours: null,
} satisfies TelegramNotificationPolicyV2;
const prefs: NotificationPreferences = {
  in_app_enabled: true,
  webhook_enabled: false,
  telegram_enabled: false,
  min_severity: "warning",
  enabled_alert_types: ["setup_signal_detected"],
  quiet_hours_enabled: false,
  timezone: "UTC",
  digest_mode: "disabled",
  telegram_policy: mutedPolicy,
};
const DLINE = [
  "11111111-1111-4111-8111-111111111111",
  "22222222-2222-4222-8222-222222222222",
];
const NESTED = ["33333333-3333-4333-8333-333333333333"];
const options = {
  symbols: [
    { label: "BTCUSDT", values: ["BTCUSDT"] },
    { label: "ETHUSDT", values: ["ETHUSDT"] },
  ],
  strategies: [{ label: "D-line BTCUSDT long 15m 207db2d63d4e", values: DLINE }],
  setup_stages: [{ label: "N2", values: ["N2"] }],
  strategy_families: [
    { label: "D-line", values: DLINE },
    { label: "Nested", values: NESTED },
  ],
};
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}/i;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api.notifications, "preferences").mockResolvedValue(prefs);
  vi.spyOn(api.notifications, "updatePreferences").mockResolvedValue(prefs);
  vi.spyOn(api.notifications, "telegramOptions").mockResolvedValue(options);
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
        user_enabled: true,
        configured: true,
        available: false,
        status_label: "disabled",
      },
    ],
  });
});
afterEach(cleanup);

function lastWrite() {
  return vi.mocked(api.notifications.updatePreferences).mock.calls.at(-1)?.[0];
}
async function mount(saved: NotificationPreferences = prefs) {
  vi.mocked(api.notifications.preferences).mockResolvedValue(saved);
  render(<NotificationSettingsPanel />);
  return screen.findByTestId("setup-alerts-form");
}
function save() {
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
}

describe("Private-chat Telegram enrollment", () => {
  const challenge = {
    challenge_id: "00000000-0000-0000-0000-000000000001",
    expires_at: "2099-01-01T12:15:00Z",
    token: "one-time-private-chat-token",
    bot_id: "123456789",
  };

  afterEach(() => sessionStorage.clear());

  it("starts authenticated enrollment on request and shows the exact private-chat message without claiming verification", async () => {
    sessionStorage.setItem("alphatrade_access_token", "test-enrollment-session");
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(challenge), { status: 200 }),
    );
    const legacyTest = vi.spyOn(api.notifications, "sendTest");
    render(<NotificationSettingsPanel />);
    const connect = await screen.findByRole("button", {
      name: "Connect Telegram",
    });
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(connect);
    expect(
      await screen.findByLabelText("One-time Telegram enrollment token"),
    ).toHaveValue(challenge.token);
    expect(fetch).toHaveBeenCalledExactlyOnceWith(
      new URL("/telegram-paper/enrollment/start", appConfig.apiBaseUrl).toString(),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer test-enrollment-session",
        }),
      }),
    );
    expect(fetch.mock.calls[0][1]?.body).toBeUndefined();
    expect(
      screen.getByText(/as the entire message, without a \/start prefix/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Enrollment is pending/)).toBeInTheDocument();
    expect(screen.getByText(/Expires:/).querySelector("time")).toHaveAttribute(
      "datetime",
      challenge.expires_at,
    );
    expect(legacyTest).not.toHaveBeenCalled();
    expect(api.notifications.updatePreferences).not.toHaveBeenCalled();
    expect(sessionStorage.length).toBe(1);
    expect(localStorage.length).toBe(0);
  });

  it("clears the previous token before a reissue and exposes the API's enrollment blocker", async () => {
    const start = vi
      .spyOn(api.notifications, "startTelegramEnrollment")
      .mockResolvedValueOnce(challenge)
      .mockRejectedValueOnce(new Error("Telegram bot is not configured."));
    render(<NotificationSettingsPanel />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Connect Telegram" }),
    );
    await screen.findByLabelText("One-time Telegram enrollment token");
    fireEvent.click(
      screen.getByRole("button", { name: "Get a new Telegram token" }),
    );
    expect(
      screen.queryByLabelText("One-time Telegram enrollment token"),
    ).not.toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Telegram bot is not configured.",
    );
    expect(start).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Connect Telegram" })).toBeEnabled();
  });
});

describe("Alerts card", () => {
  it("shows plain delivery facts and none of the removed technical controls", async () => {
    const form = await mount();
    const always = screen.getByTestId("always-sent");
    for (const label of ["Trade opened", "Trade closed", "Daily summary"]) {
      expect(within(always).getByText(label).closest("li")).toHaveTextContent("Always sent");
    }
    expect(within(always).getByText("Safety alerts").closest("li")).toHaveTextContent(
      "Always on",
    );
    expect(screen.getByTestId("settings-telegram-state")).toHaveTextContent("Not connected");
    expect(within(form).getByRole("switch", { name: "Setup alerts" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    const text = document.body.textContent ?? "";
    for (const removed of [
      /quality/i,
      /cooldown/i,
      /severity/i,
      /stage/i,
      /Other saved policy filters/,
      /Watcher alert context/,
      /Telegram preference/,
      /Delivery frequency/,
      /Policy V2/,
    ]) {
      expect(text).not.toMatch(removed);
    }
    expect(text).not.toMatch(UUID);
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(api.notifications.updatePreferences).not.toHaveBeenCalled();
  });

  it("turns setup alerts on with the pre-mute defaults and clears hidden blockers", async () => {
    const form = await mount();
    fireEvent.click(within(form).getByRole("switch", { name: "Setup alerts" }));
    save();
    await screen.findByText("Alert settings saved.");
    expect(lastWrite()).toEqual({
      telegram_enabled: true,
      min_severity: "info",
      enabled_alert_types: null,
      digest_mode: "immediate",
      telegram_policy: {
        ...mutedPolicy,
        event_types: null,
        severities: null,
        setup_stages: null,
        minimum_severity: "INFO",
        minimum_quality: null,
        forming_alerts: true,
        confirmed_alerts: true,
        risk_alerts: true,
        paper_trade_opened: true,
        paper_trade_closed: true,
        stop_event: true,
        partial_profit_event: true,
        daily_review_event: true,
        // Repeat limits are kept.
        cooldown_seconds: 120,
        duplicate_suppression_seconds: 86400,
      },
    });
  });

  it("turns setup alerts off by emptying event types and nothing else", async () => {
    const form = await mount({ ...prefs, telegram_policy: telegramPolicyFixture });
    fireEvent.click(within(form).getByRole("switch", { name: "Setup alerts" }));
    save();
    await screen.findByText("Alert settings saved.");
    expect(lastWrite()).toEqual({
      telegram_policy: { ...telegramPolicyFixture, event_types: [] },
    });
  });

  it("keeps the muted state when only quiet hours change, mirroring them to channel routing", async () => {
    const form = await mount();
    fireEvent.click(within(form).getByRole("switch", { name: "Quiet hours" }));
    fireEvent.change(within(form).getByLabelText("Time zone"), {
      target: { value: "Europe/Berlin" },
    });
    save();
    await screen.findByText("Alert settings saved.");
    expect(lastWrite()).toEqual({
      quiet_hours_enabled: true,
      quiet_hours_start: "22:00",
      quiet_hours_end: "07:00",
      timezone: "Europe/Berlin",
      telegram_policy: {
        ...mutedPolicy,
        quiet_hours: { start: "22:00", end: "07:00", timezone: "Europe/Berlin" },
      },
    });
    expect(lastWrite()?.telegram_policy?.event_types).toEqual([]);
  });

  it("rejects an unknown quiet-hours time zone without saving", async () => {
    const form = await mount();
    fireEvent.click(within(form).getByRole("switch", { name: "Quiet hours" }));
    fireEvent.change(within(form).getByLabelText("Time zone"), {
      target: { value: "Mars/Olympus" },
    });
    save();
    await screen.findByText("Enter a time zone such as Europe/Berlin.");
    expect(api.notifications.updatePreferences).not.toHaveBeenCalled();
  });

  it("filters by strategy family and coin by name, storing the identifiers underneath", async () => {
    const form = await mount({
      ...prefs,
      telegram_policy: { ...telegramPolicyFixture, symbol_subscriptions: null },
    });
    const strategies = await within(form).findByRole("group", { name: "Strategies" });
    expect(strategies).toHaveTextContent("D-line");
    expect(strategies).not.toHaveTextContent("207db2d63d4e");
    fireEvent.click(within(strategies).getByRole("button", { name: "D-line" }));
    fireEvent.click(
      within(within(form).getByRole("group", { name: "Coins" })).getByRole("button", {
        name: "ETHUSDT",
      }),
    );
    save();
    await screen.findByText("Alert settings saved.");
    expect(lastWrite()?.telegram_policy).toMatchObject({
      strategy_subscriptions: DLINE,
      symbol_subscriptions: ["ETHUSDT"],
      event_types: telegramPolicyFixture.event_types,
    });
  });

  it("flags a saved strategy filter that no longer matches and lets it be cleared", async () => {
    const form = await mount({
      ...prefs,
      telegram_policy: {
        ...telegramPolicyFixture,
        strategy_subscriptions: ["99999999-9999-4999-8999-999999999999"],
      },
    });
    const note = await within(form).findByRole("note");
    expect(note).toHaveTextContent("no longer matches");
    expect(note).not.toHaveTextContent(UUID);
    fireEvent.click(within(note).getByRole("button", { name: "Allow all" }));
    save();
    await screen.findByText("Alert settings saved.");
    expect(lastWrite()?.telegram_policy?.strategy_subscriptions).toBeNull();
  });

  it("reports a failed save", async () => {
    vi.mocked(api.notifications.updatePreferences).mockRejectedValue(new Error("Forbidden"));
    const form = await mount();
    fireEvent.click(within(form).getByRole("switch", { name: "Setup alerts" }));
    save();
    await screen.findByText("Your changes could not be saved. Please try again.");
    expect(screen.queryByText("Alert settings saved.")).not.toBeInTheDocument();
  });

  it("keeps the alert settings usable when the Telegram status read fails", async () => {
    vi.mocked(api.alerts.deliveryStatus).mockRejectedValueOnce(new Error("offline"));
    await mount();
    await screen.findByRole("button", { name: "Retry telegram status" });
    expect(screen.getByTestId("settings-telegram-state")).toHaveTextContent(
      "Status unavailable",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry telegram status" }));
    await waitFor(() =>
      expect(screen.getByTestId("settings-telegram-state")).toHaveTextContent("Not connected"),
    );
  });

  it("keeps Telegram state and the always-sent list when alert settings fail", async () => {
    vi.mocked(api.notifications.preferences).mockRejectedValue(new Error("offline"));
    render(<NotificationSettingsPanel />);
    await screen.findByText("Alert settings unavailable.");
    expect(screen.getByTestId("always-sent")).toHaveTextContent("Trade opened");
    expect(screen.queryByTestId("setup-alerts-form")).not.toBeInTheDocument();
  });

  it("clears the form if the read after saving fails", async () => {
    vi.mocked(api.notifications.preferences)
      .mockResolvedValueOnce(prefs)
      .mockRejectedValue(new Error("offline"));
    render(<NotificationSettingsPanel />);
    const form = await screen.findByTestId("setup-alerts-form");
    fireEvent.click(within(form).getByRole("switch", { name: "Setup alerts" }));
    save();
    await screen.findByText("Alert settings unavailable.");
    expect(screen.queryByTestId("setup-alerts-form")).not.toBeInTheDocument();
  });
});

describe("Worker Telegram delivery", () => {
  it.each([false, true])("uses worker delivery flags only when fresh (stale: %s)", async (stale) => {
    const snapshot = makeWatcherMonitoringSnapshot({
      worker_effective: {
        source: "worker", health_state: stale ? "STALE" : "RUNNING", stale,
        autonomy_mode: "paper",
        autonomous_internal_paper_env_ceiling: false,
        autonomous_internal_paper_db_enabled: false,
        autonomous_internal_paper_effective: false,
        watcher_orchestration_enabled: true,
        watcher_paper_staging_activation: false,
        telegram_paper_activation_armed: false,
        telegram_interaction_enabled: true,
        telegram_alerts_enabled: false,
        automatic_telegram_delivery_enabled: true,
        watchlist: ["BTCUSDT"], real_trading_enabled: false,
      },
      approved_strategies: [{
        strategy_id: "strategy-1", strategy_version_id: "version-1",
        name: "Nested BTCUSDT long 15m 49ae657c4173", lifecycle_state: "approved", compiled: true,
      }],
    });
    render(<NotificationSettingsPanel snapshot={snapshot} />);
    await screen.findByText(stale ? "Not connected" : "Connected");
    expect(screen.queryByText(/49ae657c4173|Reported markets/)).not.toBeInTheDocument();
  });
});
