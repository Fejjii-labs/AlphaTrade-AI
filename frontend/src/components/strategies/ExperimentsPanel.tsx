"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { experiments, experimentDetail, experimentTransition } from "@/lib/api/generated/client";
import { ScreeningsPanel } from "./ScreeningsPanel";
import { ApiError } from "@/lib/api/client";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";
import {
  actionsFor, experimentActivity, experimentFamily, latestVersion, lifecycleLabel, sampleSummary,
  type Experiment, type ExperimentAction, type ExperimentPage, type ExperimentVersion,
} from "@/lib/experiments";

function timestamp(value: string) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export function ExperimentsPanel({ initialExperiment }: { initialExperiment?: string | null }) {
  const { user, organization } = useAuth();
  const [generation, setGeneration] = useState(sessionGeneration);
  useEffect(() => onSessionCleared(() => setGeneration(sessionGeneration())), []);
  if (!user || !organization) return null;
  // All local reads, selection and mutation state belong to this authenticated identity.
  return <ScopedExperiments key={JSON.stringify([organization.id, user.id, generation])} initialExperiment={initialExperiment} />;
}

function ScopedExperiments({ initialExperiment }: { initialExperiment?: string | null }) {
  const [page, setPage] = useState<ExperimentPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState<string | null>(initialExperiment ?? null);
  const backFocus = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false); setPage(null);
    void experiments({ limit: 12, offset }, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setPage(result);
    }).catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [offset, refresh]);

  if (selected) return <ExperimentDetails key={selected} id={selected} onBack={() => {
    setSelected(null);
    requestAnimationFrame(() => backFocus.current?.focus());
  }} onChange={() => setRefresh(value => value + 1)} />;

  return (
    <section className="space-y-3" aria-labelledby="experiments-title" data-testid="experiments">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="experiments-title" className="text-lg font-semibold">Experiments</h2>
        <Button size="sm" variant="outline" disabled={loading} onClick={() => setRefresh(value => value + 1)}>Refresh experiments</Button>
      </div>
      <p className="text-sm text-text-muted">Explore variants or validate one configuration. Trading remains separate.</p>
      {loading ? <p role="status">Loading experiments…</p> : error ? <p role="status">Experiments unavailable. Refresh to try again.</p> : !page?.items.length ? (
        <p className="text-sm text-text-secondary">No experiments yet. <Link href="/agent?discuss=strategy" className="text-accent">Discuss a strategy</Link> or <Link href="/knowledge" className="text-accent">capture a document</Link>.</p>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {page.items.map(experiment => {
              const version = latestVersion(experiment);
              if (!version) return null;
              const activity = experimentActivity(version)[0];
              return (
                <article key={experiment.id} className="space-y-2 rounded-card border border-border-subtle bg-surface-1 p-3 sm:space-y-3 sm:p-4" data-testid="experiment-card">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <h3 className="font-semibold">{experiment.name}</h3>
                    <Badge variant="info">{version.configuration.mode === "exploration" ? "Exploration" : "Validation"}</Badge>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-secondary">
                    <Badge>{lifecycleLabel[version.state]}</Badge>
                    <span>{version.configuration.symbols.join(", ")} · {version.configuration.timeframes.join(", ")}</span>
                    <span>{version.configuration.account.source === "blofin_demo" ? "BloFin demo" : "Simulation"}</span>
                  </div>
                  <p className="text-xs text-text-muted">{activity ? <>{activity[0]} <time dateTime={activity[1]}>{timestamp(activity[1])}</time></> : "Activity unavailable"}</p>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs text-text-muted">Performance unavailable</p>
                    <Button size="sm" variant="outline" aria-label={`Open ${experiment.name}`}
                      onClick={event => { backFocus.current = event.currentTarget; setSelected(experiment.id); }}>Details</Button>
                  </div>
                  <details className="text-xs text-text-secondary">
                    <summary className="cursor-pointer py-1">Configuration summary</summary>
                    <p className="pt-2">{experimentFamily[version.configuration.family]} · v{version.version}</p>
                    <p>{sampleSummary(version)} · {version.configuration.variants.length} {version.configuration.variants.length === 1 ? "variant" : "variants"}</p>
                  </details>
                </article>
              );
            })}
          </div>
          {page.total > page.limit ? <div className="flex flex-wrap items-center gap-3 text-sm">
            <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - page.limit))}>Previous</Button>
            <span>{offset + 1}–{offset + page.items.length} of {page.total}</span>
            <Button size="sm" variant="outline" disabled={offset + page.items.length >= page.total} onClick={() => setOffset(offset + page.limit)}>Next</Button>
          </div> : null}
        </>
      )}
    </section>
  );
}

function ExperimentDetails({ id, onBack, onChange }: { id: string; onBack: () => void; onChange: () => void }) {
  const [detail, setDetail] = useState<Experiment | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const mutation = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => { heading.current?.focus(); return () => mutation.current?.abort(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false); setDetail(null);
    void experimentDetail(id, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) { setDetail(result); setUncertain(false); setNotice(null); }
    }).catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, refresh]);
  const version = detail && latestVersion(detail);

  async function transition(action: ExperimentAction, current: ExperimentVersion) {
    if (mutation.current || uncertain) return;
    const controller = new AbortController();
    mutation.current = controller; setBusy(true); setNotice(null);
    try {
      const result = await experimentTransition(id, current.id, { action, expected_revision: current.revision }, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setDetail(old => old && ({ ...old, versions: old.versions.map(item => item.id === result.id ? result : item) }));
      setNotice(action === "start" ? "Experiment started. Trading remains inactive." : `Experiment ${lifecycleLabel[result.state].toLowerCase()}.`);
      onChange();
    } catch (cause) {
      if (controller.signal.aborted) return;
      setUncertain(true);
      setNotice(cause instanceof ApiError && cause.status === 403
        ? "This change requires Trader or Owner permission."
        : "Change could not be confirmed. Refresh before retrying.");
    } finally {
      if (!controller.signal.aborted) { mutation.current = null; setBusy(false); }
    }
  }

  return (
    <section className="space-y-4" data-testid="experiment-details">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" onClick={onBack}>← Back to experiments</Button>
        <Button size="sm" variant="outline" disabled={loading || busy} onClick={() => setRefresh(value => value + 1)}>Refresh experiment</Button>
      </div>
      <h2 ref={heading} tabIndex={-1} className="text-xl font-semibold focus:outline-none">{detail?.name ?? "Experiment"}</h2>
      {loading ? <p role="status">Loading experiment…</p> : error || !version ? <p role="status">Experiment unavailable. Refresh to try again.</p> : (
        <>
          <div className="flex flex-wrap gap-2"><Badge variant="info">{version.configuration.mode === "exploration" ? "Exploration" : "Validation"}</Badge><Badge>{lifecycleLabel[version.state]}</Badge><span className="text-sm text-text-muted">v{version.version}</span></div>
          <p className="text-sm text-text-secondary">{experimentFamily[version.configuration.family]} · {version.configuration.symbols.join(", ")} · {version.configuration.timeframes.join(", ")}</p>
          <p className="text-sm text-text-muted">Starting an experiment changes its research lifecycle. It does not activate trading.</p>
          <div className="flex flex-wrap gap-2">
            {actionsFor(version).map(({ action, label }) => <Button key={action} size="sm" variant="outline" disabled={busy || uncertain} onClick={() => void transition(action, version)}>{label}</Button>)}
          </div>
          {version.state === "pending_approval" ? <p className="text-sm text-text-muted">Owner approval of the exact configuration is required.</p> : null}
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-3 rounded-card border border-border-subtle bg-surface-1 p-4">
              <h3 className="font-semibold">Recent activity</h3>
              <ul className="space-y-2 text-sm">{experimentActivity(version).slice(0, 4).map(([label, at]) => <li key={label} className="flex justify-between gap-3"><span>{label}</span><time dateTime={at} className="text-text-muted">{timestamp(at)}</time></li>)}</ul>
              <p className="text-xs text-text-muted">Recorded lifecycle activity. Signal and fill activity is not available here.</p>
            </div>
            <div className="space-y-3 rounded-card border border-border-subtle bg-surface-1 p-4">
              <h3 className="font-semibold">Samples &amp; results</h3>
              <p className="text-sm">{sampleSummary(version)} · {version.configuration.sample_target.kind === "closed_trade" ? "closed trades" : "setup observations"}</p>
              <ul className="space-y-1 text-sm">{version.configuration.variants.map(variant => <li key={variant.key}>{variant.key}: {version.sample_counts[variant.key] ?? 0} / {version.configuration.sample_target.minimum} minimum</li>)}</ul>
              <p className="text-sm text-text-muted">Performance unavailable</p>
              <p className="text-xs text-text-muted">{version.configuration.account.source === "blofin_demo" ? "BloFin demo" : "Internal simulation"} samples remain separate. No portfolio return is inferred.</p>
            </div>
          </div>
          {version.configuration.family === "trendpulse_1r/v1" ? <ScreeningsPanel key={version.id} version={version} /> : null}
          <details className="rounded-card border border-border-subtle p-4">
            <summary className="min-h-11 cursor-pointer font-semibold">Configuration &amp; evidence</summary>
            <div className="space-y-3 pt-3 text-sm">
              <Link href={`/strategy-lab/${version.configuration.strategy_id}`} className="text-accent">View authored strategy</Link>
              <p>Approval expires: {version.authorized_until ? timestamp(version.authorized_until) : "Not approved"}. Execution runtime: inactive.</p>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-control bg-surface-0 p-3 text-xs">{JSON.stringify({ experiment_id: id, versions: detail?.versions }, null, 2)}</pre>
            </div>
          </details>
        </>
      )}
      {notice ? <p role="status" className="text-sm">{notice}</p> : null}
    </section>
  );
}
