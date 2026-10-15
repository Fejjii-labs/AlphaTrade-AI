"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { NativeActivityItem, NativeActivityKind } from "@/lib/api/blofin-activity";
import { nativeIdentity, nativeMoney, nativeTime } from "@/lib/native-activity";
import { formatDateTime } from "@/lib/format";
import { useNativeActivity } from "./useNativeActivity";

const freshness = { fresh: "Fresh at read", stale: "Stale at read", never_synced: "Not synced", unverified: "Account unverified" };

function ActivityRow({ item }: { item: NativeActivityItem }) {
  return <details className="rounded-control border border-border-subtle px-3" data-testid="native-activity-row">
    <summary className="grid min-h-11 cursor-pointer gap-1 py-3 text-sm sm:grid-cols-[1fr_1fr_auto]">
      <span className="font-medium">{item.instrument} · {item.side} · {item.position_side}</span>
      <span className="break-words tabular-nums">{item.quantity} contracts{item.kind === "order" ? " requested" : ""} · {item.price ?? item.average_price ?? "Price unknown"}</span>
      <span className="text-xs text-text-secondary">{nativeTime(item.occurred_at_ms)}</span>
    </summary>
    <div className="space-y-3 border-t border-border-subtle py-3">
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        {[
          ["Source", item.origin === "alphatrade_matched" ? "Native BloFin · linked AlphaTrade command" : "Native BloFin"],
          ["Activity", item.kind === "fill" ? "Individual fill" : `Completed order · ${item.state ?? "State unknown"}`],
          ["Quantity", `${item.quantity} contracts${item.kind === "order" ? " requested" : ""}`],
          ["Accumulated filled quantity", item.filled_quantity == null ? "Unknown" : `${item.filled_quantity} contracts`],
          ["Native price", item.price ?? "Unknown"],
          ["Average price", item.average_price ?? "Unknown"],
          ["Fee", nativeMoney(item.fee, item.fee_currency)],
          ["Native closing PnL", nativeMoney(item.realized_pnl, null)],
          ["Funding", "Unknown"],
          ["Contract multiplier", item.contract_multiplier ?? "Unknown"],
          ["Base / settlement currency", `${item.base_currency ?? "Unknown"} / ${item.settlement_currency ?? "Unknown"}`],
          ["Metadata observed", item.metadata_observed_at ? formatDateTime(item.metadata_observed_at) : "Unknown"],
          ["Native event milliseconds", item.occurred_at_ms],
        ].map(([label, value]) => <div key={label}><dt className="text-xs text-text-secondary">{label}</dt><dd className="break-words tabular-nums">{value}</dd></div>)}
      </dl>
      <p className="text-xs text-text-secondary">Current contract metadata does not establish historical currency conversions or portfolio returns.</p>
      {item.command_id && item.origin === "alphatrade_matched" ? <Link className="inline-flex min-h-11 items-center text-sm text-accent" href={`/execution/manual-demo/${encodeURIComponent(item.command_id)}`}>View matched command</Link> : null}
      <Button className="min-h-11" variant="outline" onClick={event => {
        const details = event.currentTarget.closest("details");
        if (details) { details.open = false; details.querySelector("summary")?.focus(); }
      }}>Back to activity</Button>
    </div>
  </details>;
}

export function NativeActivity({ statistics = false, refreshKey = 0 }: { statistics?: boolean; refreshKey?: number }) {
  const [kind, setKind] = useState<NativeActivityKind>("fill");
  const { page, busy, error, refresh, more } = useNativeActivity(kind, refreshKey);
  const items = page?.identity_status === "verified" ? page.items.filter(item => item.kind === kind) : [];
  const lastSync = page?.coverage.find(stream => stream.kind === kind)?.last_successful_sync;
  return <Card data-testid="native-activity">
    <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
      <CardTitle>BloFin activity</CardTitle>
      <Button className="min-h-11" variant="outline" disabled={busy} onClick={refresh}>{busy ? "Loading…" : "Refresh activity"}</Button>
    </CardHeader>
    <CardContent className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button className="min-h-11" variant={kind === "fill" ? "default" : "outline"} aria-pressed={kind === "fill"} onClick={() => setKind("fill")}>Fills</Button>
        <Button className="min-h-11" variant={kind === "order" ? "default" : "outline"} aria-pressed={kind === "order"} onClick={() => setKind("order")}>Completed orders</Button>
        {page ? <Badge variant={page.freshness === "fresh" && !error ? "success" : "warning"}>{error ? "Refresh failed" : freshness[page.freshness]}</Badge> : null}
      </div>
      <p className="text-xs text-text-secondary">Stored native BloFin demo history · incomplete coverage. Each fill counts once; matched commands are links.</p>
      {page ? <p className="text-xs text-text-secondary">Last successful {kind === "fill" ? "fill" : "order"} sync: {lastSync ? formatDateTime(lastSync) : "unknown"} · Read at {formatDateTime(page.generated_at)}</p> : null}
      {statistics && page?.identity_status === "verified" && kind === "fill" ? <dl className="grid grid-cols-2 gap-3 text-sm" data-testid="native-activity-statistics">
        <div><dt className="text-xs text-text-secondary">Native fills shown</dt><dd className="font-semibold tabular-nums">{items.length}</dd></div>
        <div><dt className="text-xs text-text-secondary">Instruments shown</dt><dd className="font-semibold tabular-nums">{new Set(items.map(item => item.instrument)).size}</dd></div>
      </dl> : null}
      {busy ? <p role="status" className="text-sm">Loading stored activity…</p> : null}
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {page ? <>
        {page.identity_status !== "verified" ? <p role="status">Account identity unverified. History is withheld.</p> : items.length ? <ul className="space-y-2">{items.map(item => <li key={nativeIdentity(page, item)}><ActivityRow item={item} /></li>)}</ul> : <p className="text-sm">No stored {kind === "fill" ? "fills" : "completed orders"} in this window. Coverage remains incomplete.</p>}
        {page.identity_status === "verified" && page.next_cursor ? <Button className="min-h-11" variant="outline" disabled={busy} onClick={more}>Load older activity</Button> : null}
        <details className="text-xs text-text-secondary"><summary className="min-h-11 cursor-pointer py-3">History coverage &amp; age</summary>
          <p>Read at {formatDateTime(page.generated_at)}. This read does not synchronize the exchange.</p>
          {page.coverage.map(stream => <p key={stream.kind}>{stream.kind === "fill" ? "Fills" : "Orders"}: last successful sync {stream.last_successful_sync ? formatDateTime(stream.last_successful_sync) : "unknown"}; {stream.selection === "cursor_sweep" ? "completed-order cursor sweep; time coverage unknown" : <>window {nativeTime(stream.window_begin_ms)} – {nativeTime(stream.window_end_ms)}</>}. {stream.gap_detected ? "A history gap was detected." : "Earlier history is not established."}{stream.last_error_code ? " Synchronization reported an error." : ""}</p>)}
          {page.limitations.map(limit => <p key={limit}>{limit}</p>)}
        </details>
      </> : !busy && !error ? <p className="text-sm">Sign in to read stored activity.</p> : null}
    </CardContent>
  </Card>;
}
