"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { TelegramFilterOption, TelegramNotificationPolicyV2, TelegramPolicyOptions } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const inputClass =
  "min-h-11 w-full min-w-0 rounded border border-border bg-surface-0 px-3 text-text-primary";
const chipClass =
  "min-h-11 rounded-full border border-border px-4 text-sm text-text-secondary";
const chipOnClass = "border-accent bg-accent/15 text-text-primary";
const toggles = [
  ["forming_alerts", "Forming setup notifications"],
  ["confirmed_alerts", "Confirmed setup notifications"],
  ["risk_alerts", "Risk notifications"],
  ["paper_trade_opened", "Paper trade opened"],
  ["paper_trade_closed", "Paper trade closed"],
  ["partial_profit_event", "Partial profit"],
  ["stop_event", "Stop event"],
  ["daily_review_event", "Daily review event"],
] as const;

type Choices = TelegramFilterOption[] | "loading" | "unavailable";

/** All / None / Selected, where Selected is picked from named chips, never typed identifiers. */
function ChipFilter({
  label,
  value,
  choices,
  empty,
  hideUnknown = false,
  onChange,
}: {
  label: string;
  value: string[] | null;
  choices: Choices;
  empty: string;
  /** Unknown saved values are identifiers: count them instead of showing them. */
  hideUnknown?: boolean;
  onChange: (value: string[] | null) => void;
}) {
  const [mode, setMode] = useState(
    value === null ? "all" : value.length ? "selected" : "none",
  );
  const [kept, setKept] = useState(value ?? []);
  const selected = value ?? [];
  const options = Array.isArray(choices) ? choices : [];
  const known = new Set(options.flatMap((o) => o.values));
  const unknown = selected.filter((v) => !known.has(v));
  function select(next: string[]) {
    setKept(next);
    onChange(next);
  }
  function toggle(option: TelegramFilterOption) {
    const on = option.values.some((v) => selected.includes(v));
    select(
      on
        ? selected.filter((v) => !option.values.includes(v))
        : [...selected, ...option.values.filter((v) => !selected.includes(v))],
    );
  }
  return (
    <div className="space-y-2">
      <label className="block space-y-1">
        <span>{label}</span>
        <select
          className={inputClass}
          aria-label={label}
          value={mode}
          onChange={(event) => {
            const next = event.target.value;
            setMode(next);
            onChange(next === "all" ? null : next === "none" ? [] : kept);
          }}
        >
          <option value="all">All</option>
          <option value="none">None</option>
          <option value="selected">Selected</option>
        </select>
      </label>
      {mode === "selected" && (
        <div role="group" aria-label={`${label} choices`} className="flex flex-wrap gap-2">
          {choices === "loading" ? (
            <p className="text-xs text-text-muted">Loading choices…</p>
          ) : choices === "unavailable" ? (
            <p className="text-xs text-text-muted">Choices unavailable. Saved picks are kept.</p>
          ) : options.length === 0 && unknown.length === 0 ? (
            <p className="text-xs text-text-muted">{empty}</p>
          ) : null}
          {options.map((option) => {
            const on = option.values.some((v) => selected.includes(v));
            return (
              <button
                key={option.label}
                type="button"
                aria-pressed={on}
                onClick={() => toggle(option)}
                className={cn(chipClass, on && chipOnClass)}
              >
                {option.label}
              </button>
            );
          })}
          {choices === "loading" ? null : hideUnknown && unknown.length > 0 ? (
            <button
              type="button"
              aria-pressed={true}
              aria-label={`Remove ${unknown.length} other saved ${label.toLowerCase()}`}
              onClick={() => select(selected.filter((v) => known.has(v)))}
              className={cn(chipClass, chipOnClass)}
            >
              Other saved ({unknown.length}) ✕
            </button>
          ) : (
            unknown.map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={true}
                onClick={() => select(selected.filter((item) => item !== v))}
                className={cn(chipClass, chipOnClass)}
              >
                {v}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

export function TelegramPolicyForm({
  policy,
  enabled,
  onSaved,
}: {
  policy: TelegramNotificationPolicyV2;
  enabled: boolean;
  onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState(policy);
  const [policyEnabled, setPolicyEnabled] = useState(enabled);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [options, setOptions] = useState<TelegramPolicyOptions | "loading" | "unavailable">("loading");
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
  const choices = (key: keyof TelegramPolicyOptions) =>
    typeof options === "string" ? options : options[key];
  function update<K extends keyof TelegramNotificationPolicyV2>(
    key: K,
    value: TelegramNotificationPolicyV2[K],
  ) {
    setDraft((current) => ({ ...current, [key]: value }));
    setMessage(null);
  }
  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    const lists = [
      draft.symbol_subscriptions,
      draft.setup_stages,
      draft.strategy_subscriptions,
    ];
    if (
      lists.some(
        (list) =>
          list && (list.length > 200 || list.some((v) => !v || v.length > 80)),
      )
    ) {
      setMessage("Too many picks in one filter. Choose fewer.");
      return;
    }
    if (draft.quiet_hours) {
      if (draft.quiet_hours.start === draft.quiet_hours.end) {
        setMessage("Quiet hours start and end must differ.");
        return;
      }
      try {
        new Intl.DateTimeFormat("en", { timeZone: draft.quiet_hours.timezone });
      } catch {
        setMessage("Use a valid IANA timezone for quiet hours.");
        return;
      }
    }
    setBusy(true);
    try {
      await api.notifications.updatePreferences({
        telegram_enabled: policyEnabled,
        telegram_policy: draft,
      });
      await onSaved();
      setMessage(null);
    } catch {
      setMessage("Telegram policy could not be saved. Please try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      onSubmit={save}
      className="space-y-4 border-t border-border-subtle pt-4"
      data-testid="telegram-policy-v2"
    >
      <h3 className="font-medium">Telegram notification policy V2</h3>
      <p className="text-xs text-text-muted">
        Preferences filter eligible Telegram events. Saving does not activate
        Telegram network delivery. Existing channel filters also apply.
      </p>
      <fieldset disabled={busy} className="min-w-0 space-y-4">
        <label className="flex min-h-11 items-center gap-3">
          <input
            type="checkbox"
            checked={policyEnabled}
            onChange={(e) => setPolicyEnabled(e.target.checked)}
          />
          Enable Telegram notification policy
        </label>
        <ChipFilter
          label="Watched symbols"
          value={draft.symbol_subscriptions}
          choices={choices("symbols")}
          empty="No watched symbols yet."
          onChange={(v) => update("symbol_subscriptions", v)}
        />
        <ChipFilter
          label="Watched strategies"
          value={draft.strategy_subscriptions}
          choices={choices("strategies")}
          empty="No strategies yet."
          hideUnknown
          onChange={(v) => update("strategy_subscriptions", v)}
        />
        <ChipFilter
          label="Nested stage filters"
          value={draft.setup_stages}
          choices={choices("setup_stages")}
          empty="No stages available."
          onChange={(v) => update("setup_stages", v)}
        />
        <p className="text-xs text-text-muted">
          SFP lifecycle notifications use the shared phase, severity and event
          filters. SFP has no setup stage, so a selected Nested stage filter
          excludes SFP events.
        </p>
        <label className="block space-y-1">
          <span>Minimum Telegram severity</span>
          <select
            className={inputClass}
            value={draft.minimum_severity}
            onChange={(e) =>
              update(
                "minimum_severity",
                e.target
                  .value as TelegramNotificationPolicyV2["minimum_severity"],
              )
            }
          >
            {["INFO", "WATCH", "ACTION", "CRITICAL"].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        <label className="block space-y-1">
          <span>Minimum quality threshold</span>
          <input
            className={inputClass}
            type="number"
            aria-label="Minimum quality threshold"
            min="0"
            max="100"
            step="any"
            value={draft.minimum_quality ?? ""}
            onChange={(e) =>
              update(
                "minimum_quality",
                e.target.value === "" ? null : Number(e.target.value),
              )
            }
          />
          <span className="block text-xs text-text-muted">
            Leave blank for no threshold. Current Candidate, Nested and SFP alerts have
            no quality score and are excluded by any threshold, including zero.
          </span>
        </label>
        <details>
          <summary className="min-h-11 cursor-pointer py-3">
            Event preferences
          </summary>
          <div className="space-y-1">
            {toggles.map(([key, label]) => (
              <label key={key} className="flex min-h-11 items-center gap-3">
                <input
                  type="checkbox"
                  checked={draft[key]}
                  onChange={(e) => update(key, e.target.checked)}
                />
                {label}
              </label>
            ))}
            <p className="text-xs text-text-muted">
              Forming preferences apply to SFP sweep and reclaim notices. Nested
              notifications remain confirmed-only. Daily review preferences do
              not create a notification producer. Mandatory risk bypasses these
              policy filters.
            </p>
          </div>
        </details>
        <label className="block space-y-1">
          <span>Cooldown (seconds)</span>
          <input
            className={inputClass}
            type="number"
            min="0"
            max="604800"
            step="1"
            required
            aria-label="Cooldown (seconds)"
            value={draft.cooldown_seconds}
            onChange={(e) => update("cooldown_seconds", Number(e.target.value))}
          />
          <span className="block text-xs text-text-muted">
            0 disables cooldown. Maximum 604800 seconds (7 days).
          </span>
        </label>
        <details>
          <summary className="min-h-11 cursor-pointer py-3">
            Quiet hours
          </summary>
          <div className="space-y-3">
            <label className="flex min-h-11 items-center gap-3">
              <input
                type="checkbox"
                checked={draft.quiet_hours !== null}
                onChange={(e) =>
                  update(
                    "quiet_hours",
                    e.target.checked
                      ? { start: "22:00", end: "07:00", timezone: "UTC" }
                      : null,
                  )
                }
              />
              Enable Telegram quiet hours
            </label>
            {draft.quiet_hours && (
              <>
                {(["start", "end", "timezone"] as const).map((key) => (
                  <label key={key} className="block space-y-1">
                    <span>Quiet hours {key}</span>
                    <input
                      className={inputClass}
                      type={key === "timezone" ? "text" : "time"}
                      required
                      maxLength={key === "timezone" ? 64 : undefined}
                      value={draft.quiet_hours![key]}
                      onChange={(e) =>
                        update("quiet_hours", {
                          ...draft.quiet_hours!,
                          [key]: e.target.value,
                        })
                      }
                    />
                  </label>
                ))}
              </>
            )}
            <p className="text-xs text-text-muted">
              Overnight windows are supported. Events during quiet hours are
              discarded, not delivered later.
            </p>
          </div>
        </details>
        <details>
          <summary className="min-h-11 cursor-pointer py-3">
            Other saved policy filters
          </summary>
          <p className="break-words text-xs text-text-muted">
            Event types:{" "}
            {draft.event_types === null
              ? "All"
              : draft.event_types.join(", ") || "None"}
            . Severity allowlist:{" "}
            {draft.severities === null
              ? "All"
              : draft.severities.join(", ") || "None"}
            . Duplicate suppression: {draft.duplicate_suppression_seconds}{" "}
            seconds. These saved filters are preserved and also affect delivery.
          </p>
        </details>
        <Button type="submit" size="lg" disabled={busy}>
          {busy ? "Saving…" : "Save Telegram policy"}
        </Button>
      </fieldset>
      {message && (
        <p role="status" className="text-sm text-text-secondary">
          {message}
        </p>
      )}
    </form>
  );
}
