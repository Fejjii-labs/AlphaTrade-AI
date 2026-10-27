"use client";

import { FormEvent, type ReactNode, useCallback, useEffect, useState } from "react";

import { BotActivityPanel } from "@/components/autonomous/BotActivityPanel";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { StatusTile, type StatusTone } from "@/components/ui/status-tile";
import { useAuth } from "@/contexts/AuthContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api, ApiError } from "@/lib/api";
import type { AutonomousTradingSettingsView } from "@/lib/api/types";
import { humanizeToken } from "@/lib/format";
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
    max_trades_per_day_exceeds_account: `Bot max trades per day cannot exceed the account limit of ${ceilings.account_max_trades_per_day}.`,
    risk_per_trade_exceeds_ceiling: `Risk per trade cannot exceed ${riskCeiling}% (paper/demo ceiling).`,
    risk_per_trade_non_positive: "Risk per trade must be greater than 0% when set.",
    risk_per_trade_invalid: "Risk per trade must be a number when set.",
    symbol_not_watched: "Choose a coin from your monitoring watchlist.",
    strategy_not_available: "Choose an enabled strategy.",
  };
  return map[code] ?? `Could not save settings (${code}).`;
}

function roleLabel(role: string | null | undefined): string {
  if (!role) return "unknown";
  return role.charAt(0).toUpperCase() + role.slice(1);
}

function onOff(value: boolean | null | undefined): { tone: StatusTone; state: string } {
  if (value == null) return { tone: "unknown", state: "Not reported" };
  return value ? { tone: "ok", state: "On" } : { tone: "unknown", state: "Off" };
}

function workerTone(state: string): StatusTone {
  if (state === "Fresh") return "ok";
  if (state === "Stale") return "warn";
  return "unknown";
}

const selectClass = "min-h-11 w-full rounded-control border border-border-subtle bg-surface-1 px-3";

/**
 * The paper bot: owner switch, status tiles and bot limits. Moved from
 * `/risk/autonomous`. `between` renders between the two cards (account limits),
 * so bot limits read after the account limits they are held to.
 */
export function BotSettings({ between }: { between?: ReactNode } = {}) {
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

  useEffect(() => {
    if (data) {
      setForm(toFormState(data));
      setView(data);
    }
  }, [data]);

  if (loading) return <LoadingState label="Loading autonomous trading settings…" />;
  if (error) return <ErrorState message={error} onRetry={() => void reload()} />;
  if (!form || !data || !view)
    return <ErrorState message="Autonomous trading settings unavailable." onRetry={() => void reload()} />;

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
      setSavedMessage("Bot limits saved.");
    } catch (err) {
      if (err instanceof ApiError && typeof err.message === "string" && data) {
        setSaveError(validationMessage(err.message, data));
      } else {
        setSaveError(err instanceof Error ? err.message : "Unable to save autonomous settings.");
      }
    } finally {
      setSaving(false);
    }
  }

  async function handlePaperSwitch(enabled: boolean) {
    setSwitching(true);
    setSwitchError(null);
    setSavedMessage(null);
    try {
      setView(await api.autonomous.updateSettings({ internal_paper_enabled: enabled }));
    } catch (err) {
      setSwitchError(err instanceof Error ? err.message : "Unable to change the paper switch.");
    } finally {
      setSwitching(false);
    }
  }

  const owner = membershipRole === "owner";
  // PATCH /autonomous/settings is owner-only; say why instead of letting it 403.
  const ownerReason = owner ? null : `Owner only (your role: ${roleLabel(membershipRole)})`;
  const worker = describedWorkerHealth(view.worker_runtime_health, view.worker_flags_stale);

  return (
    <>
      <Card data-testid="autonomous-paper-switch">
        <CardHeader>
          <CardTitle>Paper bot</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <label className="flex min-h-11 items-center gap-2 font-medium">
            <input
              type="checkbox"
              data-testid="autonomous-paper-switch-input"
              checked={view.internal_paper_enabled === true}
              disabled={!owner || switching}
              onChange={(event) => void handlePaperSwitch(event.target.checked)}
            />
            <span>Autonomous internal paper</span>
          </label>
          {ownerReason ? (
            <p className="text-text-muted" data-testid="autonomous-switch-owner-reason">
              {ownerReason}
            </p>
          ) : null}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="autonomous-paper-switch-status">
            <StatusTile name="Environment ceiling" {...onOff(view.internal_paper_env_ceiling)} />
            <StatusTile name="Effective" {...onOff(view.internal_paper_effective ?? null)} />
            <StatusTile name="Worker" tone={workerTone(worker)} state={worker} />
            <StatusTile name="Mode" tone="unknown" state={view.autonomy_mode ? humanizeToken(view.autonomy_mode) : "Not reported"} />
          </div>
          <p className="text-xs text-text-muted" data-testid="autonomous-paper-switch-note">
            Turning it off stops new entries and does not close open positions. It cannot enable real money.
          </p>
          {switchError ? (
            <p className="text-sm text-danger" role="alert">
              {switchError}
            </p>
          ) : null}
        </CardContent>
      </Card>
      {between}
      <Card id="bot" data-testid="autonomous-settings-form-card" className="scroll-mt-4">
        <CardHeader>
          <CardTitle>Bot limits (≤ account)</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={(e) => void handleSubmit(e)}>
            <div className="grid gap-4 md:grid-cols-3">
              <label className="space-y-1 text-sm">
                <span className="text-text-secondary">Bot: max trades/day (≤ account)</span>
                <Input
                  data-testid="autonomous-max-trades"
                  inputMode="numeric"
                  value={form.max_trades_per_day}
                  onChange={(e) => setForm({ ...form, max_trades_per_day: e.target.value })}
                />
                <span className="block text-xs text-text-muted" data-testid="autonomous-effective-max-trades">
                  Effective {view.effective_max_trades_per_day}
                  {view.using_defaults
                    ? " · the bot limit applies once saved"
                    : view.effective_max_trades_per_day < view.max_trades_per_day
                      ? ` (capped by account limit of ${view.account_max_trades_per_day})`
                      : ` · account limit ${view.account_max_trades_per_day}`}
                </span>
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-text-secondary">Daily loss limit (%)</span>
                <Input
                  data-testid="autonomous-daily-loss-percent"
                  inputMode="decimal"
                  value={form.daily_loss_limit_percent}
                  onChange={(e) => setForm({ ...form, daily_loss_limit_percent: e.target.value })}
                />
                <span className="block text-xs text-text-muted">Max {data.max_daily_loss_percent_ceiling}%</span>
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-text-secondary">Risk per trade (%)</span>
                <Input
                  data-testid="autonomous-risk-per-trade"
                  inputMode="decimal"
                  placeholder="Grade-based"
                  value={form.risk_per_trade_percent}
                  onChange={(e) => setForm({ ...form, risk_per_trade_percent: e.target.value })}
                />
                <span className="block text-xs text-text-muted">
                  Max {data.max_risk_per_trade_percent_ceiling}%. Empty uses grade sizing.
                </span>
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-text-secondary">Strategy</span>
                <select
                  data-testid="autonomous-strategy"
                  className={selectClass}
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
              <label className="space-y-1 text-sm">
                <span className="text-text-secondary">Coin (from watchlist)</span>
                <select
                  data-testid="autonomous-symbol"
                  className={selectClass}
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
            </div>
            {saveError ? (
              <p className="text-sm text-danger" role="alert">
                {saveError}
              </p>
            ) : null}
            {savedMessage ? (
              <p className="text-sm text-success" role="status">
                {savedMessage}
              </p>
            ) : null}
            <div className="flex flex-wrap items-baseline gap-3">
              <Button type="submit" disabled={saving || !owner}>
                {saving ? "Saving…" : "Save bot limits"}
              </Button>
              {ownerReason ? (
                <p className="text-sm text-text-muted" data-testid="autonomous-save-owner-reason">
                  {ownerReason}
                </p>
              ) : null}
            </div>
          </form>
        </CardContent>
      </Card>
      <BotActivityPanel view={view} />
    </>
  );
}
