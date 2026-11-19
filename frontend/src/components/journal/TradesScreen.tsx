"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import { LinkTabs } from "@/components/ui/link-tabs";
import { PageHeader } from "@/components/ui/page-header";
import { useAuth } from "@/contexts/AuthContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import { savedEntries } from "@/lib/api/saved-entries";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";
import type { CanonicalJournalTradeListItem } from "@/lib/api/types";
import { isBloFinDemoTrade } from "@/lib/journal-activity";

import { BloFinFills, BloFinSyncLine } from "./BloFinSyncLine";
import { SavedEntryDetail } from "./JournalKnowledgeWorkspace";
import { JournalReturnLink, journalReturnKey } from "./JournalReturnLink";
import { JournalTradesTable } from "./JournalTradesTable";
import { NewDemoOrder } from "./NewDemoOrder";
import { TradeStats } from "./TradeStats";

// Bot executions, my mirrored BloFin trades, and orders placed from the app.
const TRADE_SOURCES = ["paper_execution", "blofin_demo_manual", "manual_demo_test"] as const;
const TRADE_LIMIT = 200;

function tradeTime(t: CanonicalJournalTradeListItem): number {
  return new Date(t.exit_time ?? t.entry_time ?? 0).getTime() || 0;
}

function newestFirst(trades: CanonicalJournalTradeListItem[]): CanonicalJournalTradeListItem[] {
  return [...trades].sort((a, b) => tradeTime(b) - tradeTime(a));
}

/** Trades page: BloFin demo trades with P&L and fees, a Stats tab, and saved notes. */
export function TradesScreen() {
  const { user, organization } = useAuth();
  const [session, setSession] = useState(sessionGeneration);
  useEffect(() => onSessionCleared(() => setSession(sessionGeneration())), []);
  return <TradesContent key={JSON.stringify([organization?.id, user?.id, session])} />;
}

function TradesContent() {
  const params = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const { user, organization } = useAuth();
  const stats = params.get("tab") === "stats";
  const savedId = params.get("saved");
  const query = params.get("q") ?? "";

  const loader = useCallback(async () => {
    if (stats) return null;
    if (savedId) {
      const linked = await savedEntries.get(savedId).catch(() => null);
      return { trades: [], notes: [], linked };
    }
    const [trades, notes] = await Promise.allSettled([
      Promise.all(TRADE_SOURCES.map((source) => api.journal.listTrades({ source, limit: TRADE_LIMIT }))),
      savedEntries.list({ view: "journal", category: "journal", limit: 20, offset: 0 }),
    ]);
    return {
      trades: trades.status === "fulfilled" ? newestFirst(trades.value.flatMap((p) => p.items).filter(isBloFinDemoTrade)) : null,
      notes: notes.status === "fulfilled" ? notes.value.items : [],
      linked: null,
    };
  }, [stats, savedId]);
  const { data, loading, reload } = useAsyncData(loader, [stats, savedId]);

  const returnKey = journalReturnKey(user?.id, organization?.id);
  const remember = () => {
    try {
      sessionStorage.setItem(returnKey, JSON.stringify({ href: `${pathname}?${params}`, scroll: window.scrollY }));
    } catch {
      /* storage unavailable */
    }
  };

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    router.replace(`${pathname}?${next}`, { scroll: false });
  }

  const needle = query.toLowerCase();
  const visible = data?.trades?.filter((t) =>
    `${t.symbol} ${t.direction} ${t.strategy_label ?? ""}`.toLowerCase().includes(needle),
  );

  return (
    <div className="page-sections" data-testid="trades-screen">
      <PageHeader title="Trades" actions={<NewDemoOrder />} />
      <LinkTabs
        aria-label="Trades views"
        activeId={stats ? "stats" : "trades"}
        items={[
          { id: "trades", label: "Trades", href: "/journal" },
          { id: "stats", label: "Stats", href: "/journal?tab=stats" },
        ]}
      />
      {stats ? (
        <TradeStats />
      ) : savedId ? (
        <>
          <JournalReturnLink />
          {loading ? (
            <p role="status">Loading…</p>
          ) : data?.linked ? (
            <SavedEntryDetail key={data.linked.id} entry={data.linked} onChanged={() => void reload()} />
          ) : (
            <p role="alert">Note unavailable.</p>
          )}
        </>
      ) : (
        <>
          <BloFinSyncLine onSynced={() => void reload()} />
          <Input
            aria-label="Search trades"
            placeholder="Search coin or strategy"
            value={query}
            onChange={(e) => setParam("q", e.target.value)}
          />
          {loading ? (
            <p role="status">Loading trades…</p>
          ) : data?.trades == null ? (
            <p role="alert">Trades unavailable.</p>
          ) : (
            <JournalTradesTable trades={visible ?? []} onOpen={remember} />
          )}
          <BloFinFills />
          {data?.notes.length ? (
            <details className="text-sm" data-testid="saved-notes">
              <summary className="min-h-11 cursor-pointer py-2 text-text-secondary">Notes ({data.notes.length})</summary>
              <ul className="divide-y divide-border-subtle">
                {data.notes.map((note) => (
                  <li key={note.id}>
                    <Link
                      onClick={remember}
                      href={`/journal?saved=${encodeURIComponent(note.id)}`}
                      className="flex min-h-11 items-center text-accent"
                    >
                      {note.title}
                    </Link>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      )}
    </div>
  );
}
