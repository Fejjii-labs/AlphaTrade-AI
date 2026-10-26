"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type {
  BacktestRun,
  BacktestRunCreate,
  BacktestSplitMode,
  PaginatedBacktestRuns,
  PaginatedBacktestTrades,
  Timeframe,
} from "@/lib/api/types";
import { formatDate } from "@/lib/utils";

type Props = {
  strategyId: string;
  onRun: (body: BacktestRunCreate) => Promise<BacktestRun>;
  onLoadTrades: (runId: string) => Promise<PaginatedBacktestTrades>;
  onListRuns?: () => Promise<PaginatedBacktestRuns>;
  latestRun?: BacktestRun | null;
};

const SPLIT_MODES: { value: BacktestSplitMode; label: string }[] = [
  { value: "none", label: "None" },
  { value: "holdout", label: "Holdout" },
  { value: "rolling", label: "Rolling walk-forward" },
];

export function BacktestPanel({ strategyId, onRun, onLoadTrades, onListRuns, latestRun }: Props) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("4h");
  const [startDate, setStartDate] = useState("2024-01-01");
  const [endDate, setEndDate] = useState("2024-06-01");
  const [capital, setCapital] = useState("10000");
  const [feesBps, setFeesBps] = useState("4");
  const [slippageBps, setSlippageBps] = useState("5");
  const [fundingRateBps, setFundingRateBps] = useState("0");
  const [riskPct, setRiskPct] = useState("1");
  const [runnerTrailPct, setRunnerTrailPct] = useState("1.5");
  const [splitMode, setSplitMode] = useState<BacktestSplitMode>("none");
  const [oosFraction, setOosFraction] = useState("0.3");
  const [windowBars, setWindowBars] = useState("500");
  const [stepBars, setStepBars] = useState("100");
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<BacktestRun | null>(latestRun ?? null);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [trades, setTrades] = useState<PaginatedBacktestTrades | null>(null);
  const [error, setError] = useState<string | null>(null);
  const idempotencyKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!onListRuns) return;
    void onListRuns()
      .then((listing) => setRuns(listing.items))
      .catch(() => {
        /* list is optional */
      });
  }, [onListRuns, run?.id]);

  function ensureIdempotencyKey(): string {
    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current = crypto.randomUUID();
    }
    return idempotencyKeyRef.current;
  }

  function resetIdempotencyKey() {
    idempotencyKeyRef.current = null;
  }

  function buildAssumptions() {
    const assumptions: BacktestRunCreate["assumptions"] = {
      symbol,
      exchange: "binance",
      timeframe: timeframe as Timeframe,
      start_date: startDate || null,
      end_date: endDate || null,
      initial_capital: capital,
      fees_bps: feesBps,
      slippage_bps: slippageBps,
      funding_rate_bps_per_8h: fundingRateBps,
      risk_per_trade_pct: riskPct,
      runner_trail_pct: runnerTrailPct,
    };

    if (splitMode !== "none") {
      assumptions.split_config = {
        mode: splitMode,
        oos_fraction: Number(oosFraction),
        window_bars: splitMode === "rolling" ? Number(windowBars) : null,
        step_bars: splitMode === "rolling" ? Number(stepBars) : null,
      };
    }

    return assumptions;
  }

  async function handleRun() {
    setBusy(true);
    setError(null);
    const idempotency_key = ensureIdempotencyKey();
    try {
      const result = await onRun({
        assumptions: buildAssumptions(),
        idempotency_key,
      });
      resetIdempotencyKey();
      setRun(result);
      if (result.id) {
        const listing = await onLoadTrades(result.id);
        setTrades(listing);
      }
      if (onListRuns) {
        const listing = await onListRuns();
        setRuns(listing.items);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Backtest failed");
    } finally {
      setBusy(false);
    }
  }

  const metrics = run?.result?.metrics;
  const limitations = run?.result?.limitations ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Backtest v2 (historical simulation)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm text-zinc-300">
        <p className="text-zinc-400">
          Historical simulation only — not a guarantee of future performance. Real trading remains
          disabled.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1">
            <span className="text-zinc-400">Symbol</span>
            <Input value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-zinc-400">Timeframe</span>
            <Input value={timeframe} onChange={(e) => setTimeframe(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-zinc-400">Start date</span>
            <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-zinc-400">End date</span>
            <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-zinc-400">Initial capital</span>
            <Input value={capital} onChange={(e) => setCapital(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-zinc-400">Risk % / trade</span>
            <Input value={riskPct} onChange={(e) => setRiskPct(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-zinc-400">Fees (bps)</span>
            <Input value={feesBps} onChange={(e) => setFeesBps(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-zinc-400">Slippage (bps)</span>
            <Input value={slippageBps} onChange={(e) => setSlippageBps(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-zinc-400">Funding rate (bps / 8h)</span>
            <Input value={fundingRateBps} onChange={(e) => setFundingRateBps(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-zinc-400">Runner trail %</span>
            <Input value={runnerTrailPct} onChange={(e) => setRunnerTrailPct(e.target.value)} />
          </label>
        </div>

        <fieldset className="space-y-3 rounded-lg border border-zinc-800 p-3">
          <legend className="px-1 text-zinc-400">Walk-forward split</legend>
          <label className="block space-y-1">
            <span className="text-zinc-400">Mode</span>
            <select
              className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
              value={splitMode}
              onChange={(e) => setSplitMode(e.target.value as BacktestSplitMode)}
            >
              {SPLIT_MODES.map((mode) => (
                <option key={mode.value} value={mode.value}>
                  {mode.label}
                </option>
              ))}
            </select>
          </label>
          {splitMode !== "none" ? (
            <label className="block space-y-1">
              <span className="text-zinc-400">OOS fraction (0–1)</span>
              <Input value={oosFraction} onChange={(e) => setOosFraction(e.target.value)} />
            </label>
          ) : null}
          {splitMode === "rolling" ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1">
                <span className="text-zinc-400">Window bars</span>
                <Input value={windowBars} onChange={(e) => setWindowBars(e.target.value)} />
              </label>
              <label className="space-y-1">
                <span className="text-zinc-400">Step bars</span>
                <Input value={stepBars} onChange={(e) => setStepBars(e.target.value)} />
              </label>
            </div>
          ) : null}
        </fieldset>

        <Button disabled={busy} onClick={() => void handleRun()}>
          {busy ? "Running…" : "Run backtest"}
        </Button>
        {error ? <p className="text-red-400">{error}</p> : null}
        {run ? (
          <div className="space-y-2 rounded-lg border border-zinc-800 p-3">
            <p>
              Status: {run.status}
              {run.id ? (
                <>
                  {" "}
                  ·{" "}
                  <Link href={`/backtests/${run.id}`} className="text-sky-400 hover:underline">
                    View run detail
                  </Link>
                </>
              ) : null}
            </p>
            {run.result ? (
              <>
                <p>
                  Recommendation:{" "}
                  <span className="text-amber-300">{run.result.recommendation}</span>
                </p>
                {metrics ? (
                  <ul className="grid gap-1 sm:grid-cols-2">
                    <li>Trades: {metrics.trade_count}</li>
                    <li>Win rate: {(metrics.win_rate * 100).toFixed(1)}%</li>
                    <li>Profit factor: {metrics.profit_factor?.toFixed(2)}</li>
                    <li>Max DD: {metrics.max_drawdown_pct?.toFixed(1)}%</li>
                    <li>Net PnL: {metrics.net_pnl}</li>
                    <li>Return: {metrics.return_pct?.toFixed(2)}%</li>
                  </ul>
                ) : null}
                {limitations.length ? (
                  <p className="text-amber-400">Limitations: {limitations.join(" ")}</p>
                ) : null}
                <p className="text-xs text-zinc-500">
                  {run.result.note ??
                    "Historical simulation only — not a guarantee of future performance. Real trading remains disabled."}
                </p>
              </>
            ) : null}
          </div>
        ) : null}
        {trades?.items.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-zinc-400">
                  <th className="p-1">Entry</th>
                  <th className="p-1">Exit</th>
                  <th className="p-1">Net PnL</th>
                  <th className="p-1">Reason</th>
                </tr>
              </thead>
              <tbody>
                {trades.items.slice(0, 10).map((t) => (
                  <tr key={`${t.entry_time}-${t.exit_time}`} className="border-t border-zinc-800">
                    <td className="p-1">{t.entry_price}</td>
                    <td className="p-1">{t.exit_price}</td>
                    <td className="p-1">{t.net_pnl}</td>
                    <td className="p-1">{t.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {runs.length ? (
          <div className="space-y-2">
            <p className="text-zinc-400">Recent runs</p>
            <ul className="space-y-1 text-xs">
              {runs.slice(0, 8).map((item) => (
                <li key={item.id} className="flex flex-wrap items-center gap-2">
                  <Link href={`/backtests/${item.id}`} className="text-sky-400 hover:underline">
                    Run
                  </Link>
                  <span className="text-zinc-500">{item.status}</span>
                  <span className="text-zinc-600">{formatDate(item.created_at)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="text-xs text-zinc-500">Strategy ID: {strategyId}</p>
      </CardContent>
    </Card>
  );
}
