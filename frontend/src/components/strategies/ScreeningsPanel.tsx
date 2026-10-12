"use client";

import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { rejectedBeforeCommit } from "./request-recovery";
import { trendpulseScreen, trendpulseScreenings, trendpulseScreeningDetail, trendpulseCandidate } from "@/lib/api/generated/client";
import { trendpulseScreenRequest } from "@/lib/api/generated/validators";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";
import { recoverySessionId } from "@/lib/auth/recovery-session";
import type { ExperimentVersion } from "@/lib/experiments";

type Page = Awaited<ReturnType<typeof trendpulseScreenings>>;
type Detail = Awaited<ReturnType<typeof trendpulseScreeningDetail>>;
type Request = Parameters<typeof trendpulseScreen>[2];
const prefix = "alphatrade:screening:";
const statuses = { unavailable: "Unavailable", refused: "Rejected", no_setup: "No setup", qualified_research_signal: "Research signal", duplicate: "Already screened" };
const provenance = { live_public_rest: "Public REST", recorded_public_receipts: "Recorded receipts", synthetic_fixture: "Synthetic fixture" };
const time = (at: string) => new Date(at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
function readPending(key: string): Request | null {
  try { const body = JSON.parse(sessionStorage.getItem(key) ?? "null"); return trendpulseScreenRequest(body) ? body : null; }
  catch { return null; }
}
export function ScreeningsPanel({ version }: { version: ExperimentVersion }) {
  const { user, organization } = useAuth();
  const [generation, setGeneration] = useState(sessionGeneration);
  useEffect(() => onSessionCleared(() => {
    setGeneration(sessionGeneration());
  }), []);
  if (!user || !organization) return null;
  const session = recoverySessionId(organization.id, user.id);
  if (!session) return null;
  const scope = `${prefix}${JSON.stringify([organization.id, user.id, session, version.id])}`;
  return <ScopedScreenings key={`${scope}:${generation}`} scope={scope} version={version} />;
}
function ScopedScreenings({ scope, version }: { scope: string; version: ExperimentVersion }) {
  const [page, setPage] = useState<Page | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [pending, setPending] = useState<Request | null>(() => readPending(scope));
  const [trigger, setTrigger] = useState("");
  const [variant, setVariant] = useState(version.configuration.variants[0].key);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const mutation = useRef<AbortController | null>(null);
  useEffect(() => () => mutation.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(false); setPage(null); setDetail(null);
    const operation = selected ? trendpulseScreeningDetail(selected, { signal: controller.signal })
      : trendpulseScreenings(version.experiment_id, version.id, { limit: 5, offset: 0 }, { signal: controller.signal });
    void operation.then(result => {
      if (controller.signal.aborted) return;
      if ("items" in result) setPage(result); else setDetail(result);
    }).catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [version.experiment_id, version.id, refresh, selected]);
  async function submit(original?: Request) {
    if (mutation.current) return;
    const at = new Date(trigger ? `${trigger}Z` : "");
    const body = original ?? { request_id: crypto.randomUUID(), variant_key: variant, trigger_end: Number.isFinite(at.getTime()) ? at.toISOString() : "" };
    if (!trendpulseScreenRequest(body)) { setNotice("Choose a valid closed trigger and variant."); return; }
    // Validate before recording; transport ambiguity retains exact identity and input.
    try { sessionStorage.setItem(scope, JSON.stringify(body)); }
    catch { setNotice("Request recovery storage unavailable. Try again when browser storage is available."); return; }
    setPending(body); setBusy(true); setNotice(null);
    const controller = new AbortController(); mutation.current = controller;
    try {
      const result = await trendpulseScreen(version.experiment_id, version.id, body, { signal: controller.signal });
      if (controller.signal.aborted) return;
      sessionStorage.removeItem(scope); setPending(null); setSelected(result.id); setRefresh(value => value + 1);
    } catch (cause) {
      if (controller.signal.aborted) return;
      // Rejection of a retry does not resolve the original request's uncertainty.
      if (!original && rejectedBeforeCommit(cause, "screening")) {
        sessionStorage.removeItem(scope); setPending(null);
        setNotice(cause instanceof ApiError && cause.status === 503 ? "Research screening is unavailable. Existing history remains readable." : "Screening rejected. Correct the input or permissions before submitting again.");
      } else setNotice("Screening could not be confirmed. Recover the original request before starting another.");
    } finally { if (!controller.signal.aborted) { mutation.current = null; setBusy(false); } }
  }
  async function reviewSetup() {
    if (!detail || mutation.current || pending) return;
    const controller = new AbortController(); mutation.current = controller;
    setBusy(true); setNotice(null);
    try {
      await trendpulseCandidate(detail.id, { signal: controller.signal });
      if (!controller.signal.aborted) setNotice("Setup passed canonical review. Orders still require their existing approval and risk checks.");
    } catch {
      if (!controller.signal.aborted) setNotice("Setup review could not be confirmed. Check the approved strategy, experiment window and account evidence. No order was requested.");
    } finally {
      if (!controller.signal.aborted) { mutation.current = null; setBusy(false); }
    }
  }
  return <section className="space-y-3 rounded-card border border-border-subtle bg-surface-1 p-4" data-testid="screenings">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">Research screening</h3><Button size="sm" variant="outline" disabled={loading || busy} onClick={() => setRefresh(value => value + 1)}>Refresh history</Button></div>
    <p className="text-xs text-text-muted">Research only. A signal is not a trade or an admitted sample. Performance unavailable.</p>
    {selected ? <Button size="sm" variant="ghost" onClick={() => setSelected(null)}>← Back to screening history</Button> : null}
    {loading ? <p role="status">Loading screenings…</p> : error ? <p role="status">Screening history unavailable. Refresh to try again.</p> : detail ? <>
      <p className="text-sm"><Badge>{statuses[detail.status]}</Badge> · {detail.reason.replaceAll("_", " ")}</p>
      <p className="text-xs text-text-muted">{provenance[detail.receipt_provenance]} · {time(detail.decision_at)}</p>
      {version.state === "running" && detail.status === "qualified_research_signal" && detail.receipt_provenance === "live_public_rest" ? <Button size="sm" variant="outline" disabled={busy || !!pending} onClick={() => void reviewSetup()}>Review setup</Button> : null}
      <details><summary className="min-h-11 cursor-pointer">Receipts &amp; evidence</summary><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(detail, null, 2)}</pre></details>
    </> : page?.items.length ? <>
      <ul className="space-y-2">{page.items.map(record => <li key={record.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <div><p>{statuses[record.status]} · {record.reason.replaceAll("_", " ")}</p><p className="text-xs text-text-muted">{provenance[record.receipt_provenance]} · {time(record.decision_at)}</p></div>
        <Button size="sm" variant="ghost" onClick={() => setSelected(record.id)}>Details</Button>
      </li>)}</ul><p className="text-xs text-text-muted">Recent {page.items.length} of {page.total} recorded screenings.</p>
    </> : <p className="text-sm text-text-muted">No screenings recorded.</p>}
    {pending ? <div role="status" className="space-y-2 text-sm"><p>An earlier screening is awaiting confirmation. Its original trigger and identity are retained.</p><Button size="sm" variant="outline" disabled={busy} onClick={() => void submit(pending)}>Recover screening</Button></div> : <details>
      <summary className="min-h-11 cursor-pointer text-sm">Screen a closed trigger</summary>
      <form className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); void submit(); }}>
        <label className="text-sm">Variant<select aria-label="Screening variant" value={variant} onChange={event => setVariant(event.target.value)} className="block min-h-11 bg-surface-0">{version.configuration.variants.map(item => <option key={item.key}>{item.key}</option>)}</select></label>
        <label className="text-sm">Trigger close (UTC)<input aria-label="Trigger close (UTC)" type="datetime-local" value={trigger} onChange={event => setTrigger(event.target.value)} required className="block min-h-11 max-w-full rounded-control bg-surface-0 p-2" /></label>
        <Button size="sm" type="submit" disabled={busy}>Screen trigger</Button>
      </form>
    </details>}
    {notice ? <p role="status" className="text-sm">{notice}</p> : null}
  </section>;
}
