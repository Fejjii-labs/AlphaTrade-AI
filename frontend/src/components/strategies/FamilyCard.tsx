import Link from "next/link";

import type { BrainOutcomes, BrainSetup, BrainStrategy } from "@/lib/api/brain-types";
import { formatDateTime } from "@/lib/format";

function sentence(token: string | null | undefined): string {
  if (!token) return "";
  const text = token.replaceAll("_", " ").toLowerCase().trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function SetupRow({ setup }: { setup: BrainSetup }) {
  return (
    <li>
      <Link
        className="flex flex-wrap items-baseline justify-between gap-x-3 rounded-control py-1 text-text-primary hover:text-accent"
        href={`/strategies/setups/${setup.setup_id}?returnTo=${encodeURIComponent("/strategies")}`}
      >
        <span>
          {setup.symbol || setup.instrument} · {sentence(setup.direction)}
          <span className="text-text-muted"> · {sentence(setup.state)}</span>
        </span>
        <span className="text-xs text-text-muted">{formatDateTime(setup.observed_at)}</span>
      </Link>
    </li>
  );
}

/** "BTCUSDT, ETHUSDT · 15m" from the family's configured strategies. */
export function scopeText(strategies: readonly BrainStrategy[]): string {
  const coins = [...new Set(strategies.map((s) => s.spec.symbol))].sort();
  const frames = [...new Set(strategies.map((s) => s.spec.trigger_timeframe))];
  return [coins.join(", "), frames.join(", ")].filter(Boolean).join(" · ");
}

export function outcomesText(outcomes: BrainOutcomes): string {
  return `${outcomes.confirmed} confirmed · ${outcomes.traded} traded · ${outcomes.won} won`;
}

export interface FamilyCardProps {
  id: string;
  name: string;
  loading: boolean;
  /** Configured strategies in this family, or null when they could not be read. */
  strategies: BrainStrategy[] | null;
  /** Last-7-day confirmed / traded / won, or null when unavailable. */
  outcomes: BrainOutcomes | null;
  /** Latest setups (newest first, up to 20), or null when unavailable. */
  setups: BrainSetup[] | null;
}

/** One strategy family: on/off, coins, latest version, 7-day results and the latest setups. */
export function FamilyCard({ id, name, loading, strategies, outcomes, setups }: FamilyCardProps) {
  const recent = setups
    ?.slice()
    .sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at));
  const enabled = strategies?.some((s) => s.enabled) ?? false;
  const latest = strategies?.length ? Math.max(...strategies.map((s) => s.version)) : null;
  return (
    <article
      data-testid="strategy-family"
      className="card-inner space-y-3 rounded-card border border-border-subtle bg-surface-1 p-4"
    >
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-section">{name}</h2>
        {strategies ? (
          <span className={enabled ? "text-sm text-positive" : "text-sm text-text-muted"} data-testid="family-enabled">
            {enabled ? "On" : "Off"}
          </span>
        ) : null}
      </div>
      {strategies?.length ? (
        <p className="text-sm text-text-secondary">
          {scopeText(strategies)}
          {latest != null ? <span className="text-text-muted"> · v{latest}</span> : null}
        </p>
      ) : null}
      <div>
        <p className="text-base font-medium tabular-nums" data-testid="family-outcomes">
          {loading ? "Loading…" : outcomes ? outcomesText(outcomes) : "Results unavailable"}
        </p>
        <p className="text-xs text-text-muted">Last 7 days</p>
      </div>
      {recent && recent.length > 0 ? (
        <>
          <ul className="text-sm">{recent.slice(0, 3).map((s) => <SetupRow key={s.setup_id} setup={s} />)}</ul>
          {recent.length > 3 ? (
            <details className="text-sm">
              <summary className="min-h-11 cursor-pointer content-center text-text-secondary">More setups</summary>
              <ul>{recent.slice(3).map((s) => <SetupRow key={s.setup_id} setup={s} />)}</ul>
            </details>
          ) : null}
        </>
      ) : recent ? (
        <p className="text-sm text-text-muted">No setups in 7 days.</p>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-4 border-t border-border-subtle pt-2">
        <Link className="inline-flex h-10 items-center text-sm text-accent" href={`/agent?discuss=${id}`}>
          Ask Agent
        </Link>
        <Link className="inline-flex h-10 items-center text-sm text-accent" href={`/strategies?family=${id}`}>
          Versions
        </Link>
      </div>
    </article>
  );
}
