"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type {
  NotificationPreferences,
  TelegramFilterOption,
  TelegramNotificationPolicyV2,
  TelegramPolicyOptions,
} from "@/lib/api/types";
import { cn } from "@/lib/utils";

type QuietHours = TelegramNotificationPolicyV2["quiet_hours"];
type Choices = TelegramFilterOption[] | "loading" | "unavailable";

/**
 * Saved filters with no control on this page that can each silence every
 * setup alert. Saving with setup alerts on puts them back to their defaults.
 * Cooldown and duplicate suppression only hold back repeats, so they stay.
 */
const SETUP_ON_DEFAULTS = {
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
} satisfies Partial<TelegramNotificationPolicyV2>;

/** Older channel routing that also gates paper alerts; "on" must not be blocked there. */
const ROUTING_ON = {
  telegram_enabled: true,
  min_severity: "info",
  enabled_alert_types: null,
  digest_mode: "immediate",
} satisfies Partial<NotificationPreferences>;

const MAX_PICKS = 200;

/** Setup alerts are off when the saved event types leave out every setup event. */
export function setupAlertsOn(policy: TelegramNotificationPolicyV2): boolean {
  return (
    policy.event_types === null ||
    policy.event_types.some((type) => type === "SETUP" || type.startsWith("SFP_"))
  );
}

function savedQuietHours(prefs: NotificationPreferences): QuietHours {
  if (prefs.telegram_policy?.quiet_hours) return prefs.telegram_policy.quiet_hours;
  if (prefs.quiet_hours_enabled && prefs.quiet_hours_start && prefs.quiet_hours_end) {
    return {
      start: prefs.quiet_hours_start,
      end: prefs.quiet_hours_end,
      timezone: prefs.timezone ?? "UTC",
    };
  }
  return null;
}

function localTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function validTimezone(timezone: string): boolean {
  try {
    new Intl.DateTimeFormat("en", { timeZone: timezone });
    return true;
  } catch {
    return false;
  }
}

function Switch({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className="inline-flex min-h-11 shrink-0 items-center gap-2 text-sm font-medium text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
    >
      <span
        aria-hidden
        className={cn(
          "relative inline-block h-6 w-11 rounded-full transition-colors",
          checked ? "bg-accent-solid" : "bg-surface-raised ring-1 ring-border",
        )}
      >
        <span
          className={cn(
            "absolute left-0 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform",
            checked ? "translate-x-[1.375rem]" : "translate-x-0.5",
          )}
        />
      </span>
      {checked ? "On" : "Off"}
    </button>
  );
}

/** Named picks; nothing picked means every one. Identifiers never reach the screen. */
function PickFilter({
  label,
  hint,
  options,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  options: Choices;
  value: string[] | null;
  onChange: (value: string[] | null) => void;
}) {
  const choices = typeof options === "string" ? [] : options;
  const picked = (option: TelegramFilterOption) =>
    option.values.some((v) => value?.includes(v) ?? false);
  const pickedOptions = choices.filter(picked);
  function toggle(option: TelegramFilterOption) {
    const next = choices.filter((o) => (o === option ? !picked(o) : picked(o)));
    onChange(next.length ? [...new Set(next.flatMap((o) => o.values))] : null);
  }
  // A saved list that none of today's choices match lets no setup alert through.
  const blocking = value !== null && typeof options !== "string" && pickedOptions.length === 0;
  return (
    <div className="space-y-2">
      <p className="font-medium text-text-primary">{label}</p>
      <p className="text-xs text-text-muted">{hint}</p>
      {options === "loading" ? (
        <p className="text-xs text-text-muted">Loading choices…</p>
      ) : options === "unavailable" ? (
        <p className="text-xs text-text-muted">Choices unavailable. Your saved choice is kept.</p>
      ) : choices.length === 0 ? (
        <p className="text-xs text-text-muted">Nothing to choose from yet.</p>
      ) : (
        <div role="group" aria-label={label} className="flex flex-wrap gap-2">
          {choices.map((option) => (
            <Chip
              key={option.label}
              label={option.label}
              pressed={picked(option)}
              onPressedChange={() => toggle(option)}
            />
          ))}
        </div>
      )}
      {blocking ? (
        <div className="flex flex-wrap items-center gap-2 text-xs text-warning" role="note">
          <span>A saved {label.toLowerCase()} choice no longer matches anything and blocks setup alerts.</span>
          <Button type="button" variant="secondary" size="sm" onClick={() => onChange(null)}>
            Allow all
          </Button>
        </div>
      ) : null}
    </div>
  );
}

export function SetupAlertsForm({
  prefs,
  policy,
  onSaved,
}: {
  prefs: NotificationPreferences;
  policy: TelegramNotificationPolicyV2;
  onSaved: () => Promise<void>;
}) {
  const initial = {
    on: setupAlertsOn(policy),
    strategies: policy.strategy_subscriptions,
    coins: policy.symbol_subscriptions,
    quiet: savedQuietHours(prefs),
  };
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [options, setOptions] = useState<TelegramPolicyOptions | "loading" | "unavailable">(
    "loading",
  );
  useEffect(() => {
    let live = true;
    api.notifications
      .telegramOptions()
      .then((loaded) => live && setOptions(loaded))
      .catch(() => live && setOptions("unavailable"));
    return () => {
      live = false;
    };
  }, []);

  const families: Choices =
    typeof options === "string" ? options : (options.strategy_families ?? "unavailable");
  // Saved coins stay pickable even after they leave the watchlist: they are names.
  const coins: Choices =
    typeof options === "string"
      ? options
      : [
          ...options.symbols,
          ...(draft.coins ?? [])
            .filter((coin) => !options.symbols.some((o) => o.values.includes(coin)))
            .map((coin) => ({ label: coin, values: [coin] })),
        ];
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);

  function update(patch: Partial<typeof draft>) {
    setDraft((current) => ({ ...current, ...patch }));
    setMessage(null);
  }

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    if ([draft.strategies, draft.coins].some((list) => (list?.length ?? 0) > MAX_PICKS)) {
      setMessage("Too many strategies picked. Choose fewer families.");
      return;
    }
    if (draft.quiet) {
      if (draft.quiet.start === draft.quiet.end) {
        setMessage("Quiet hours need different start and end times.");
        return;
      }
      if (!validTimezone(draft.quiet.timezone)) {
        setMessage("Enter a time zone such as Europe/Berlin.");
        return;
      }
    }
    let next: TelegramNotificationPolicyV2 = {
      ...policy,
      strategy_subscriptions: draft.strategies,
      symbol_subscriptions: draft.coins,
      quiet_hours: draft.quiet,
    };
    const patch: Partial<NotificationPreferences> = {};
    if (draft.on) {
      next = { ...next, ...SETUP_ON_DEFAULTS };
      if (!initial.on) {
        // Back to the default before muting: every event type.
        next.event_types = null;
        Object.assign(patch, ROUTING_ON);
      }
    } else if (initial.on) {
      next.event_types = [];
    }
    if (JSON.stringify(draft.quiet) !== JSON.stringify(initial.quiet)) {
      Object.assign(
        patch,
        draft.quiet
          ? {
              quiet_hours_enabled: true,
              quiet_hours_start: draft.quiet.start,
              quiet_hours_end: draft.quiet.end,
              timezone: draft.quiet.timezone,
            }
          : { quiet_hours_enabled: false },
      );
    }
    setBusy(true);
    try {
      await api.notifications.updatePreferences({ ...patch, telegram_policy: next });
      await onSaved();
    } catch {
      setMessage("Your changes could not be saved. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-5" data-testid="setup-alerts-form">
      <fieldset disabled={busy} className="min-w-0 space-y-5">
        <section className="space-y-3 rounded-card border border-border-subtle p-4" aria-labelledby="setup-alerts-title">
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1">
              <h3 id="setup-alerts-title" className="font-medium text-text-primary">
                Setup alerts
              </h3>
              <p className="text-xs text-text-muted">
                A message when a strategy spots a possible trade setup. It does not open a trade.
              </p>
            </div>
            <Switch
              checked={draft.on}
              label="Setup alerts"
              onChange={(on) => update({ on })}
            />
          </div>
          {draft.on ? (
            <div className="space-y-4 border-t border-border-subtle pt-3">
              <PickFilter
                label="Strategies"
                hint="Pick the strategy families you want. Pick none to get all. Strategies you add later are included after you save again."
                options={families}
                value={draft.strategies}
                onChange={(strategies) => update({ strategies })}
              />
              <PickFilter
                label="Coins"
                hint="Pick the coins you want. Pick none to get all."
                options={coins}
                value={draft.coins}
                onChange={(coins) => update({ coins })}
              />
            </div>
          ) : null}
        </section>

        <section className="space-y-3 rounded-card border border-border-subtle p-4" aria-labelledby="quiet-hours-title">
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1">
              <h3 id="quiet-hours-title" className="font-medium text-text-primary">
                Quiet hours
              </h3>
              <p className="text-xs text-text-muted">
                No setup alerts during these hours. They are skipped, not sent later. Trade,
                daily summary and safety messages still arrive.
              </p>
            </div>
            <Switch
              checked={draft.quiet !== null}
              label="Quiet hours"
              onChange={(on) =>
                update({
                  quiet: on ? { start: "22:00", end: "07:00", timezone: localTimezone() } : null,
                })
              }
            />
          </div>
          {draft.quiet ? (
            <div className="grid gap-3 sm:grid-cols-3">
              {(
                [
                  ["start", "From"],
                  ["end", "To"],
                  ["timezone", "Time zone"],
                ] as const
              ).map(([key, label]) => (
                <label key={key} className="block space-y-1 text-xs text-text-secondary">
                  <span>{label}</span>
                  <Input
                    type={key === "timezone" ? "text" : "time"}
                    required
                    maxLength={key === "timezone" ? 64 : undefined}
                    value={draft.quiet![key]}
                    onChange={(e) => update({ quiet: { ...draft.quiet!, [key]: e.target.value } })}
                  />
                </label>
              ))}
            </div>
          ) : null}
        </section>

        <Button type="submit" size="lg" disabled={busy || !dirty}>
          {busy ? "Saving…" : "Save changes"}
        </Button>
      </fieldset>
      {message ? (
        <p role="status" className="text-sm text-text-secondary">
          {message}
        </p>
      ) : null}
    </form>
  );
}
