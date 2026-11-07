"use client";

import { useCallback, useState } from "react";

import { SOURCE_LABELS } from "@/components/dashboard/performance/performance-model";
import { Row } from "@/components/ui/row";
import { Stat, StatStrip } from "@/components/ui/stat-strip";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type {
  JournalStatsBucket,
  JournalTradeSource,
  JournalTradeStatsMetrics,
} from "@/lib/api/types";
import { formatMoney, formatPercent, humanizeToken } from "@/lib/format";

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

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source as JournalTradeSource] ?? humanizeToken(source);
}

/**
 * Trades → Stats: overall results, then by source, strategy and symbol.
 * The endpoint aggregates every closed journal trade (backtest, imported and
 * manual rows included) unless one source is picked, so the headline says
 * which sources it covers instead of implying paper/demo only.
 */
export function TradeStats() {
  const [source, setSource] = useState<JournalTradeSource | "">("");
  const loader = useCallback(async () => {
    const scoped = source ? { source } : {};
    const [strategy, symbol, bySource] = await Promise.allSettled([
      api.journal.statistics({ group_by: "strategy", ...scoped }),
      api.journal.statistics({ group_by: "symbol", ...scoped }),
      source ? Promise.resolve(null) : api.journal.statistics({ group_by: "source" }),
    ]);
    const byStrategy = strategy.status === "fulfilled" ? strategy.value : null;
    const bySymbol = symbol.status === "fulfilled" ? symbol.value : null;
    return {
      overall: (byStrategy ?? bySymbol)?.overall ?? null,
      byStrategy: byStrategy?.buckets ?? null,
      bySymbol: bySymbol?.buckets ?? null,
      bySource:
        bySource.status === "fulfilled"
          ? (bySource.value?.buckets.map((b) => ({ ...b, label: sourceLabel(b.key) })) ?? null)
          : null,
    };
  }, [source]);
  const { data, loading } = useAsyncData(loader, [loader]);
  const picker = (
    <label className="inline-flex flex-wrap items-center gap-2 text-sm text-text-secondary">
      Source
      <select
        aria-label="Stats source"
        value={source}
        onChange={(event) => setSource(event.target.value as JournalTradeSource | "")}
        className="min-h-11 rounded-control border border-border bg-surface-1 px-2 text-sm text-text-primary"
      >
        <option value="">All sources (incl. backtest and imported)</option>
        {(Object.keys(SOURCE_LABELS) as JournalTradeSource[]).map((option) => (
          <option key={option} value={option}>
            {SOURCE_LABELS[option]}
          </option>
        ))}
      </select>
    </label>
  );
  if (loading)
    return (
      <div className="page-sections">
        {picker}
        <p role="status">Loading stats…</p>
      </div>
    );
  const m = data?.overall;
  if (!m)
    return (
      <div className="page-sections">
        {picker}
        <p role="status">Stats unavailable.</p>
      </div>
    );
  const scopeText = source ? sourceLabel(source) : "all sources";
  return (
    <div className="page-sections" data-testid="trade-stats">
      {picker}
      <p className="text-xs text-text-muted" data-testid="trade-stats-scope">
        Closed journal trades from {scopeText}.
        {source
          ? ""
          : " Backtest, imported and manually logged trades are included; pick a source to compare with the dashboard venue."}
      </p>
      <StatStrip label="Overall">
        <Stat label="Trades" value={String(m.trade_count)} />
        <Stat label="Win rate" value={m.win_rate == null ? null : formatPercent(m.win_rate)} />
        <Stat
          label={`Net P&L (${scopeText})`}
          value={
            signedMoney(m.net_pnl_total) == null ? null : (
              <span className={pnlClass(m.net_pnl_total)}>{signedMoney(m.net_pnl_total)}</span>
            )
          }
        />
        <Stat label="Profit factor" value={m.profit_factor == null ? null : m.profit_factor.toFixed(2)} />
        <Stat label="Avg R" value={m.average_r == null ? null : m.average_r.toFixed(2)} />
      </StatStrip>
      {source ? null : <Breakdown title="By source" buckets={data.bySource} />}
      <Breakdown title="By strategy" buckets={data.byStrategy} />
      <Breakdown title="By symbol" buckets={data.bySymbol} />
    </div>
  );
}
