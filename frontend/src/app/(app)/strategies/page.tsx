"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { RiskSettingsSummary } from "@/components/settings/RiskSettingsSummary";
import { ExperimentsPanel } from "@/components/strategies/ExperimentsPanel";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/AuthContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import { savedEntries } from "@/lib/api/saved-entries";
import { formatDateTime } from "@/lib/format";
import { strategyStatusFor } from "@/lib/strategy-status";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";

const families = [
  {
    id: "nested",
    name: "Nested Continuation",
    detectionFamily: "nested_continuation",
    kind: "operational_nested_continuation/v1",
  },
  { id: "sfp", name: "SFP", kind: "swing_failure_pattern/v1", detectionFamily: "sfp" },
];
export default function StrategiesPage() {
  const { user, organization } = useAuth();
  const [session, setSession] = useState(sessionGeneration);
  useEffect(() => onSessionCleared(() => setSession(sessionGeneration())), []);
  if (!user || !organization) return null;
  return (
    <StrategiesWorkspace key={JSON.stringify([organization.id, user.id, session])} />
  );
}

function StrategiesWorkspace() {
  const params = useSearchParams();
  const family = families.find((f) => f.id === params.get("family"));
  const loader = useCallback(async () => {
    const [brain, library, drafts] = await Promise.allSettled([
      api.strategyBrain.overview(),
      api.strategies.list({ limit: 50 }),
      savedEntries.list({ category: "strategies", limit: 6 }),
    ]);
    return {
      brain: brain.status === "fulfilled" ? brain.value : null,
      library: library.status === "fulfilled" ? library.value.items : null,
      drafts: drafts.status === "fulfilled" ? drafts.value.items : null,
    };
  }, []);
  const { data, loading, reload } = useAsyncData(loader, []);
  useEffect(() => {
    const openRisk = () => {
      if (window.location.hash !== "#risk") return;
      const target = document.getElementById("risk");
      if (target instanceof HTMLDetailsElement) target.open = true;
    };
    openRisk();
    window.addEventListener("hashchange", openRisk);
    return () => window.removeEventListener("hashchange", openRisk);
  }, []);
  return (
    <div className="space-y-5" data-testid="trader-strategies">
      <div className="flex flex-wrap justify-between gap-3">
        <h1 className="text-2xl font-semibold">
          {family?.name ?? "Strategies"}
        </h1>
        <Link href="/agent?discuss=strategy" className="text-accent">
          Discuss a strategy
        </Link>
      </div>
      {family ? (
        <>
          <Link
            href="/strategies"
            className="inline-flex min-h-11 items-center text-accent"
          >
            ← Back to Strategies
          </Link>
          {data?.brain?.strategies
            .filter((s) => s.spec.kind === family.kind)
            .map((s) => (
              <article
                key={s.version_id}
                className="space-y-2 rounded-card border border-border-subtle p-4"
              >
                <div className="flex flex-wrap justify-between gap-2">
                  <h2 className="font-semibold">
                    {s.spec.symbol} · {s.spec.direction} ·{" "}
                    {s.spec.trigger_timeframe}
                  </h2>
                  <span className="text-sm">
                    {s.status} · v{s.version}
                  </span>
                </div>
                <p className="text-sm text-text-muted">
                  Approval, validation and automation are separate.
                </p>
                <Link
                  href={`/strategy-lab/${s.strategy_id}`}
                  className="inline-flex min-h-11 items-center text-accent"
                >
                  Open configuration &amp; revisions
                </Link>
                <details>
                  <summary className="min-h-11 cursor-pointer text-sm">
                    Rules &amp; evidence
                  </summary>
                  <pre className="whitespace-pre-wrap break-words text-xs">
                    {JSON.stringify(s.spec, null, 2)}
                  </pre>
                </details>
              </article>
            ))}
          {!loading && !data?.brain && (
            <p role="status">Configurations unavailable.</p>
          )}
        </>
      ) : (
        <>
          <h2 className="text-lg font-semibold">Strategy library</h2>
          <div className="grid items-start gap-4 md:grid-cols-2">
            {families.map((f) => {
              const configs = data?.brain?.strategies.filter(
                (s) => s.spec.kind === f.kind,
              );
              const recent = data?.brain?.setups.filter(s =>
                s.family === f.detectionFamily && Date.parse(s.observed_at) >= Date.now() - 7 * 86400000,
              ).sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at));
              return (
                <article
                  key={f.id}
                  data-testid="strategy-family"
                  className="space-y-3 rounded-card border border-border-subtle bg-surface-1 p-4"
                >
                  <h2 className="text-lg font-semibold">{f.name}</h2>
                  <p className="text-sm text-text-secondary">
                    {loading
                      ? "Loading…"
                      : !configs
                        ? "Configurations unavailable"
                        : `${configs.length} configurations`}
                  </p>
                  <div className="flex gap-4 text-sm text-accent">
                    <Link href={`/strategies?family=${f.id}`}>Open</Link>
                    <Link href={`/agent?discuss=${f.id}`}>Ask Agent</Link>
                  </div>
                  <details className="border-t border-border-subtle pt-2">
                    <summary className="min-h-11 cursor-pointer content-center text-sm">
                      Recent detections{recent ? ` (${recent.length})` : ""}
                    </summary>
                    <div className="space-y-3 pt-2 text-sm">
                      <p className="text-text-muted">Last 7 days · market observations</p>
                      {loading ? <p role="status">Loading detections…</p> : !recent ?
                        <p>Detections unavailable.</p> : recent.length === 0 ?
                        <p>No recent detections.</p> : <ul className="space-y-3">
                          {recent.slice(0, 3).map(s => <li key={s.setup_id}>
                            <Link className="text-accent" href={`/strategies/setups/${s.setup_id}?returnTo=${encodeURIComponent("/strategies")}`}>
                              {s.symbol || s.instrument} · {s.direction} · {s.timeframe}
                            </Link>
                            <p className="text-text-muted">{s.state.replaceAll("_", " ")} · {formatDateTime(s.observed_at)}</p>
                          </li>)}
                        </ul>}
                      <Link className="inline-flex min-h-11 items-center text-accent" href={`/strategies/detections?family=${f.detectionFamily}`}>
                        Browse detections
                      </Link>
                    </div>
                  </details>
                </article>
              );
            })}
          </div>
          <Link
            href="/strategies/detections"
            className="inline-flex min-h-11 items-center text-accent"
          >
            Recent detections →
          </Link>
          <ExperimentsPanel initialExperiment={params.get("experiment")} />
          <section className="space-y-3" data-testid="captured-strategy-notes">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-semibold">Captured strategy notes</h2>
              <Link
                href="/journal?tab=knowledge&category=strategies"
                className="inline-flex min-h-11 items-center text-sm text-accent"
              >
                All strategy notes
              </Link>
            </div>
            <p className="text-sm text-text-muted">
              Recent Agent captures. Configuration and approval remain separate.
            </p>
            {loading ? (
              <p role="status">Loading captured notes…</p>
            ) : data?.drafts == null ? (
              <p role="status">Captured notes unavailable.</p>
            ) : data.drafts.length === 0 ? (
              <p className="text-sm text-text-muted">No captured strategy notes yet.</p>
            ) : (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {data.drafts.map((d) => (
                  <details
                    key={d.id}
                    data-testid="captured-strategy-note"
                    className="min-w-0 rounded-card border border-border-subtle bg-surface-1 p-4"
                  >
                    <summary className="min-h-11 cursor-pointer break-words font-medium">
                      {d.title}
                    </summary>
                    <div className="space-y-2 pt-2">
                      <p className="break-words text-sm text-text-secondary">{d.summary}</p>
                      <p className="text-xs text-text-muted">Captured note · v{d.revision}</p>
                      <Link
                        href={`/journal?tab=knowledge&category=strategies&saved=${encodeURIComponent(d.id)}`}
                        className="inline-flex min-h-11 items-center text-sm text-accent"
                      >
                        Open saved note
                      </Link>
                    </div>
                  </details>
                ))}
              </div>
            )}
          </section>
          <section className="space-y-3">
            <h2 className="text-lg font-semibold">Other configurations</h2>
            {data?.library
              ?.filter(
                (s) =>
                  !data.brain?.strategies.some((b) => b.strategy_id === s.id),
              )
              .map((s) => (
                <article
                  key={s.id}
                  className="flex flex-wrap justify-between gap-3 rounded-card border border-border-subtle p-4"
                >
                  <Link href={`/strategy-lab/${s.id}`} className="text-accent">
                    {s.latest_card?.strategy_name || s.name}
                  </Link>
                  <span className="text-sm">{strategyStatusFor(s).label}</span>
                </article>
              ))}
            {!loading && data?.library == null ? (
              <p role="status">Strategy configurations unavailable.</p>
            ) : null}
            {!loading && data?.library?.length === 0 && (
              <p className="text-sm text-text-muted">
                Discuss your rules with Agent to create a draft.
              </p>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={loading}
              onClick={() => void reload()}
            >
              Refresh strategies
            </Button>
          </section>
        </>
      )}
      <details
        className="rounded-card border border-border-subtle p-4"
        id="risk"
      >
        <summary className="min-h-11 cursor-pointer font-semibold">
          Risk policy
        </summary>
        <div className="space-y-3 pt-3">
          <RiskSettingsSummary />
          <Link href="/risk" className="text-accent">
            Configure account limits
          </Link>
          <p className="text-sm text-text-muted">
            Strategy limits remain bounded by the enforced account policy.
          </p>
        </div>
      </details>
    </div>
  );
}
