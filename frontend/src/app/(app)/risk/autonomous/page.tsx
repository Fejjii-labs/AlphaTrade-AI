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
  const loader = useCallback(() => api.autonomous.settings(), []);
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const [form, setForm] = useState<FormState | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);
  const { executionMode, realTradingEnabled } = useSafetyPosture();
  const { killSwitchStatus, killSwitchError, loading: appLoading } = useAppContext();
  const posture = describeSafetyPosture(executionMode, realTradingEnabled);
  const block = killSwitchBlockNotice({
    killSwitchStatus,
    killSwitchError,
    killSwitchLoading: appLoading,
  });

  useEffect(() => {
    if (data) setForm(toFormState(data));
  }, [data]);

  if (loading) return <LoadingState label="Loading autonomous trading settings…" />;
  if (error) return <ErrorState message={error} onRetry={() => void reload()} />;
  if (!form || !data)
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
      setSavedMessage("Autonomous trading settings saved.");
    } catch (err) {
      if (err instanceof ApiError && typeof err.message === "string") {
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
