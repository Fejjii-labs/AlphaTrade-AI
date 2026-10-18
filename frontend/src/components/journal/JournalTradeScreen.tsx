"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { useAsyncData } from "@/hooks/useAsyncData";
import { journalTradeApi } from "@/lib/api/journal-trade";
import { formatDateTime, formatMoney, formatQuantity, humanizeToken } from "@/lib/format";

export function JournalTradeScreen({ tradeId }: { tradeId: string }) {
  const load = useCallback(() => journalTradeApi.detail(tradeId), [tradeId]);
  const { data, loading, error, reload } = useAsyncData(load, [tradeId]);
  const [reflection, setReflection] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [strategy, setStrategy] = useState("");
  const [grade, setGrade] = useState("");
  const [notes, setNotes] = useState("");
  const [tagSaved, setTagSaved] = useState(false);
  const [tagError, setTagError] = useState<string | null>(null);
  const [tagging, setTagging] = useState(false);
  const pending = useRef(false);
  const tagPending = useRef(false);
  useEffect(() => {
    if (!data) return;
    setStrategy(data.trade.strategy_label ?? "");
    setGrade(data.trade.grade ?? "");
    setNotes(data.trade.notes ?? "");
  }, [data]);
  if (loading && !data) return <LoadingState label="Loading this Journal trade…" />;
  if (error || !data) return <ErrorState message={error ?? "This Journal trade is unavailable in your account."} onRetry={() => void reload()} />;
  // A mismatched response must never display another trade under the linked identity.
  if (data.trade.id !== tradeId) return <ErrorState message="The linked Journal trade could not be verified." />;
  const { trade, manual_demo: attempt } = data;
  const evidence = attempt?.evidence;
  const manual = trade.source === "manual_demo_test";
  const cycleMarker = (trade.external_ref ?? "") === "blofin-demo-mirror-cycle";
  const mirrored = !cycleMarker && (trade.source === "blofin_demo_manual"
    || (trade.external_ref ?? "").startsWith("blofin-demo-"));
  const currency = trade.exchange === "BLOFIN_DEMO" ? "USDT" : null;
  const money = (value: string | null | undefined) => `${formatMoney(value)}${currency ? ` ${currency}` : ""}`;
  const fields = [
    ["Venue", trade.exchange ?? "Unavailable"],
    ["Origin", humanizeToken(trade.source)],
    ["Contracts filled", formatQuantity(evidence?.filled_quantity, { maximumFractionDigits: 8 })],
    ["Base quantity", `${formatQuantity(trade.size, { maximumFractionDigits: 8 })}${attempt ? " BTC" : ""}`],
    ["Entry", money(manual ? evidence?.average_fill_price : trade.entry_price)],
    ["Exit", money(manual ? evidence?.exit_price : trade.exit_price)],
    ["Gross trading PnL", money(manual ? evidence?.gross_pnl : trade.gross_pnl)],
    ["Fees", money(manual ? evidence?.fees : trade.fees)],
    ["Funding", money(manual ? evidence?.funding : trade.funding)],
    ["Net PnL", money(manual ? evidence?.net_pnl : trade.net_pnl)],
    ...(mirrored ? [
      ["Leverage", trade.leverage ?? "—"],
      ["Stop", money(trade.planned_stop_price)],
    ] as const : []),
  ];
  async function saveReflection(event: React.FormEvent) {
    event.preventDefault();
    if (pending.current || !reflection.trim()) return;
    pending.current = true;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      await journalTradeApi.reflect(tradeId, reflection.trim());
      setReflection("");
      setSaved(true);
      await reload();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Reflection could not be saved.");
    } finally {
      pending.current = false;
      setSaving(false);
    }
  }
  async function saveTags(event: React.FormEvent) {
    event.preventDefault();
    if (tagPending.current) return;
    tagPending.current = true;
    setTagging(true);
    setTagError(null);
    setTagSaved(false);
    try {
      await journalTradeApi.annotate(tradeId, {
        strategy_label: strategy.trim(),
        grade: grade.trim(),
        notes: notes.trim(),
      });
      setTagSaved(true);
      await reload();
    } catch (err) {
      setTagError(err instanceof Error ? err.message : "Tags could not be saved.");
    } finally {
      tagPending.current = false;
      setTagging(false);
    }
  }
  return <div className="space-y-5" data-testid="journal-trade-detail">
    <PageHeader title={`${trade.symbol} · ${humanizeToken(trade.direction)}`}
      description="Journal trade · recorded execution evidence"
      actions={<Link href="/journal" className="inline-flex min-h-11 items-center text-sm text-accent">All Journal records</Link>} />
    <Card>
      <CardHeader><CardTitle>Execution evidence</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-text-secondary">{evidence ? humanizeToken(evidence.position_status ?? "unverified") : humanizeToken(trade.status)} · Entry {formatDateTime(trade.entry_time)}{trade.exit_time ? ` · Exit ${formatDateTime(trade.exit_time)}` : ""}</p>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          {fields.map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-xs text-text-secondary">{label}</dt><dd className="mt-1 break-words text-sm font-medium tabular-nums">{value}</dd></div>)}
        </dl>
        <p className="text-xs text-text-secondary">Unavailable values (—) remain unverified. Account flatness alone does not establish this trade’s closure.</p>
        {evidence?.missing_evidence[0] ? <p role="status" className="text-sm text-warning">{evidence.missing_evidence[0]}</p> : null}
        {attempt ? <Link href={`/execution/manual-demo/${encodeURIComponent(attempt.command_id)}`} className="inline-flex min-h-11 items-center text-sm text-accent">Open this exact attempt</Link> : null}
        <details className="rounded-control border border-border-subtle p-3 text-sm">
          <summary className="cursor-pointer">Supporting evidence and identities</summary>
          <div className="mt-3 space-y-2 break-words [overflow-wrap:anywhere]">
            <p>Journal trade: {trade.id}</p>
            {attempt ? <><p>Command: {attempt.command_id}</p><p>Native entry order: {evidence?.venue_order_id ?? "Unverified"}</p><p>Historically configured protection: {humanizeToken(evidence?.historical_protection)}</p><p>Active protection: {evidence?.position_status === "closed_verified" ? "Not applicable to verified closed exposure" : humanizeToken(evidence?.protection)}</p><p>Triggered protection: {humanizeToken(evidence?.triggered_protection)}</p><p>Evidence observed: {formatDateTime(evidence?.observed_at)}</p></> : null}
            {evidence?.missing_evidence.map((note) => <p key={note}>{note}</p>)}
            {data.evidence.map((item) => <p key={item.id}>{humanizeToken(item.kind)}: {item.caption ?? item.ref ?? "Recorded evidence"}</p>)}
            {data.rule_checks.map((item) => <p key={item.id}>{humanizeToken(item.rule_key)}: {humanizeToken(item.status)}{item.notes ? ` · ${item.notes}` : ""}</p>)}
          </div>
        </details>
      </CardContent>
    </Card>
    {mirrored ? (
      <Card data-testid="journal-mirror-annotation">
        <CardHeader><CardTitle>Strategy tag</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-text-secondary">
            Tag this mirrored BloFin demo trade so it can be compared by strategy.
            Prices, size, fees, and P&amp;L stay as recorded.
          </p>
          <form onSubmit={(event) => void saveTags(event)} className="space-y-3">
            <Label htmlFor="mirror-strategy">Strategy</Label>
            <Input id="mirror-strategy" value={strategy} maxLength={120} disabled={tagging}
              onChange={(event) => { setStrategy(event.target.value); setTagSaved(false); }} />
            <Label htmlFor="mirror-grade">Grade</Label>
            <Input id="mirror-grade" value={grade} maxLength={2} disabled={tagging}
              placeholder="A+ through D-, or F"
              onChange={(event) => { setGrade(event.target.value); setTagSaved(false); }} />
            <Label htmlFor="mirror-notes">Notes</Label>
            <Textarea id="mirror-notes" value={notes} maxLength={8000} disabled={tagging}
              onChange={(event) => { setNotes(event.target.value); setTagSaved(false); }} />
            <Button type="submit" disabled={tagging}>{tagging ? "Saving…" : "Save strategy tag"}</Button>
            {tagError ? <p role="alert" className="text-sm text-danger">{tagError}</p> : null}
            {tagSaved ? <p role="status" className="text-sm text-success">Strategy tag saved.</p> : null}
          </form>
        </CardContent>
      </Card>
    ) : null}
    <Card>
      <CardHeader><CardTitle>Personal reflection</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-text-secondary">Reflections attach to this trade separately from immutable execution evidence.</p>
        {data.observations.map((note) => <article key={note.id} className="rounded-control border border-border-subtle p-3"><p className="whitespace-pre-wrap break-words text-sm">{note.observation}</p><p className="mt-2 text-xs text-text-secondary">{formatDateTime(note.created_at)}</p></article>)}
        <form onSubmit={(event) => void saveReflection(event)} className="space-y-3">
          <Label htmlFor="trade-reflection">What would you repeat or change?</Label>
          <Textarea id="trade-reflection" value={reflection} maxLength={8000} required disabled={saving} onChange={(event) => { setReflection(event.target.value); setSaved(false); }} />
          <Button type="submit" disabled={saving || !reflection.trim()}>{saving ? "Saving…" : "Attach reflection"}</Button>
          {saveError ? <p role="alert" className="text-sm text-danger">{saveError}</p> : null}
          {saved ? <p role="status" className="text-sm text-success">Reflection attached to this trade.</p> : null}
        </form>
      </CardContent>
    </Card>
  </div>;
}
