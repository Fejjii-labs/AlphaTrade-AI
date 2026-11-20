import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationSettingsPanel } from "./NotificationSettingsPanel";
import { telegramPolicyFixture } from "./settings/telegram-policy.fixture";
import { api } from "@/lib/api";
import { makeWatcherMonitoringSnapshot } from "@/lib/watcher-monitoring-fixtures";
import { appConfig } from "@/lib/config";
import type { NotificationPreferences } from "@/lib/api/types";

const prefs: NotificationPreferences = {
  in_app_enabled: true,
  webhook_enabled: false,
  telegram_enabled: true,
  min_severity: "info",
  enabled_alert_types: ["setup_detected", "risk_warning"],
  quiet_hours_enabled: true,
  quiet_hours_start: "22:00",
  quiet_hours_end: "07:00",
  timezone: "Europe/Berlin",
  digest_mode: "immediate",
};
beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api.notifications, "preferences").mockResolvedValue(prefs);
  vi.spyOn(api.notifications, "updatePreferences").mockResolvedValue(prefs);
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

describe("Trader notification settings", () => {
  it("distinguishes saved Telegram preferences from delivery availability and shows existing alert preferences", async () => {
    render(<NotificationSettingsPanel />);
    await screen.findByLabelText("Minimum alert severity");
    expect(screen.getByTestId("settings-telegram-state")).toHaveTextContent(
      "API delivery disabled",
    );
    expect(
      screen.getByText("Telegram preference").parentElement,
    ).toHaveTextContent("On");
    expect(screen.getByText("Alert types").parentElement).toHaveTextContent(
      "setup detected, risk warning",
    );
    expect(screen.getByText("Quiet hours").parentElement).toHaveTextContent(
      "22:00 – 07:00 (Europe/Berlin)",
    );
    expect(
      screen.getByText("Delivery frequency").parentElement,
    ).toHaveTextContent("immediate");
    expect(
      screen.getByText(/Message delivery has not been verified here/),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("notifications-never-trade-copy")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /test/i }),
    ).not.toBeInTheDocument();
  });

  it("labels omitted quiet hours and delivery frequency as unavailable", async () => {
    vi.mocked(api.notifications.preferences).mockResolvedValue({
      in_app_enabled: true,
      webhook_enabled: false,
      telegram_enabled: false,
      min_severity: "warning",
    });
    render(<NotificationSettingsPanel />);
    await screen.findByLabelText("Minimum alert severity");
    expect(screen.getByText("Quiet hours").parentElement).toHaveTextContent(
      "Unavailable",
    );
    expect(
      screen.getByText("Delivery frequency").parentElement,
    ).toHaveTextContent("Unavailable");
    expect(
      screen.getByTestId("settings-notification-context"),
    ).toHaveTextContent("Unavailable");
  });

  it("shows all alert types only when the API explicitly returns a null filter", async () => {
    vi.mocked(api.notifications.preferences).mockResolvedValue({
      ...prefs,
      enabled_alert_types: null,
    });
    render(<NotificationSettingsPanel />);
    await screen.findByLabelText("Minimum alert severity");
    expect(screen.getByText("Alert types").parentElement).toHaveTextContent(
      "All alert types",
    );
  });

  it("preserves an explicitly empty alert-type preference", async () => {
    vi.mocked(api.notifications.preferences).mockResolvedValue({
      ...prefs,
      enabled_alert_types: [],
    });
    render(<NotificationSettingsPanel />);
    await screen.findByLabelText("Minimum alert severity");
    expect(screen.getByText("Alert types").parentElement).toHaveTextContent(
      "None selected",
    );
  });

  it("keeps preferences available when delivery status fails and retries the read", async () => {
    vi.mocked(api.alerts.deliveryStatus).mockRejectedValueOnce(
      new Error("offline"),
    );
    render(<NotificationSettingsPanel />);
    await screen.findByRole("button", { name: "Retry telegram state" });
    expect(screen.getByLabelText("Minimum alert severity")).toBeEnabled();
    expect(screen.getByTestId("settings-telegram-state")).toHaveTextContent(
      "Unavailable",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Retry telegram state" }),
    );
    await screen.findByText("API delivery disabled");
  });

  it("keeps Telegram state visible when alert preferences fail", async () => {
    vi.mocked(api.notifications.preferences).mockRejectedValue(
      new Error("offline"),
    );
    render(<NotificationSettingsPanel />);
    await screen.findByText("Alert preferences unavailable.");
    expect(screen.getByTestId("settings-telegram-state")).toHaveTextContent(
      "API delivery disabled",
    );
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("reports a failed save without displaying an unsaved preference", async () => {
    vi.mocked(api.notifications.updatePreferences).mockRejectedValue(
      new Error("Forbidden"),
    );
    render(<NotificationSettingsPanel />);
    const select = await screen.findByLabelText("Minimum alert severity");
    fireEvent.change(select, { target: { value: "critical" } });
    await screen.findByText(
      "Preferences could not be saved. Please try again.",
    );
    expect(select).toHaveValue("info");
    expect(select).toBeEnabled();
    expect(screen.queryByText("Preferences saved.")).not.toBeInTheDocument();
  });

  it("clears preferences when the refresh after a successful save fails", async () => {
    vi.mocked(api.notifications.preferences)
      .mockResolvedValueOnce(prefs)
      .mockRejectedValue(new Error("offline"));
    render(<NotificationSettingsPanel />);
    fireEvent.change(await screen.findByLabelText("Minimum alert severity"), {
      target: { value: "critical" },
    });
    await screen.findByText("Alert preferences unavailable.");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});

describe("Policy V2 settings integration", () => {
  it("loads the backend policy, refreshes the saved values and never exposes credentials", async () => {
    const initial = {
      ...prefs,
      telegram_policy: telegramPolicyFixture,
      telegram_chat_id: "private-recipient",
    };
    const updated = {
      ...initial,
      telegram_policy: {
        ...telegramPolicyFixture,
        minimum_severity: "ACTION" as const,
      },
    };
    vi.mocked(api.notifications.preferences)
      .mockResolvedValueOnce(initial)
      .mockResolvedValue(updated);
    render(<NotificationSettingsPanel />);
    fireEvent.change(
      await screen.findByLabelText("Minimum Telegram severity"),
      { target: { value: "ACTION" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Save Telegram policy" }),
    );
    await screen.findByText("Telegram policy saved.");
    expect(screen.getByLabelText("Minimum Telegram severity")).toHaveValue(
      "ACTION",
    );
    expect(screen.queryByText("private-recipient")).not.toBeInTheDocument();
    expect(screen.getByTestId("settings-telegram-state")).toHaveTextContent(
      "API delivery disabled",
    );
    expect(api.notifications.updatePreferences).toHaveBeenCalledExactlyOnceWith(
      { telegram_enabled: true, telegram_policy: updated.telegram_policy },
    );
  });

  it("labels absent V2 support unavailable and does not offer its controls", async () => {
    render(<NotificationSettingsPanel />);
    await screen.findByText(/Telegram Policy V2 settings: Unavailable/);
    expect(screen.queryByTestId("telegram-policy-v2")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save Telegram policy" }),
    ).not.toBeInTheDocument();
  });

  it("clears stale V2 controls if the read after saving fails", async () => {
    vi.mocked(api.notifications.preferences)
      .mockResolvedValueOnce({
        ...prefs,
        telegram_policy: telegramPolicyFixture,
      })
      .mockRejectedValue(new Error("offline"));
    render(<NotificationSettingsPanel />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Save Telegram policy" }),
    );
    await screen.findByText("Alert preferences unavailable.");
    await waitFor(() =>
      expect(
        screen.queryByTestId("telegram-policy-v2"),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("settings-telegram-state")).toHaveTextContent(
      "API delivery disabled",
    );
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
    await screen.findByText(stale ? "API delivery disabled" : "Enabled on worker");
    expect(screen.queryByText(/Reported strategies|49ae657c4173/)).not.toBeInTheDocument();
    expect(screen.getByText("Reported markets")).toBeInTheDocument();
  });
});
