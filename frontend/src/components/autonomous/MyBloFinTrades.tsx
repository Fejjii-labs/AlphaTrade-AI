"use client";

import Link from "next/link";
import { useCallback } from "react";

import { BloFinSyncLine } from "@/components/journal/BloFinSyncLine";
import { NewDemoOrder } from "@/components/journal/NewDemoOrder";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { CanonicalJournalTradeListItem } from "@/lib/api/types";
import { formatDateTime, formatMonetary } from "@/lib/format";

const MINE = ["blofin_demo_manual", "manual_demo_test"] as const;

function when(t: CanonicalJournalTradeListItem): number {
  return new Date(t.exit_time ?? t.entry_time ?? 0).getTime() || 0;
}

/** My own BloFin demo trades (mirrored, or placed from the app): sync, new order, the latest five. */
export function MyBloFinTrades() {
  const loader = useCallback(async () => {
    const pages = await Promise.all(MINE.map((source) => api.journal.listTrades({ source, limit: 5 })));
    return pages
      .flatMap((p) => p.items)
      .filter((t) => t.entry_time && !["planned", "cancelled"].includes(t.status))
      .sort((a, b) => when(b) - when(a))
      .slice(0, 5);
  }, []);
  const { data, loading, error, reload } = useAsyncData(loader, []);
  return (
    <div className="space-y-3" data-testid="my-blofin-trades">
      <BloFinSyncLine onSynced={() => void reload()} />
      {loading ? (
        <p className="text-sm text-text-muted">Loading…</p>
      ) : error || !data ? (
        <p className="text-sm text-text-muted">Trades unavailable.</p>
      ) : data.length === 0 ? (
        <p className="text-sm text-text-muted">No trades yet.</p>
      ) : (
        <ul className="divide-y divide-border-subtle text-sm">
          {data.map((t) => (
            <li key={t.id}>
              <Link href={`/journal?trade_id=${encodeURIComponent(t.id)}`} className="grid min-h-11 grid-cols-[1fr_auto] items-center gap-x-3 hover:text-accent sm:grid-cols-[1fr_auto_auto]">
                <span>
                  {t.symbol} · {t.direction.toLowerCase() === "short" ? "Short" : "Long"}
                  {t.status === "open" ? <span className="text-text-muted"> · Open</span> : null}
                </span>
                <span className="hidden text-xs text-text-muted sm:inline">{formatDateTime(t.exit_time ?? t.entry_time)}</span>
                <span className="tabular-nums">{t.net_pnl ? formatMonetary(t.net_pnl) : "—"}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <NewDemoOrder />
        <Link href="/journal" className="inline-flex min-h-11 items-center text-sm text-accent">
          All trades
        </Link>
      </div>
    </div>
  );
}
