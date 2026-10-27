import Link from "next/link";

import type { BrainSetup } from "@/lib/api/brain-types";
import { formatDateTime } from "@/lib/format";

const DAY_MS = 86400000;

function sentence(token: string | null | undefined): string {
  if (!token) return "";
  const text = token.replaceAll("_", " ").toLowerCase().trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Detections per local day for the last seven days, oldest first. */
export function dailyCounts(setups: readonly BrainSetup[], now = new Date()): { day: Date; count: number }[] {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Array.from({ length: 7 }, (_, i) => {
    const day = new Date(today.getTime() - (6 - i) * DAY_MS);
    const next = day.getTime() + DAY_MS;
    const count = setups.filter((s) => {
      const at = Date.parse(s.observed_at);
      return at >= day.getTime() && at < next;
    }).length;
    return { day, count };
  });
}

function DetectionRow({ setup }: { setup: BrainSetup }) {
  return (
    <li>
      <Link
        className="flex flex-wrap items-baseline justify-between gap-x-3 rounded-control py-1 text-text-primary hover:text-accent"
        href={`/strategies/setups/${setup.setup_id}?returnTo=${encodeURIComponent("/strategies")}`}
      >
        <span>
          {setup.symbol || setup.instrument} · {sentence(setup.direction)} · {setup.timeframe}
          <span className="text-text-muted"> · {sentence(setup.stage ?? setup.state)}</span>
        </span>
        <span className="text-xs text-text-muted">{formatDateTime(setup.observed_at)} →</span>
      </Link>
    </li>
  );
}

export interface FamilyCardProps {
  id: string;
  name: string;
  loading: boolean;
  /** Versions in this family, or null when configurations could not be read. */
  versions: number | null;
  /** The family's last-7-day read: server total plus up to 20 rows. Null when unavailable. */
  detections: { setups: BrainSetup[]; count: number } | null;
}

/** One strategy family: 7-day count and bars, the latest detections, and the full week behind a disclosure. */
export function FamilyCard({ id, name, loading, versions, detections }: FamilyCardProps) {
  const recent = detections?.setups
    .slice()
    .sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at));
  const days = recent ? dailyCounts(recent) : [];
  const peak = Math.max(1, ...days.map((d) => d.count));
  const partial = detections && recent ? detections.count > recent.length : false;
  return (
    <article
      data-testid="strategy-family"
      className="card-inner space-y-3 rounded-card border border-border-subtle bg-surface-1 p-4"
    >
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-section">{name}</h2>
        <span className="text-sm text-text-secondary" data-testid="family-count">
          {loading ? "Loading…" : detections ? `${detections.count} in 7 days` : "Detections unavailable"}
        </span>
      </div>
      <p className="text-sm text-text-muted">
        {loading ? "" : versions == null ? "Versions unavailable" : `${versions} ${versions === 1 ? "version" : "versions"} · Paper`}
      </p>
      {recent && recent.length > 0 ? (
        <>
          <div
            role="img"
            aria-label={`Detections per day: ${days.map((d) => d.count).join(", ")}`}
            className="grid h-12 grid-cols-7 items-end gap-1"
            data-testid="family-bars"
          >
            {days.map((d) => (
              <div key={d.day.toISOString()} className="flex h-full flex-col justify-end gap-1">
                <div
                  className="rounded-sm bg-accent/70"
                  style={{ height: `${d.count === 0 ? 2 : Math.max(8, (d.count / peak) * 100)}%` }}
                />
              </div>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-1 text-center text-[0.6875rem] text-text-muted" aria-hidden="true">
            {days.map((d) => (
              <span key={d.day.toISOString()}>{d.day.toLocaleDateString(undefined, { weekday: "narrow" })}</span>
            ))}
          </div>
          {partial ? (
            <p className="text-xs text-text-muted">Bars and list cover the latest {recent.length}.</p>
          ) : null}
          <ul className="text-sm">{recent.slice(0, 3).map((s) => <DetectionRow key={s.setup_id} setup={s} />)}</ul>
          {recent.length > 3 ? (
            <details className="text-sm">
              <summary className="min-h-11 cursor-pointer content-center text-text-secondary">Show all 7 days</summary>
              <ul>{recent.slice(3).map((s) => <DetectionRow key={s.setup_id} setup={s} />)}</ul>
            </details>
          ) : null}
        </>
      ) : recent ? (
        <p className="text-sm text-text-muted">No detections in 7 days.</p>
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
