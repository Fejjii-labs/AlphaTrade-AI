"use client";

import { useCallback, useState } from "react";

import { TelegramPolicyForm } from "@/components/settings/TelegramPolicyForm";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  SettingsReadout,
  SettingsUnavailable,
} from "@/components/settings/SettingsReadout";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type {
  NotificationPreferences,
  TelegramEnrollmentStartResponse,
  WatcherMonitoringSnapshot,
} from "@/lib/api/types";
import { formatReasonCode } from "@/lib/watcher-monitoring";

export function NotificationSettingsPanel({
  snapshot = null,
}: {
  snapshot?: WatcherMonitoringSnapshot | null;
}) {
  const [busy, setBusy] = useState(false);
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
  const apiDeliveryDisabled = telegram?.status_label === "disabled" ||
    (!telegram && delivery.data?.telegram_enabled === false);

  async function save(patch: Partial<NotificationPreferences>) {
    setBusy(true);
    setMessage(null);
    try {
      await api.notifications.updatePreferences(patch);
      await reload();
      setMessage("Preferences saved.");
    } catch {
      setMessage("Preferences could not be saved. Please try again.");
    } finally {
      setBusy(false);
    }
  }

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
      <CardContent className="space-y-4 text-sm">
        <div
          className="flex flex-wrap items-center gap-2"
          data-testid="settings-telegram-state"
        >
          <span>Telegram delivery</span>
          <StatusBadge
            label={
              workerDeliveryEnabled
                ? "Enabled on worker"
                : delivery.loading
                  ? "Loading…"
                  : delivery.error || !delivery.data
                    ? "Unavailable"
                    : apiDeliveryDisabled
                      ? "API delivery disabled"
                      : telegram
                        ? formatReasonCode(telegram.status_label)
                        : "State unavailable"
            }
            tone={workerDeliveryEnabled || telegram?.available ? "healthy" : "muted"}
          />
        </div>
        <p className="text-xs text-text-muted">
          {telegram
            ? `API delivery configuration: ${telegram.configured ? "configured" : "not configured"}. `
            : ""}
          Delivery status reflects service configuration. Message delivery has not been verified here.
        </p>
        {delivery.error ? (
          <SettingsUnavailable
            label="Telegram state"
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
        {loading || error || !prefs ? (
          <SettingsUnavailable
            label="Alert preferences"
            loading={loading}
            onRetry={() => void reload()}
          />
        ) : (
          <>
            <p className="text-xs text-text-muted">
              Existing channel routing preferences apply alongside Telegram
              Policy V2.
            </p>
            <SettingsReadout
              rows={[
                ["In-app alerts", prefs.in_app_enabled ? "On" : "Off"],
                ["Telegram preference", prefs.telegram_enabled ? "On" : "Off"],
                ["Webhook preference", prefs.webhook_enabled ? "On" : "Off"],
                [
                  "Alert types",
                  prefs.enabled_alert_types === undefined
                    ? "Unavailable"
                    : prefs.enabled_alert_types === null
                      ? "All alert types"
                      : prefs.enabled_alert_types.length
                        ? prefs.enabled_alert_types
                            .map(formatReasonCode)
                            .join(", ")
                        : "None selected",
                ],
                [
                  "Quiet hours",
                  prefs.quiet_hours_enabled === undefined
                    ? "Unavailable"
                    : prefs.quiet_hours_enabled
                      ? `${prefs.quiet_hours_start ?? "Time unavailable"} – ${prefs.quiet_hours_end ?? "Time unavailable"} (${prefs.timezone ?? "Timezone unavailable"})`
                      : "Off",
                ],
                [
                  "Delivery frequency",
                  prefs.digest_mode
                    ? formatReasonCode(prefs.digest_mode)
                    : "Unavailable",
                ],
              ]}
            />
            <label className="flex flex-wrap items-center gap-2 text-text-secondary">
              Minimum alert severity
              <select
                className="rounded border border-border bg-surface-0 px-2 py-1 text-text-primary"
                value={prefs.min_severity}
                disabled={busy}
                onChange={(event) =>
                  void save({ min_severity: event.target.value })
                }
                data-testid="min-severity-select"
              >
                <option value="info">Info</option>
                <option value="warning">Warning</option>
                <option value="critical">Critical</option>
              </select>
            </label>
            {prefs.telegram_policy?.schema_version === 2 ? (
              <TelegramPolicyForm
                key={JSON.stringify([
                  prefs.telegram_policy,
                  prefs.telegram_enabled,
                ])}
                policy={prefs.telegram_policy}
                enabled={prefs.telegram_enabled}
                onSaved={async () => {
                  await reload();
                  setMessage("Telegram policy saved.");
                }}
              />
            ) : (
              <p className="text-xs text-text-muted">
                Telegram Policy V2 settings: Unavailable — not returned by the
                current API.
              </p>
            )}
            {prefs.using_defaults ? (
              <p className="text-xs text-text-muted">
                Using default alert preferences.
              </p>
            ) : null}
          </>
        )}
        <div
          className="space-y-2 border-t border-border-subtle pt-3"
          data-testid="settings-notification-context"
        >
          <p className="font-medium text-text-primary">Watcher alert context</p>
          <SettingsReadout
            rows={[
              [
                "Reported markets",
                snapshot
                  ? snapshot.symbols_monitored.join(", ") ||
                    "No markets reported"
                  : "Unavailable",
              ],
            ]}
          />
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
