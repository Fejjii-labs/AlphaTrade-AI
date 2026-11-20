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

  const loadTrades = useCallback(async () => {
    if (stats || savedId) return null;
    const pages = await Promise.all(
      TRADE_SOURCES.map((source) => api.journal.listTrades({ source, limit: TRADE_LIMIT })),
    );
    return newestFirst(pages.flatMap((page) => page.items).filter(isBloFinDemoTrade));
  }, [stats, savedId]);
  const loadNotes = useCallback(
    () => stats || savedId
      ? Promise.resolve(null)
      : savedEntries.list({ view: "journal", category: "journal", limit: 20, offset: 0 }),
    [stats, savedId],
  );
  const loadLinked = useCallback(
    () => !stats && savedId ? savedEntries.get(savedId) : Promise.resolve(null),
    [stats, savedId],
  );

  const { data: trades, loading, reload } = useAsyncData(loadTrades, [loadTrades]);
  const { data: notes } = useAsyncData(loadNotes, [loadNotes]);
  const {
    data: linked, loading: linkedLoading, reload: reloadLinked,
  } = useAsyncData(loadLinked, [loadLinked]);

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
  const visible = trades?.filter((t) =>
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
          {linkedLoading ? (
            <p role="status">Loading…</p>
          ) : linked ? (
            <SavedEntryDetail key={linked.id} entry={linked} onChanged={() => void reloadLinked()} />
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
          ) : trades == null ? (
            <p role="alert">Trades unavailable.</p>
          ) : (
            <JournalTradesTable trades={visible ?? []} onOpen={remember} />
          )}
          <BloFinFills />
          {notes?.items.length ? (
            <details className="text-sm" data-testid="saved-notes">
              <summary className="min-h-11 cursor-pointer py-2 text-text-secondary">Notes ({notes.items.length})</summary>
              <ul className="divide-y divide-border-subtle">
                {notes.items.map((note) => (
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
