"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/AuthContext";
import { ApiError } from "@/lib/api/client";
import { recoverySessionId } from "@/lib/auth/recovery-session";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";
import { formatDateTime } from "@/lib/format";
import { monitoringAssignments, type AssignmentWrite, type AssignmentsConfiguration, type AssignmentsReplace } from "@/lib/monitoring/assignments-api";

const writeRows = (rows: AssignmentWrite[]) => rows.map(({ id, symbol, strategy_version_id, monitoring_enabled, timeframes, parameters }) => ({ id, symbol, strategy_version_id, monitoring_enabled, timeframes, parameters }));
const reason = (code: string) => code.replaceAll("_", " ");

export function WatcherAssignmentsSection() {
  const { user, organization } = useAuth();
  const identity = user && organization ? `${organization.id}:${user.id}:${recoverySessionId(organization.id, user.id) ?? "unbound"}` : null;
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const [config, setConfig] = useState<AssignmentsConfiguration | null>(null);
  const [rows, setRows] = useState<AssignmentWrite[]>([]);
  const [pending, setPending] = useState<AssignmentsReplace | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [coin, setCoin] = useState("");
  const [versionId, setVersionId] = useState("");
  const [detail, setDetail] = useState<string | null>(null);
  const flight = useRef<AbortController | null>(null);
  const serial = useRef(0);
  const cancel = useCallback(() => { ++serial.current; flight.current?.abort(); }, []);
  const storageKey = identity ? `alphatrade:watcher-assignments:${identity}` : null;

  const refresh = useCallback(async () => {
    if (!identity) return;
    flight.current?.abort();
    const controller = new AbortController();
    flight.current = controller;
    const request = ++serial.current;
    const generation = sessionGeneration();
    try {
      const loaded = await monitoringAssignments.read(controller.signal);
      if (controller.signal.aborted || request !== serial.current || generation !== sessionGeneration() || identityRef.current !== identity) return;
      setConfig(loaded); setRows(writeRows(loaded.assignments)); setCoin(loaded.symbols[0] ?? "");
    } catch (error) {
      if (!controller.signal.aborted && request === serial.current) setMessage(error instanceof Error ? error.message : "Monitoring unavailable.");
    }
  }, [identity]);

  useEffect(() => {
    setConfig(null); setRows([]); setPending(null); setMessage(null); setDetail(null); setVersionId("");
    if (storageKey) {
      try {
        const saved: unknown = JSON.parse(sessionStorage.getItem(storageKey) ?? "null");
        if (saved !== null) { monitoringAssignments.validateRequest(saved); setPending(saved as AssignmentsReplace); }
      } catch { setMessage("Request recovery storage unavailable."); }
    }
    void refresh();
    return cancel;
  }, [storageKey, refresh, cancel]);

  useEffect(() => onSessionCleared(() => {
    ++serial.current; flight.current?.abort(); setConfig(null); setRows([]); setPending(null); setMessage(null); setDetail(null); setBusy(false);
  }), []);

  async function submit(recover = false) {
    if (!config || !identity || !storageKey || busy || (!recover && pending)) return;
    if (identity.endsWith(":unbound")) { setMessage("Sign in again to establish request recovery."); return; }
    const request = recover ? pending : { revision: config.revision, request_key: crypto.randomUUID(), assignments: writeRows(rows) };
    if (!request) return;
    try { monitoringAssignments.validateRequest(request); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Invalid monitoring request."); return; }
    if (!recover) {
      try { sessionStorage.setItem(storageKey, JSON.stringify(request)); }
      catch { setMessage("Request recovery storage unavailable. Nothing was sent."); return; }
      setPending(request);
    }
    const generation = sessionGeneration();
    const controller = new AbortController(); flight.current?.abort(); flight.current = controller;
    setBusy(true); setMessage(null);
    try {
      const saved = await monitoringAssignments.replace(request, controller.signal);
      if (controller.signal.aborted || identityRef.current !== identity || generation !== sessionGeneration()) return;
      try { sessionStorage.removeItem(storageKey); } catch { /* Auth lifecycle invalidates scope independently. */ }
      setPending(null); setConfig(saved); setRows(writeRows(saved.assignments)); setMessage("Monitoring saved.");
    } catch (error) {
      if (controller.signal.aborted || identityRef.current !== identity || generation !== sessionGeneration()) return;
      if (!recover && error instanceof ApiError && [400, 403, 404, 409, 422].includes(error.status)) {
        try { sessionStorage.removeItem(storageKey); } catch { /* Cleanup cannot block local state. */ }
        setPending(null);
      }
      setMessage(error instanceof Error ? error.message : "Reply interrupted. Recover the saved request.");
    } finally {
      if (identityRef.current === identity && generation === sessionGeneration()) setBusy(false);
    }
  }

  const options = config?.available_versions.filter(v => v.symbol === coin || v.symbol === null) ?? [];
  const selected = options.find(v => v.strategy_version_id === versionId);
  const selectedDetail = rows.find(r => r.id === detail);
  const dirty = !!config && JSON.stringify(writeRows(rows)) !== JSON.stringify(writeRows(config.assignments));
  const coinLimit = rows.filter(row => row.symbol === coin).length >= 5;
  if (detail && selectedDetail) {
    const saved = config?.assignments.find(r => r.id === detail);
    const version = config?.available_versions.find(v => v.strategy_version_id === selectedDetail.strategy_version_id);
    return <section className="space-y-3 rounded-card border border-border-subtle p-4" data-testid="monitoring-assignment-details">
      <Button variant="secondary" size="sm" onClick={() => setDetail(null)}>Back to monitoring</Button>
      <h3 className="font-semibold">{version?.strategy_name ?? saved?.strategy_name ?? "Saved strategy"} · {selectedDetail.symbol}</h3>
      <p className="text-sm">Version {version?.version ?? saved?.version} · {selectedDetail.timeframes.join(" / ")}</p>
      <p className="text-xs text-text-muted">Saved parameters are immutable. Capture changes in Agent as a new strategy version.</p>
      <details>
        <summary className="min-h-11 cursor-pointer content-center text-sm">Saved parameters &amp; IDs</summary>
        <pre className="whitespace-pre-wrap break-words text-sm">{JSON.stringify(selectedDetail.parameters, null, 2)}</pre>
        <p className="break-all text-sm text-text-muted">Assignment: {selectedDetail.id} · Version: {selectedDetail.strategy_version_id}</p>
      </details>
      {saved?.latest && <p className="text-xs">{formatDateTime(saved.latest.observed_at)} · {reason(saved.latest.reason_code)} · {saved.latest.candidate_count} candidates{saved.latest.replayed ? " · original scan replay" : ""}</p>}
    </section>;
  }
  return <section className="space-y-3 rounded-card border border-border-subtle p-4" data-testid="monitoring-assignments">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">Coin strategies</h3><Button variant="secondary" size="sm" disabled={busy || !!pending || dirty} onClick={() => void refresh()}>Refresh</Button></div>
    <p className="text-xs text-text-muted">Assign saved versions for monitoring. Trading requires its existing approvals.</p>
    {!config && <p className="text-sm">Loading monitoring…</p>}
    {config && rows.length === 0 && <p className="text-sm text-text-muted">No saved strategy assignments.</p>}
    <div className="grid gap-2 lg:grid-cols-2">{rows.map(row => {
      const saved = config?.assignments.find(r => r.id === row.id);
      const version = config?.available_versions.find(v => v.strategy_version_id === row.strategy_version_id);
      const changed = !saved || saved.monitoring_enabled !== row.monitoring_enabled;
      return <article key={row.id} className="flex flex-col gap-2 rounded border border-border-subtle p-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between" data-testid="monitoring-assignment">
        <div className="min-w-0 w-full sm:w-auto sm:flex-1"><p className="break-words text-sm font-medium sm:truncate">{row.symbol} · {version?.strategy_name ?? saved?.strategy_name ?? "Saved strategy"}</p><p className="text-xs text-text-muted">v{version?.version ?? saved?.version} · {row.timeframes.join(" / ")} · {changed ? "Unsaved" : reason(saved?.reason_code ?? "pending")}</p>
          {!changed && saved?.latest && <p className="text-xs text-text-muted">{reason(saved.latest.reason_code)} · {formatDateTime(saved.latest.observed_at)}</p>}</div>
        <div className="flex flex-wrap items-center gap-2 sm:shrink-0">
        <label className="flex min-h-10 items-center gap-2 text-xs"><input type="checkbox" aria-label={`Monitor ${row.symbol} ${version?.strategy_name ?? saved?.strategy_name ?? "saved strategy"}`} checked={row.monitoring_enabled} disabled={busy || !!pending} onChange={e => setRows(current => current.map(value => value.id === row.id ? { ...value, monitoring_enabled: e.target.checked } : value))} />Monitor</label>
        <Button size="sm" variant="secondary" onClick={() => setDetail(row.id)}>Details</Button>
        <Button size="sm" variant="secondary" disabled={busy || !!pending} onClick={() => setRows(current => current.filter(value => value.id !== row.id))}>Remove</Button>
        </div>
      </article>;
    })}</div>
    {config && <div className="flex flex-wrap items-center gap-2">
      <select aria-label="Assignment coin" className="min-h-10 rounded border border-border bg-surface-2 px-2 text-sm" value={coin} disabled={busy || !!pending} onChange={e => { setCoin(e.target.value); setVersionId(""); }}>{config.symbols.map(symbol => <option key={symbol}>{symbol}</option>)}</select>
      <select aria-label="Saved strategy version" className="min-h-10 min-w-0 w-full max-w-full flex-1 basis-full rounded border border-border bg-surface-2 px-2 text-sm sm:w-auto sm:basis-0" value={versionId} disabled={busy || !!pending} onChange={e => setVersionId(e.target.value)}><option value="">Select saved version</option>{options.map(version => <option key={version.strategy_version_id} value={version.strategy_version_id}>{version.strategy_name} · v{version.version} · {version.timeframes.join(" / ")}</option>)}</select>
      <Button size="sm" variant="secondary" disabled={busy || !!pending || !selected || !selected.timeframes.length || rows.length >= config.max_assignments || coinLimit || rows.some(r => r.symbol === coin && r.strategy_version_id === versionId)} onClick={() => {
        if (selected) setRows(current => [...current, { id: crypto.randomUUID(), symbol: coin, strategy_version_id: selected.strategy_version_id, monitoring_enabled: true, timeframes: [...selected.timeframes], parameters: structuredClone(selected.parameters) }]);
      }}>Add</Button>
      <Link className="text-sm text-accent" href="/agent">Capture a strategy</Link>
      {coin && options.length === 0 && <p className="text-xs text-text-muted">No saved version for {coin}. Capture one in Agent.</p>}
      {coinLimit && <p className="text-xs text-text-muted">Five saved versions per coin.</p>}
    </div>}
    <div className="flex flex-wrap items-center gap-3">
      <Button disabled={busy || !config || !!pending || !dirty} onClick={() => void submit()}>{busy ? "Saving…" : "Save monitoring"}</Button>
      {pending && <><span className="text-xs text-text-muted">Reply uncertain. The original request is retained.</span><Button variant="secondary" disabled={busy || !config} onClick={() => void submit(true)}>Recover saved request</Button></>}
      {message && <p role="status" className="text-xs text-text-muted">{message}</p>}
    </div>
  </section>;
}
