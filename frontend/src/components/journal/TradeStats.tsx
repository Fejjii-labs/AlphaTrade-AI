"use client";

import { useCallback } from "react";

import { Row } from "@/components/ui/row";
import { Stat, StatStrip } from "@/components/ui/stat-strip";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { JournalStatsBucket, JournalTradeStatsMetrics } from "@/lib/api/types";
import { formatMoney, formatPercent } from "@/lib/format";

function signedMoney(value: string | null | undefined): string | null {
  if (value == null) return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return `${n > 0 ? "+" : ""}${formatMoney(value)}`;
}

function pnlClass(value: string | null | undefined): string {
  const n = Number(value);
  if (value == null || !Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "text-positive" : "text-negative";
}

function bucketValue(m: JournalTradeStatsMetrics): string {
  const parts = [`${m.trade_count} ${m.trade_count === 1 ? "trade" : "trades"}`];
  if (m.win_rate != null) parts.push(`${formatPercent(m.win_rate)} win`);
  return parts.join(" · ");
}

function Breakdown({ title, buckets }: { title: string; buckets: JournalStatsBucket[] | null }) {
  return (
    <section className="space-y-1" aria-label={title}>
      <h2 className="text-section">{title}</h2>
      {buckets == null ? (
        <p role="status" className="text-sm text-text-muted">
          Unavailable.
        </p>
      ) : buckets.length === 0 ? (
        <p className="text-sm text-text-muted">No closed trades yet.</p>
      ) : (
        <div className="divide-y divide-border-subtle">
          {buckets.map((b) => (
            <Row
              key={b.key}
              label={b.label}
              value={bucketValue(b.metrics)}
              meta={
                <span className={pnlClass(b.metrics.net_pnl_total)}>
                  {signedMoney(b.metrics.net_pnl_total) ?? "—"}
                </span>
              }
            />
          ))}
        </div>
      )}
    </section>
  );
}

/** Trades → Stats: overall results, then by strategy and by symbol. */
export function TradeStats() {
  const loader = useCallback(async () => {
    const [strategy, symbol] = await Promise.allSettled([
      api.journal.statistics({ group_by: "strategy" }),
      api.journal.statistics({ group_by: "symbol" }),
    ]);
    const byStrategy = strategy.status === "fulfilled" ? strategy.value : null;
    const bySymbol = symbol.status === "fulfilled" ? symbol.value : null;
    return {
      overall: (byStrategy ?? bySymbol)?.overall ?? null,
      byStrategy: byStrategy?.buckets ?? null,
      bySymbol: bySymbol?.buckets ?? null,
    };
  }, []);
  const { data, loading } = useAsyncData(loader, []);
  if (loading) return <p role="status">Loading stats…</p>;
  const m = data?.overall;
  if (!m) return <p role="status">Stats unavailable.</p>;
  return (
    <div className="page-sections" data-testid="trade-stats">
      <StatStrip label="Overall">
        <Stat label="Trades" value={String(m.trade_count)} />
        <Stat label="Win rate" value={m.win_rate == null ? null : formatPercent(m.win_rate)} />
        <Stat
          label="Net P&L"
          value={
            signedMoney(m.net_pnl_total) == null ? null : (
              <span className={pnlClass(m.net_pnl_total)}>{signedMoney(m.net_pnl_total)}</span>
            )
          }
        />
        <Stat label="Profit factor" value={m.profit_factor == null ? null : m.profit_factor.toFixed(2)} />
        <Stat label="Avg R" value={m.average_r == null ? null : m.average_r.toFixed(2)} />
      </StatStrip>
      <Breakdown title="By strategy" buckets={data.byStrategy} />
      <Breakdown title="By symbol" buckets={data.bySymbol} />
    </div>
  );
}
