"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api, ApiError } from "@/lib/api";
import type { UserRiskSettings, UserRiskSettingsUpdate } from "@/lib/api/types";

type FormState = {
  daily_loss_limit: string;
  daily_target: string;
  max_trades_per_day: string;
  max_risk_per_trade_percent: string;
  default_account_balance: string;
  timezone: string;
  green_day_protection_enabled: boolean;
  one_loss_stop_enabled: boolean;
  overtrading_guard_enabled: boolean;
  notes: string;
};

function toFormState(settings: UserRiskSettings): FormState {
  return {
    daily_loss_limit: settings.daily_loss_limit ?? "",
    daily_target: settings.daily_target ?? "",
    max_trades_per_day: String(settings.max_trades_per_day),
    max_risk_per_trade_percent: settings.max_risk_per_trade_percent,
    default_account_balance: settings.default_account_balance,
    timezone: settings.timezone,
    green_day_protection_enabled: settings.green_day_protection_enabled,
    one_loss_stop_enabled: settings.one_loss_stop_enabled,
    overtrading_guard_enabled: settings.overtrading_guard_enabled,
    notes: settings.notes ?? "",
  };
}

function toPayload(form: FormState): UserRiskSettingsUpdate {
  return {
    daily_loss_limit: form.daily_loss_limit.trim() ? form.daily_loss_limit.trim() : null,
    daily_target: form.daily_target.trim() ? form.daily_target.trim() : null,
    max_trades_per_day: Number(form.max_trades_per_day),
    max_risk_per_trade_percent: form.max_risk_per_trade_percent.trim(),
    default_account_balance: form.default_account_balance.trim(),
    timezone: form.timezone.trim() || "UTC",
    green_day_protection_enabled: form.green_day_protection_enabled,
    one_loss_stop_enabled: form.one_loss_stop_enabled,
    overtrading_guard_enabled: form.overtrading_guard_enabled,
    notes: form.notes.trim() ? form.notes.trim() : null,
  };
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError || err instanceof Error ? err.message : fallback;
}

/** Account limits: apply to every trade, manual or bot. Moved from `/risk`. */
export function AccountLimitsCard() {
  const loader = useCallback(() => api.risk.settings(), []);
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const [form, setForm] = useState<FormState | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  useEffect(() => {
    if (data) setForm(toFormState(data));
  }, [data]);

  if (loading) return <LoadingState label="Loading risk settings…" />;
  if (error) return <ErrorState message={error} onRetry={() => void reload()} />;
  if (!form) return <ErrorState message="Risk settings unavailable." onRetry={() => void reload()} />;

  async function run(action: () => Promise<UserRiskSettings>, done: string, fallback: string) {
    setSaving(true);
    setSaveError(null);
    setSavedMessage(null);
    try {
      setForm(toFormState(await action()));
      setSavedMessage(done);
    } catch (err) {
      setSaveError(errorText(err, fallback));
    } finally {
      setSaving(false);
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    void run(() => api.risk.updateSettings(toPayload(form)), "Risk settings saved.", "Unable to save risk settings.");
  }

  const field = (label: string, key: keyof FormState, testId: string, extra?: { type?: string; min?: number; placeholder?: string }) => (
    <label className="space-y-1 text-sm">
      <span className="text-text-secondary">{label}</span>
      <Input
        data-testid={testId}
        type={extra?.type}
        min={extra?.min}
        placeholder={extra?.placeholder}
        value={form[key] as string}
        onChange={(e) => setForm({ ...form, [key]: e.target.value })}
      />
    </label>
  );
  const toggle = (label: string, key: keyof FormState, testId: string) => (
    <label className="flex min-h-11 items-center gap-2">
      <input
        type="checkbox"
        data-testid={testId}
        checked={form[key] as boolean}
        onChange={(e) => setForm({ ...form, [key]: e.target.checked })}
      />
      {label}
    </label>
  );

  return (
    <Card id="account" data-testid="risk-settings-form-card" className="scroll-mt-4">
      <CardHeader>
        <CardTitle>Account limits (all trades)</CardTitle>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={handleSubmit}>
          <div className="grid gap-4 md:grid-cols-3">
            {field("Account: max trades/day (all trades)", "max_trades_per_day", "risk-max-trades", { type: "number", min: 1 })}
            {field("Daily loss limit", "daily_loss_limit", "risk-daily-loss-limit", { placeholder: "e.g. 50" })}
            {field("Max risk per trade (%)", "max_risk_per_trade_percent", "risk-max-risk-percent")}
          </div>
          <details className="text-sm">
            <summary className="min-h-11 cursor-pointer text-text-secondary">More (target, balance, timezone)</summary>
            <div className="space-y-4 pt-2">
              <div className="grid gap-4 md:grid-cols-3">
                {field("Daily profit target", "daily_target", "risk-daily-target", { placeholder: "e.g. 100" })}
                {field("Default account balance", "default_account_balance", "risk-default-balance")}
                {field("Timezone", "timezone", "risk-timezone")}
              </div>
              <div className="text-text-secondary">
                {toggle("Green day protection", "green_day_protection_enabled", "risk-green-day")}
                {toggle("One loss stop", "one_loss_stop_enabled", "risk-one-loss-stop")}
                {toggle("Overtrading guard", "overtrading_guard_enabled", "risk-overtrading-guard")}
              </div>
              {field("Notes (optional)", "notes", "risk-notes")}
            </div>
          </details>
          {data?.using_defaults ? (
            <p className="text-xs text-warning" data-testid="risk-using-defaults">
              Using defaults until you save.
            </p>
          ) : null}
          {saveError ? (
            <p className="text-sm text-danger" role="alert" data-testid="risk-settings-error">
              {saveError}
            </p>
          ) : null}
          {savedMessage ? (
            <p className="text-sm text-success" role="status" data-testid="risk-settings-saved">
              {savedMessage}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={saving} data-testid="risk-settings-save">
              Save account limits
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={saving}
              onClick={() =>
                void run(() => api.risk.resetSettings(), "Risk settings reset to defaults.", "Unable to reset risk settings.")
              }
              data-testid="risk-settings-reset"
            >
              Reset defaults
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
