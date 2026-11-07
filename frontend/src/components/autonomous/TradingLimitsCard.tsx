"use client";

import Link from "next/link";
import { FormEvent, type ReactNode, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import type { AutonomousTradingSettingsView, UserRiskSettings, UserRiskSettingsUpdate } from "@/lib/api/types";

import { type Bounds, boundHint, inputLimits } from "./bounds";

type BotForm = {
  daily_loss_limit_percent: string;
  max_trades_per_day: string;
  risk_per_trade_percent: string;
  strategy_id: string;
  symbol: string;
};

type AccountForm = {
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

function toBotForm(data: AutonomousTradingSettingsView): BotForm {
  return {
    daily_loss_limit_percent: data.daily_loss_limit_percent,
    max_trades_per_day: String(data.max_trades_per_day),
    risk_per_trade_percent: data.risk_per_trade_percent ?? "",
    strategy_id: data.strategy_id ?? "",
    symbol: data.symbol ?? "",
  };
}

function toAccountForm(settings: UserRiskSettings): AccountForm {
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

const optional = (value: string) => (value.trim() ? value.trim() : null);

function toAccountPayload(form: AccountForm): UserRiskSettingsUpdate {
  return {
    daily_loss_limit: optional(form.daily_loss_limit),
    daily_target: optional(form.daily_target),
    max_trades_per_day: Number(form.max_trades_per_day),
    max_risk_per_trade_percent: form.max_risk_per_trade_percent.trim(),
    default_account_balance: form.default_account_balance.trim(),
    timezone: form.timezone.trim() || "UTC",
    green_day_protection_enabled: form.green_day_protection_enabled,
    one_loss_stop_enabled: form.one_loss_stop_enabled,
    overtrading_guard_enabled: form.overtrading_guard_enabled,
    notes: optional(form.notes),
  };
}

function toBotPayload(form: BotForm): Record<string, unknown> {
  const risk = form.risk_per_trade_percent.trim();
  return {
    daily_loss_limit_percent: form.daily_loss_limit_percent.trim(),
    max_trades_per_day: Number(form.max_trades_per_day),
    strategy_id: form.strategy_id || null,
    symbol: form.symbol || null,
    ...(risk ? { risk_per_trade_percent: risk } : { clear_risk_per_trade_percent: true }),
  };
}

export function botValidationMessage(code: string, ceilings: AutonomousTradingSettingsView): string {
  const map: Record<string, string> = {
    daily_loss_limit_exceeds_ceiling: `Bot daily loss limit cannot exceed ${ceilings.max_daily_loss_percent_ceiling}% (code ceiling).`,
    daily_loss_limit_non_positive: "Bot daily loss limit must be greater than 0%.",
    daily_loss_limit_invalid: "Bot daily loss limit must be a number.",
    max_trades_per_day_non_positive: "Bot max trades per day must be at least 1.",
    max_trades_per_day_invalid: "Bot max trades per day must be a whole number.",
    max_trades_per_day_exceeds_account: `Bot max trades per day cannot exceed the account limit of ${ceilings.account_max_trades_per_day}.`,
    risk_per_trade_exceeds_ceiling: `Bot risk per trade cannot exceed ${ceilings.max_risk_per_trade_percent_ceiling}% (paper/demo ceiling).`,
    risk_per_trade_non_positive: "Bot risk per trade must be greater than 0% when set.",
    risk_per_trade_invalid: "Bot risk per trade must be a number when set.",
    symbol_not_watched: "Choose a coin from your monitoring watchlist.",
    strategy_not_available: "Choose an enabled strategy.",
  };
  return map[code] ?? `Could not save bot settings (${code}).`;
}

const same = <T,>(a: T, b: T) => JSON.stringify(a) === JSON.stringify(b);
const selectClass = "min-h-11 w-full rounded-control border border-border-subtle bg-surface-1 px-3";

function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="space-y-1 text-sm">
      <span className="text-text-secondary">{label}</span>
      {children}
      {hint ? <span className="block text-xs text-text-muted">{hint}</span> : null}
    </label>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <fieldset id={id} className="scroll-mt-4 space-y-3" data-testid={`limits-${id}`}>
      <legend className="text-sm font-semibold text-text-primary">{title}</legend>
      {children}
    </fieldset>
  );
}

type Props = {
  bot: AutonomousTradingSettingsView;
  account: UserRiskSettings;
  /** PATCH /autonomous/settings is owner-only; account limits accept traders. */
  owner: boolean;
  ownerReason: string | null;
  onBotSaved: (view: AutonomousTradingSettingsView) => void;
  onAccountSaved: (settings: UserRiskSettings) => void;
  /** GET /autonomous/settings/bounds; null falls back to the ceilings on the bot view. */
  bounds?: Bounds;
};

/**
 * One form for every limit. Account limits (all trades) and bot limits (the
 * autonomous bot, held to ≤ account) stay separate values side by side; one
 * save writes whichever side changed, account first so a raised account cap
 * is in place before the bot cap is checked against it.
 */
export function TradingLimitsCard({ bot, account, owner, ownerReason, onBotSaved, onAccountSaved, bounds = null }: Props) {
  const [botForm, setBotForm] = useState(() => toBotForm(bot));
  const [accountForm, setAccountForm] = useState(() => toAccountForm(account));
  const [saving, setSaving] = useState(false);
  // Synchronous guard: state updates are async, so a fast double submit could
  // otherwise start two save sequences before `saving` re-renders the button.
  const inFlight = useRef(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [saved, setSaved] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);

  const botDirty = owner && !same(botForm, toBotForm(bot));
  const accountDirty = !same(accountForm, toAccountForm(account));
  const setBot = (patch: Partial<BotForm>) => setBotForm({ ...botForm, ...patch });
  const setAccount = (patch: Partial<AccountForm>) => setAccountForm({ ...accountForm, ...patch });

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    setErrors([]);
    setSaved(null);
    try {
      const failures: string[] = [];
      const done: string[] = [];
      if (accountDirty) {
        try {
          const updated = await api.risk.updateSettings(toAccountPayload(accountForm));
          setAccountForm(toAccountForm(updated));
          onAccountSaved(updated);
          done.push("Account limits saved.");
        } catch (err) {
          failures.push(
            `Account limits not saved: ${err instanceof Error ? err.message : "unknown error"}.`,
          );
          if (botDirty) failures.push("Bot limits not saved: skipped because the account limits failed.");
        }
      }
      if (botDirty && failures.length === 0) {
        try {
          const updated = await api.autonomous.updateSettings(toBotPayload(botForm));
          setBotForm(toBotForm(updated));
          onBotSaved(updated);
          done.push("Bot limits saved.");
        } catch (err) {
          const reason = err instanceof ApiError ? botValidationMessage(err.message, bot) : "unknown error.";
          failures.push(
            done.length ? `Bot limits not saved (account limits were saved): ${reason}` : `Bot limits not saved: ${reason}`,
          );
        }
      }
      setErrors(failures);
      setSaved(done.length ? done.join(" ") : null);
    } finally {
      setSaving(false);
      inFlight.current = false;
    }
  }

  async function handleReset() {
    setConfirmReset(false);
    if (inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    setErrors([]);
    setSaved(null);
    try {
      const updated = await api.risk.resetSettings();
      setAccountForm(toAccountForm(updated));
      onAccountSaved(updated);
      setSaved("Account limits reset to defaults.");
    } catch (err) {
      setErrors([err instanceof Error ? err.message : "Unable to reset account limits."]);
    } finally {
      setSaving(false);
      inFlight.current = false;
    }
  }

  // Every hint below is read from the server; nothing here is a client-side limit.
  const botDailyMax = bounds?.bot_daily_loss_limit_percent.max ?? bot.max_daily_loss_percent_ceiling;
  const botRiskMax = bounds?.bot_risk_per_trade_percent.max ?? bot.max_risk_per_trade_percent_ceiling;
  const accountTradesLimits = bounds ? inputLimits(bounds.account_max_trades_per_day) : { min: "1" };
  const botTradesHint = boundHint(bounds?.bot_max_trades_per_day);
  const accountRiskHint = boundHint(bounds?.account_max_risk_per_trade_percent, "%");
  const effectiveNote = bot.using_defaults
    ? "the bot limit applies once saved"
    : bot.effective_max_trades_per_day < bot.max_trades_per_day
      ? `capped by account limit of ${bot.account_max_trades_per_day}`
      : `account limit ${bot.account_max_trades_per_day}`;
  const toggle = (label: string, key: "green_day_protection_enabled" | "one_loss_stop_enabled" | "overtrading_guard_enabled", testId: string) => (
    <label className="flex min-h-11 items-center gap-2">
      <input
        type="checkbox"
        data-testid={testId}
        checked={accountForm[key]}
        onChange={(e) => setAccount({ [key]: e.target.checked })}
      />
      {label}
    </label>
  );

  return (
    <Card id="limits" data-testid="trading-limits-card" className="scroll-mt-4">
      <CardHeader>
        <CardTitle>Trading limits</CardTitle>
      </CardHeader>
      <CardContent>
        <form className="space-y-6" onSubmit={(e) => void handleSubmit(e)}>
          <p className="text-xs text-text-muted">
            Account limits apply to every trade, manual or bot. Bot limits apply to the autonomous bot and are held to
            the account limits.
          </p>
          <Section id="caps" title="Daily loss and trade caps">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Account: max trades/day (all trades)">
                <Input
                  data-testid="risk-max-trades"
                  type="number"
                  {...accountTradesLimits}
                  value={accountForm.max_trades_per_day}
                  onChange={(e) => setAccount({ max_trades_per_day: e.target.value })}
                />
              </Field>
              <Field
                label="Bot: max trades/day (≤ account)"
                hint={
                  <span data-testid="autonomous-effective-max-trades">
                    Effective {bot.effective_max_trades_per_day} · {effectiveNote}
                    {botTradesHint ? ` · allowed ${botTradesHint}` : ""}
                  </span>
                }
              >
                <Input
                  data-testid="autonomous-max-trades"
                  inputMode="numeric"
                  disabled={!owner}
                  value={botForm.max_trades_per_day}
                  onChange={(e) => setBot({ max_trades_per_day: e.target.value })}
                />
              </Field>
              <Field label="Account: daily loss limit (amount)">
                <Input
                  data-testid="risk-daily-loss-limit"
                  placeholder="e.g. 50"
                  value={accountForm.daily_loss_limit}
                  onChange={(e) => setAccount({ daily_loss_limit: e.target.value })}
                />
              </Field>
              <Field label="Bot: daily loss limit (%)" hint={`Max ${botDailyMax}% of equity`}>
                <Input
                  data-testid="autonomous-daily-loss-percent"
                  inputMode="decimal"
                  disabled={!owner}
                  value={botForm.daily_loss_limit_percent}
                  onChange={(e) => setBot({ daily_loss_limit_percent: e.target.value })}
                />
              </Field>
            </div>
          </Section>
          <Section id="sizing" title="Risk per trade and position size">
            <div className="grid gap-4 md:grid-cols-3">
              <Field label="Account: max risk per trade (%)" hint={accountRiskHint ? `Allowed ${accountRiskHint}` : undefined}>
                <Input
                  data-testid="risk-max-risk-percent"
                  value={accountForm.max_risk_per_trade_percent}
                  onChange={(e) => setAccount({ max_risk_per_trade_percent: e.target.value })}
                />
              </Field>
              <Field
                label="Bot: risk per trade (%)"
                hint={`Max ${botRiskMax}% of portfolio. Empty uses grade risk (below).`}
              >
                <Input
                  data-testid="autonomous-risk-per-trade"
                  inputMode="decimal"
                  placeholder="Grade-based"
                  disabled={!owner}
                  value={botForm.risk_per_trade_percent}
                  onChange={(e) => setBot({ risk_per_trade_percent: e.target.value })}
                />
              </Field>
              <Field label="Position size: account balance" hint="Size = balance × risk % ÷ stop distance.">
                <Input
                  data-testid="risk-default-balance"
                  value={accountForm.default_account_balance}
                  onChange={(e) => setAccount({ default_account_balance: e.target.value })}
                />
              </Field>
            </div>
          </Section>
          <Section id="target" title="Strategy and coin">
            <div className="grid gap-4 md:grid-cols-2">
              <Field
                label="Strategy"
                hint={bot.strategy_options.length === 0 ? "No enabled strategies yet. Enable one under Strategies." : undefined}
              >
                <select
                  data-testid="autonomous-strategy"
                  className={selectClass}
                  disabled={!owner}
                  value={botForm.strategy_id}
                  onChange={(e) => setBot({ strategy_id: e.target.value })}
                >
                  <option value="">Select strategy</option>
                  {bot.strategy_options.map((s) => (
                    <option key={s.strategy_id} value={s.strategy_id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="Coin (from watchlist)"
                hint={
                  <span data-testid="autonomous-symbol-hint">
                    {bot.symbol_options.length === 0 ? "No enabled coins on the watchlist. " : null}
                    <Link href="/settings/watcher" className="underline">
                      Edit watchlist
                    </Link>
                  </span>
                }
              >
                <select
                  data-testid="autonomous-symbol"
                  className={selectClass}
                  disabled={!owner}
                  value={botForm.symbol}
                  onChange={(e) => setBot({ symbol: e.target.value })}
                >
                  <option value="">Select coin</option>
                  {bot.symbol_options.map((symbol) => (
                    <option key={symbol} value={symbol}>
                      {symbol}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </Section>
          <details className="text-sm">
            <summary className="min-h-11 cursor-pointer text-text-secondary">More account rules (target, timezone, guards)</summary>
            <div className="space-y-4 pt-2">
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="Daily profit target">
                  <Input
                    data-testid="risk-daily-target"
                    placeholder="e.g. 100"
                    value={accountForm.daily_target}
                    onChange={(e) => setAccount({ daily_target: e.target.value })}
                  />
                </Field>
                <Field label="Timezone">
                  <Input data-testid="risk-timezone" value={accountForm.timezone} onChange={(e) => setAccount({ timezone: e.target.value })} />
                </Field>
              </div>
              <div className="text-text-secondary">
                {toggle("Green day protection", "green_day_protection_enabled", "risk-green-day")}
                {toggle("One loss stop", "one_loss_stop_enabled", "risk-one-loss-stop")}
                {toggle("Overtrading guard", "overtrading_guard_enabled", "risk-overtrading-guard")}
              </div>
              <Field label="Notes (optional)">
                <Input data-testid="risk-notes" value={accountForm.notes} onChange={(e) => setAccount({ notes: e.target.value })} />
              </Field>
            </div>
          </details>
          {account.using_defaults ? (
            <p className="text-xs text-warning" data-testid="risk-using-defaults">
              Account limits use defaults until you save.
            </p>
          ) : null}
          {errors.map((message) => (
            <p key={message} className="text-sm text-danger" role="alert" data-testid="limits-error">
              {message}
            </p>
          ))}
          {saved ? (
            <p className="text-sm text-success" role="status" data-testid="limits-saved">
              {saved}
            </p>
          ) : null}
          <div className="flex flex-wrap items-baseline gap-3">
            <Button type="submit" disabled={saving || (!botDirty && !accountDirty)} data-testid="limits-save">
              {saving ? "Saving…" : "Save limits"}
            </Button>
            <Button type="button" variant="secondary" disabled={saving} onClick={() => setConfirmReset(true)} data-testid="risk-settings-reset">
              Reset account defaults
            </Button>
            <ConfirmDialog
              open={confirmReset}
              title="Reset account limits to defaults?"
              description={
                <>
                  Your account limits (daily loss and target, trades per day, risk per trade, balance, timezone and
                  guards) return to the system defaults. Unsaved edits in this form are discarded. A balance change
                  is audited and reaches the account equity stop from the next UTC day. The kill switch and bot limits
                  are not changed.
                </>
              }
              confirmLabel="Reset to defaults"
              confirmVariant="destructive"
              onConfirm={() => void handleReset()}
              onCancel={() => setConfirmReset(false)}
              testId="risk-reset-confirm"
            />
            {ownerReason ? (
              <p className="text-sm text-text-muted" data-testid="autonomous-save-owner-reason">
                Bot limits: {ownerReason}
              </p>
            ) : null}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
