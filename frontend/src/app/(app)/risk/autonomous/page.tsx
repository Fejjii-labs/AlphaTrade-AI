"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { killSwitchBlockNotice, PortfolioHubChrome } from "@/components/portfolio";
import { describeSafetyPosture } from "@/components/workflows";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAppContext, useSafetyPosture } from "@/contexts/AppContext";
import { useAuth } from "@/contexts/AuthContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api, ApiError } from "@/lib/api";
import type { AutonomousTradingSettingsView } from "@/lib/api/types";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";
import { describedWorkerHealth } from "@/lib/worker-runtime";

type FormState = {
  daily_loss_limit_percent: string;
  max_trades_per_day: string;
  risk_per_trade_percent: string;
  strategy_id: string;
  symbol: string;
};

function toFormState(data: AutonomousTradingSettingsView): FormState {
  return {
    daily_loss_limit_percent: data.daily_loss_limit_percent,
    max_trades_per_day: String(data.max_trades_per_day),
    risk_per_trade_percent: data.risk_per_trade_percent ?? "",
    strategy_id: data.strategy_id ?? "",
    symbol: data.symbol ?? "",
  };
}

function validationMessage(code: string, ceilings: AutonomousTradingSettingsView): string {
  const dailyCeiling = ceilings.max_daily_loss_percent_ceiling;
  const riskCeiling = ceilings.max_risk_per_trade_percent_ceiling;
  const map: Record<string, string> = {
    daily_loss_limit_exceeds_ceiling: `Daily loss limit cannot exceed ${dailyCeiling}% (code ceiling).`,
    daily_loss_limit_non_positive: "Daily loss limit must be greater than 0%.",
    daily_loss_limit_invalid: "Daily loss limit must be a number.",
    max_trades_per_day_non_positive: "Max trades per day must be at least 1.",
    max_trades_per_day_invalid: "Max trades per day must be a whole number.",
    risk_per_trade_exceeds_ceiling: `Risk per trade cannot exceed ${riskCeiling}% (paper/demo ceiling).`,
    risk_per_trade_non_positive: "Risk per trade must be greater than 0% when set.",
    risk_per_trade_invalid: "Risk per trade must be a number when set.",
    symbol_not_watched: "Choose a coin from your monitoring watchlist.",
    strategy_not_available: "Choose an enabled strategy.",
  };
  return map[code] ?? `Could not save settings (${code}).`;
}

export default function AutonomousTradingSettingsPage() {
  const { user, organization } = useAuth();
  const [session, setSession] = useState(sessionGeneration);
  useEffect(() => onSessionCleared(() => setSession(sessionGeneration())), []);
  if (!user || !organization) return null;
  return (
    <AutonomousSettingsWorkspace
      key={JSON.stringify([organization.id, user.id, session])}
    />
  );
}

function AutonomousSettingsWorkspace() {
  const { membershipRole } = useAuth();
  const loader = useCallback(() => api.autonomous.settings(), []);
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const [form, setForm] = useState<FormState | null>(null);
  const [view, setView] = useState<AutonomousTradingSettingsView | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  const { executionMode, realTradingEnabled } = useSafetyPosture();
  const { killSwitchStatus, killSwitchError, loading: appLoading } = useAppContext();
  const posture = describeSafetyPosture(executionMode, realTradingEnabled);
  const block = killSwitchBlockNotice({
    killSwitchStatus,
    killSwitchError,
    killSwitchLoading: appLoading,
  });

  useEffect(() => {
    if (data) {
      setForm(toFormState(data));
      setView(data);
    }
  }, [data]);

  if (loading) return <LoadingState label="Loading autonomous trading settings…" />;
  if (error) return <ErrorState message={error} onRetry={() => void reload()} />;
  if (!form || !data || !view)
    return (
      <ErrorState
        message="Autonomous trading settings unavailable."
        onRetry={() => void reload()}
      />
    );

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setSaveError(null);
    setSavedMessage(null);
    setSwitchError(null);
    const riskText = form!.risk_per_trade_percent.trim();
    const payload: Record<string, unknown> = {
      daily_loss_limit_percent: form!.daily_loss_limit_percent.trim(),
      max_trades_per_day: Number(form!.max_trades_per_day),
      strategy_id: form!.strategy_id || null,
      symbol: form!.symbol || null,
    };
    if (!riskText) {
      payload.clear_risk_per_trade_percent = true;
    } else {
      payload.risk_per_trade_percent = riskText;
    }
    try {
      const updated = await api.autonomous.updateSettings(payload);
      setForm(toFormState(updated));
      setView(updated);
      setSavedMessage("Autonomous trading settings saved.");
    } catch (err) {
      if (err instanceof ApiError && typeof err.message === "string" && data) {
        setSaveError(validationMessage(err.message, data));
      } else {
        setSaveError(
          err instanceof Error ? err.message : "Unable to save autonomous settings.",
        );
      }
    } finally {
      setSaving(false);
    }
  }

  const owner = membershipRole === "owner";
  const ceiling =
    view.internal_paper_env_ceiling == null
      ? "Not reported"
      : view.internal_paper_env_ceiling
        ? "On"
        : "Off";

  async function handlePaperSwitch(enabled: boolean) {
    setSwitching(true);
    setSwitchError(null);
    setSavedMessage(null);
    try {
      const updated = await api.autonomous.updateSettings({ internal_paper_enabled: enabled });
      setView(updated);
      setSavedMessage(
        enabled ? "Internal paper switch turned on." : "Internal paper switch turned off.",
      );
    } catch (err) {
      setSwitchError(err instanceof Error ? err.message : "Unable to change the paper switch.");
    } finally {
      setSwitching(false);
    }
  }

  return (
    <PortfolioHubChrome
      title="Autonomous trading"
      description="Limits and strategy/coin selection for autonomous paper and demo paths."
      posture={posture}
      riskBlocked={block.blocked}
      riskBlockReason={block.reason}
      testId="autonomous-settings-page"
    >
      <p className="text-sm text-text-muted" data-testid="autonomous-settings-ownership">
        Configuration only. Changes apply to the next autonomous decision. Real-money
        execution stays blocked.{" "}
        <Link href="/strategies#risk" className="text-accent">
          ← Back to Strategies
        </Link>
      </p>
      <Card data-testid="autonomous-paper-switch">
        <CardHeader>
          <CardTitle>Internal paper switch</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <label className="flex min-h-11 items-center gap-2">
            <input
              type="checkbox"
              data-testid="autonomous-paper-switch-input"
              checked={view.internal_paper_enabled === true}
              disabled={!owner || switching}
              onChange={(event) => void handlePaperSwitch(event.target.checked)}
            />
            <span>Autonomous internal paper</span>
          </label>
          <p className="text-text-muted" data-testid="autonomous-paper-switch-note">
            The environment ceiling wins when it is off. This switch cannot enable real money.
            Only an owner can change it. Turning it off stops new entries and does not close
            open positions.
          </p>
          <dl className="grid gap-2 sm:grid-cols-2" data-testid="autonomous-paper-switch-status">
            <div>
              <dt className="text-xs text-text-muted">Environment ceiling</dt>
              <dd>{ceiling}</dd>
            </div>
            <div>
              <dt className="text-xs text-text-muted">Effective for this workspace</dt>
              <dd>{view.internal_paper_effective ? "On" : "Off"}</dd>
            </div>
            <div>
              <dt className="text-xs text-text-muted">Worker report</dt>
              <dd>
                {describedWorkerHealth(view.worker_runtime_health, view.worker_flags_stale)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-text-muted">Last worker mode</dt>
              <dd>{view.autonomy_mode ? view.autonomy_mode : "Not reported"}</dd>
            </div>
          </dl>
          {switchError ? (
            <p className="text-sm text-destructive" role="alert">
              {switchError}
            </p>
          ) : null}
        </CardContent>
      </Card>
      <Card data-testid="autonomous-settings-form-card">
        <CardHeader>
          <CardTitle>Limits and selection</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={(e) => void handleSubmit(e)}>
            <label className="block space-y-1 text-sm">
              <span>Daily loss limit (% of portfolio)</span>
              <Input
                data-testid="autonomous-daily-loss-percent"
                inputMode="decimal"
                value={form.daily_loss_limit_percent}
                onChange={(e) =>
                  setForm({ ...form, daily_loss_limit_percent: e.target.value })
                }
              />
              <span className="text-text-muted">
                Code ceiling: {data.max_daily_loss_percent_ceiling}%
              </span>
            </label>
            <label className="block space-y-1 text-sm">
              <span>Max trades per day</span>
              <Input
                data-testid="autonomous-max-trades"
                inputMode="numeric"
                value={form.max_trades_per_day}
                onChange={(e) => setForm({ ...form, max_trades_per_day: e.target.value })}
              />
            </label>
            <label className="block space-y-1 text-sm">
              <span>Risk per trade % (optional override)</span>
              <Input
                data-testid="autonomous-risk-per-trade"
                inputMode="decimal"
                placeholder="Leave empty for grade-based sizing"
                value={form.risk_per_trade_percent}
                onChange={(e) =>
                  setForm({ ...form, risk_per_trade_percent: e.target.value })
                }
              />
              <span className="text-text-muted">
                Paper/demo ceiling: {data.max_risk_per_trade_percent_ceiling}%. Empty uses
                grade rules (A+ up to 5%, etc.).
              </span>
            </label>
            <label className="block space-y-1 text-sm">
              <span>Strategy</span>
              <select
                data-testid="autonomous-strategy"
                className="min-h-11 w-full rounded-md border border-border-subtle bg-surface-1 px-3"
                value={form.strategy_id}
                onChange={(e) => setForm({ ...form, strategy_id: e.target.value })}
              >
                <option value="">Select strategy</option>
                {data.strategy_options.map((s) => (
                  <option key={s.strategy_id} value={s.strategy_id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1 text-sm">
              <span>Coin (from watchlist)</span>
              <select
                data-testid="autonomous-symbol"
                className="min-h-11 w-full rounded-md border border-border-subtle bg-surface-1 px-3"
                value={form.symbol}
                onChange={(e) => setForm({ ...form, symbol: e.target.value })}
              >
                <option value="">Select coin</option>
                {data.symbol_options.map((symbol) => (
                  <option key={symbol} value={symbol}>
                    {symbol}
                  </option>
                ))}
              </select>
            </label>
            {saveError ? (
              <p className="text-sm text-destructive" role="alert">{saveError}</p>
            ) : null}
            {savedMessage ? (
              <p className="text-sm text-accent" role="status">{savedMessage}</p>
            ) : null}
            <Button type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save settings"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </PortfolioHubChrome>
  );
}
