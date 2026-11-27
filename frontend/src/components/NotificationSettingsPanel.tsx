"use client";

import { useCallback, useState } from "react";

import { SetupAlertsForm } from "@/components/settings/SetupAlertsForm";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { SettingsUnavailable } from "@/components/settings/SettingsReadout";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type {
  TelegramEnrollmentStartResponse,
  WatcherMonitoringSnapshot,
} from "@/lib/api/types";

/**
 * Demo trade pages, the daily summary and safety pages go out on the server's
 * safety-alert path; no preference on this page filters them.
 */
const ALWAYS_SENT: [string, string, string][] = [
  ["Trade opened", "Coin, direction, entry, stop and risk.", "Always sent"],
  ["Trade closed", "Result in dollars and R.", "Always sent"],
  ["Daily summary", "Yesterday's trades, plus a weekly and monthly review.", "Always sent"],
  [
    "Safety alerts",
    "Problems that need you, such as a trade without its stop.",
    "Always on",
  ],
];

export function NotificationSettingsPanel({
  snapshot = null,
}: {
  snapshot?: WatcherMonitoringSnapshot | null;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const [enrolling, setEnrolling] = useState(false);
  const [enrollment, setEnrollment] =
    useState<TelegramEnrollmentStartResponse | null>(null);
  const [enrollmentError, setEnrollmentError] = useState<string | null>(null);
  const loader = useCallback(() => api.notifications.preferences(), []);
  const statusLoader = useCallback(() => api.alerts.deliveryStatus(), []);
  const { data: prefs, loading, error, reload } = useAsyncData(loader, []);
  const delivery = useAsyncData(statusLoader, []);
  const telegram = delivery.data?.channel_statuses?.find(
    (channel) => channel.channel === "telegram",
  );

  const worker = snapshot?.worker_effective;
  const workerDeliveryEnabled = worker?.source === "worker" &&
    worker.health_state === "RUNNING" && !worker.stale &&
    (worker.automatic_telegram_delivery_enabled || worker.telegram_alerts_enabled ||
      (worker.telegram_paper_activation_armed && worker.telegram_interaction_enabled));
  const connected = workerDeliveryEnabled || telegram?.available === true;
  const policy =
    prefs?.telegram_policy?.schema_version === 2 ? prefs.telegram_policy : null;

  async function startEnrollment() {
    setEnrolling(true);
    setEnrollment(null);
    setEnrollmentError(null);
    try {
      setEnrollment(await api.notifications.startTelegramEnrollment());
    } catch (err) {
      setEnrollmentError(
        err instanceof Error
          ? err.message
          : "Telegram enrollment could not be started.",
      );
    } finally {
      setEnrolling(false);
    }
  }

  return (
    <Card data-testid="notification-settings-panel">
      <CardContent className="space-y-5 pt-4 text-sm lg:pt-5">
        <section className="space-y-3" aria-labelledby="telegram-title">
          <div
            className="flex flex-wrap items-center gap-2"
            data-testid="settings-telegram-state"
          >
            <h3 id="telegram-title" className="font-medium text-text-primary">
              Telegram
            </h3>
            <StatusBadge
              label={
                connected
                  ? "Connected"
                  : delivery.loading
                    ? "Checking…"
                    : delivery.error || !delivery.data
                      ? "Status unavailable"
                      : "Not connected"
              }
              tone={connected ? "healthy" : "muted"}
            />
          </div>
          {delivery.error ? (
            <SettingsUnavailable
              label="Telegram status"
              loading={false}
              onRetry={() => void delivery.reload()}
            />
          ) : null}
          <div className="space-y-2" data-testid="telegram-enrollment">
            <Button
              variant="secondary"
              disabled={enrolling}
              onClick={() => void startEnrollment()}
            >
              {enrolling
                ? "Starting enrollment…"
                : enrollment
                  ? "Get a new Telegram token"
                  : "Connect Telegram"}
            </Button>
            {enrollment ? (
              <div className="space-y-2">
                <p className="text-xs text-text-secondary">
                  Open your AlphaTrade bot in a private Telegram chat and send the
                  token below as the entire message, without a /start prefix.
                </p>
                <label
                  htmlFor="telegram-enrollment-token"
                  className="block text-xs text-text-secondary"
                >
                  One-time Telegram enrollment token
                </label>
                <input
                  id="telegram-enrollment-token"
                  className="w-full rounded border border-border bg-surface-0 px-2 py-1 font-mono text-text-primary"
                  value={enrollment.token}
                  readOnly
                  autoComplete="off"
                  onFocus={(event) => event.currentTarget.select()}
                />
                <p className="text-xs text-text-muted">
                  Expires:{" "}
                  <time dateTime={enrollment.expires_at}>
                    {new Date(enrollment.expires_at).toLocaleString()}
                  </time>
                  . Enrollment is pending; this token does not confirm a connection.
                </p>
              </div>
            ) : null}
            {enrollmentError ? (
              <p role="alert" className="text-xs text-text-secondary">
                {enrollmentError}
              </p>
            ) : null}
          </div>
        </section>

        <section
          className="space-y-2 border-t border-border-subtle pt-4"
          aria-labelledby="receive-title"
          data-testid="always-sent"
        >
          <h3 id="receive-title" className="font-medium text-text-primary">
            What you receive
          </h3>
          <ul className="divide-y divide-border-subtle">
            {ALWAYS_SENT.map(([label, detail, state]) => (
              <li key={label} className="flex items-start justify-between gap-4 py-2.5">
                <div className="space-y-0.5">
                  <p className="text-text-primary">{label}</p>
                  <p className="text-xs text-text-muted">{detail}</p>
                </div>
                <StatusBadge label={state} tone="healthy" className="shrink-0" />
              </li>
            ))}
          </ul>
          <p className="text-xs text-text-muted">
            These are set on the server for the demo account. The settings below
            do not change them.
          </p>
        </section>

        <div className="border-t border-border-subtle pt-4">
          {loading || error || !prefs ? (
            <SettingsUnavailable
              label="Alert settings"
              loading={loading}
              onRetry={() => void reload()}
            />
          ) : policy ? (
            <SetupAlertsForm
              key={JSON.stringify(prefs)}
              prefs={prefs}
              policy={policy}
              onSaved={async () => {
                await reload();
                setMessage("Alert settings saved.");
              }}
            />
          ) : (
            <p className="text-xs text-text-muted">
              Setup alert settings are not available right now.
            </p>
          )}
        </div>
        {message ? (
          <p role="status" className="text-xs text-text-secondary">
            {message}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
