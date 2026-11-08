"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ConfidenceBadge } from "@/components/ConfidenceBadge";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { useAsyncData } from "@/hooks/useAsyncData";
import { PerpetualMarketStatusCard } from "@/components/canonical-decision/PerpetualMarketStatusCard";
import { api } from "@/lib/api";
import type {
  CanonicalMarketMonitorStatusRead,
  MarketAnalyzeResponse,
  MarketSnapshotResponse,
} from "@/lib/api/types";
import { perpetualMarketStatusFromCanonical } from "@/lib/canonical-decision/compose";

// Public market data comes from Binance; the internal mock is a server fallback
// (badged below when used), not something a trader picks.
const EXCHANGE = "binance";
const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

function dataTone(quality: string): "healthy" | "paper" | "warn" | "muted" {
  if (quality === "live") return "healthy";
  if (quality === "stale") return "warn";
  if (quality === "mock") return "paper";
  return "muted";
}

function SnapshotPanel({ snapshot }: { snapshot: MarketSnapshotResponse }) {
  const meta = snapshot.meta;
  const quality = meta.fallback_used || !meta.is_live ? "mock" : meta.is_stale ? "stale" : "live";

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>
            {meta.symbol} · {meta.exchange}
          </CardTitle>
          <StatusBadge label={quality} tone={dataTone(quality)} />
          {meta.fallback_used ? <StatusBadge label="Fallback" tone="warn" /> : null}
          {meta.cache_hit ? <StatusBadge label="Cached" tone="muted" /> : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm text-zinc-300">
        {meta.is_stale ? (
          <p className="rounded-lg border border-amber-900/50 bg-amber-950/20 p-3 text-amber-200">
            Stale data warning: {meta.stale_reason ?? "Data may be outdated."}
          </p>
        ) : null}
        {meta.fallback_used ? (
          <p className="rounded-lg border border-zinc-700 bg-zinc-900/50 p-3 text-zinc-400">
            Using mock fallback — prices are not live exchange data.
          </p>
        ) : null}
        <div className="grid gap-3 md:grid-cols-3">
          <div>
            <p className="text-zinc-500">Compatibility last price</p>
            <p className="text-lg font-medium" data-testid="compatibility-last-price">
              {snapshot.ticker?.last_price ?? "—"}
            </p>
            <p className="text-xs text-zinc-500">Not a current live perpetual price</p>
          </div>
          <div>
            <p className="text-zinc-500">Source</p>
            <p>{meta.source}</p>
          </div>
          <div>
            <p className="text-zinc-500">Retrieved</p>
            <p>{new Date(meta.retrieved_at).toLocaleString()}</p>
          </div>
        </div>
        {snapshot.latest_bar ? (
          <div className="overflow-x-auto rounded-lg border border-zinc-800">
            <table className="min-w-full text-left text-xs">
              <thead className="bg-zinc-900 text-zinc-400">
                <tr>
                  <th scope="col" className="px-3 py-2">Open</th>
                  <th scope="col" className="px-3 py-2">High</th>
                  <th scope="col" className="px-3 py-2">Low</th>
                  <th scope="col" className="px-3 py-2">Close</th>
                  <th scope="col" className="px-3 py-2">Volume</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="px-3 py-2">{snapshot.latest_bar.open}</td>
                  <td className="px-3 py-2">{snapshot.latest_bar.high}</td>
                  <td className="px-3 py-2">{snapshot.latest_bar.low}</td>
                  <td className="px-3 py-2">{snapshot.latest_bar.close}</td>
                  <td className="px-3 py-2">{snapshot.latest_bar.volume}</td>
                </tr>
              </tbody>
            </table>
          </div>
        ) : null}
        {snapshot.indicators ? (
          <div>
            <p className="mb-2 font-medium text-zinc-200">Indicators</p>
            <div className="flex flex-wrap gap-2">
              {snapshot.indicators.rsi != null ? (
                <StatusBadge label={`RSI ${snapshot.indicators.rsi.toFixed(1)}`} tone="muted" />
              ) : null}
              {snapshot.indicators.ema_fast ? (
                <StatusBadge label={`EMA fast ${snapshot.indicators.ema_fast}`} tone="muted" />
              ) : null}
              {snapshot.indicators.macd != null ? (
                <StatusBadge label={`MACD ${snapshot.indicators.macd.toFixed(2)}`} tone="muted" />
              ) : null}
              {snapshot.indicators.atr ? (
                <StatusBadge label={`ATR ${snapshot.indicators.atr}`} tone="muted" />
              ) : null}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function AnalyzePanel({ analysis }: { analysis: MarketAnalyzeResponse }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Strategy signals</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <StatusBadge label={`Data: ${analysis.data_quality}`} tone={dataTone(analysis.data_quality)} />
        {analysis.strategy_signals.length === 0 ? (
          <p className="text-zinc-400">No strategy signals for current context.</p>
        ) : (
          analysis.strategy_signals.map((signal) => (
            <div key={signal.strategy_id} className="rounded-lg border border-zinc-800 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{signal.strategy_id}</span>
                <ConfidenceBadge value={signal.confidence} />
                {signal.direction ? <StatusBadge label={signal.direction} tone="muted" /> : null}
              </div>
              {signal.data_quality_note ? (
                <p className="mt-2 text-amber-300">{signal.data_quality_note}</p>
              ) : null}
              {signal.evidence.length ? (
                <ul className="mt-2 list-disc pl-5 text-zinc-400">
                  {signal.evidence.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

export default function MarketPage() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const exchange = EXCHANGE;
  const [timeframe, setTimeframe] = useState("1h");
  const [analysis, setAnalysis] = useState<MarketAnalyzeResponse | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  const loader = useCallback(
    () => api.market.snapshot({ symbol, exchange, timeframe }),
    [symbol, exchange, timeframe],
  );
  const { data, loading, error, reload } = useAsyncData(loader, [symbol, exchange, timeframe]);
  const [monitor, setMonitor] = useState<CanonicalMarketMonitorStatusRead | null>(null);
  const [monitorError, setMonitorError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMonitorError(null);
    void api.canonical
      .getMarketStatus({ symbol })
      .then((status) => {
        if (!cancelled) setMonitor(status);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setMonitor(null);
          setMonitorError(err instanceof Error ? err.message : "Perpetual status unavailable");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const marketStatus = useMemo(
    () => (monitor ? perpetualMarketStatusFromCanonical(monitor) : null),
    [monitor],
  );

  async function runAnalyze() {
    setAnalyzing(true);
    try {
      const result = await api.market.analyze({ symbol, exchange, timeframe });
      setAnalysis(result);
    } finally {
      setAnalyzing(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Market Monitor</h1>
        <p className="text-sm text-zinc-400">
          Compatibility snapshots are not current live perpetual prices. Canonical USD-M status is
          below. No exchange execution.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Filters</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-4">
          <div className="space-y-2">
            <Label htmlFor="symbol">Symbol</Label>
            <Input id="symbol" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
          </div>
          <div className="space-y-2">
            <p className="text-label text-text-secondary">Exchange</p>
            <p className="min-h-9 py-2 text-sm text-text-primary" data-testid="market-exchange">
              Binance · public market data
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="timeframe">Timeframe</Label>
            <select
              id="timeframe"
              className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
            >
              {TIMEFRAMES.map((tf) => (
                <option key={tf} value={tf}>
                  {tf}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-end gap-2">
            <Button onClick={() => void reload()} disabled={loading}>
              Refresh
            </Button>
            <Button variant="secondary" onClick={() => void runAnalyze()} disabled={analyzing}>
              {analyzing ? "Analyzing…" : "Analyze"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {monitorError ? (
        <ErrorState message={`Perpetual status: ${monitorError}`} />
      ) : null}
      {marketStatus ? <PerpetualMarketStatusCard status={marketStatus} /> : null}
      {loading ? <LoadingState /> : null}
      {error ? <ErrorState message={error} onRetry={() => void reload()} /> : null}
      {data ? <SnapshotPanel snapshot={data} /> : !loading && !error ? <EmptyState title="No snapshot" /> : null}
      {analysis ? <AnalyzePanel analysis={analysis} /> : null}
    </div>
  );
}
