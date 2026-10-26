"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { TIMEFRAMES, type Timeframe, type TradeDirection } from "@/lib/api/types";
import { displayedBrainSetup, useBrainSetupClock } from "@/hooks/useBrainSetupClock";
import { useAsyncData } from "@/hooks/useAsyncData";
import { readSfpParameters, SfpParameterFields } from "./SfpParameterFields";

const EMPTY_SETUPS: never[] = [];

export function StrategyBrainPanel({ family }: { family: "nested" | "sfp" }) {
  const sfp = family === "sfp";
  const label = sfp ? "SFP" : "Nested";
  const kind = sfp ? "swing_failure_pattern/v1" : "operational_nested_continuation/v1";
  const loader = useCallback(() => api.strategyBrain.overview(), []);
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const now = useBrainSetupClock(data?.setups ?? EMPTY_SETUPS);
  const [symbol, setSymbol] = useState("");
  const [direction, setDirection] = useState<TradeDirection>("long");
  const [timeframe, setTimeframe] = useState<Timeframe>("15m");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const strategies = data?.strategies.filter(strategy => strategy.spec.kind === kind) ?? [];
  const setups = data?.setups.filter(setup => sfp ? setup.family === "sfp" : setup.family !== "sfp") ?? [];
  const mutate = async (action: () => Promise<unknown>) => {
    setBusy(true); setMessage(null);
    try { await action(); await reload(); }
    catch (failure) { setMessage(failure instanceof Error ? failure.message : "Strategy action failed"); }
    finally { setBusy(false); }
  };
  return <Card className="mb-6">
    <CardHeader><CardTitle>{sfp ? "SFP · Swing Failure Pattern" : "Nested Continuation"}</CardTitle>
      <p className="text-sm text-text-muted">{sfp ? "Structural sweep and reclaim" : "Operational structure proxy"} · paper only · provisional research rules</p>
    </CardHeader>
    <CardContent className="space-y-4">
      {loading && !data && <p>Loading stored setups…</p>}
      {error && <p role="alert">{error}</p>}
      {message && <p role="alert">{message}</p>}
      {data && <>
        <Button variant="secondary" disabled={loading || busy} onClick={() => void reload()}>Refresh stored {label} setups</Button>
        <p className="text-sm">Watcher markets: {data.watched_symbols.join(", ") || "none enabled"}</p>
        <form aria-label={`${label} research draft`} className="space-y-3" onSubmit={event => {
          event.preventDefault();
          const form = event.currentTarget;
          void mutate(() => {
            const binding = { symbol: symbol || data.watched_symbols[0]!, direction, trigger_timeframe: timeframe };
            if (!binding.symbol) throw new Error("Select an enabled Watcher market");
            return sfp ? api.strategyBrain.createSfp({ ...binding, parameters: readSfpParameters(form), paper_only: true }) : api.strategyBrain.createNested(binding);
          });
        }}>
          <fieldset disabled={busy} className="flex flex-wrap items-center gap-3">
            <label>Market <select aria-label={`${label} market`} value={symbol || data.watched_symbols[0] || ""} onChange={e => setSymbol(e.target.value)} className="bg-surface">
              {data.watched_symbols.map(value => <option key={value}>{value}</option>)}
            </select></label>
            <label>Direction <select aria-label={`${label} direction`} value={direction} onChange={e => setDirection(e.target.value as TradeDirection)} className="bg-surface">
              <option value="long">{sfp ? "Bullish sweep and reclaim" : "Bullish proxy"}</option><option value="short">{sfp ? "Bearish sweep and reclaim" : "Bearish mirror"}</option>
            </select></label>
            <label>Timeframe <select aria-label={`${label} timeframe`} value={timeframe} onChange={e => setTimeframe(e.target.value as Timeframe)} className="bg-surface">
              {TIMEFRAMES.map(value => <option key={value} value={value}>{value}</option>)}
            </select></label>
          </fieldset>
          {sfp && <SfpParameterFields disabled={busy} />}
          <Button type="submit" disabled={busy || !data.watched_symbols.length}>Create {sfp ? "SFP " : ""}research draft</Button>
        </form>
        <p className="text-sm text-text-muted">Each version binds one market, direction and timeframe. Validation and expectancy must be assessed separately for each timeframe. Bybit has no native 3d candles; scans require complete history from the selected provider.</p>
        {strategies.map(strategy => <div key={strategy.version_id} className="space-y-2 rounded border border-border p-3">
          <h3>{strategy.name} · version {strategy.version} · {strategy.status}</h3>
          <p className="text-sm">{strategy.spec.symbol} · {strategy.spec.trigger_timeframe} · {strategy.spec.direction}. Insufficient history for expectancy.</p>
          <details><summary>Review versioned rules</summary><pre className="overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(strategy.spec.parameters, null, 2)}</pre>
            <p className="text-sm">{sfp ? "Causal structural level, sweep, reclaim and closed confirmation. Approval enables governed research scans and alerts. No SFP entry, stop, target or execution plan is authorized." : "Closed impulse break after a controlled pullback; stop at the pullback extreme; measured impulse target. Existing risk and paper execution gates remain required."}</p>
          </details>
          {!["approved", "active", "paused", "retired"].includes(strategy.status) && <Button disabled={busy} variant="secondary" onClick={() => void mutate(async () => {
            const result = await api.strategies.compileVersion(strategy.strategy_id, strategy.version_id);
            if (result.status !== "executable") throw new Error(result.failures.map(f => f.message).join("; ") || "Rules did not compile");
            await api.strategies.approveVersion(strategy.strategy_id, strategy.version_id, {confirm: "I confirm"});
          })}>Approve these {sfp ? "research" : "paper"} rules</Button>}
          <Link href={`/strategy-lab/${strategy.strategy_id}`} className="ml-3 text-sm underline">Strategy details</Link>
        </div>)}
        {!setups.length && <p>No stored {label} setups yet. The existing Watcher must scan an approved version.</p>}
        <ul className="space-y-3">{setups.map(stored => { const setup = displayedBrainSetup(stored, now); return <li key={setup.setup_id} className="rounded border border-border p-3">
          <Link href={`/strategies/setups/${setup.setup_id}`} className="font-medium underline">{setup.symbol || setup.instrument} · {sfp ? setup.condition : setup.stage} · {setup.state}</Link>
          <p className="text-sm">{setup.direction} · {setup.timeframe} · evidence {setup.freshness} · risk {setup.risk_state} · {new Date(setup.observed_at).toLocaleString()}</p>
          <p className="text-sm">{setup.reason_codes.join(", ")}</p>
        </li>; })}</ul>
        <p className="text-xs text-text-muted">Structure does not establish profitability. Stored observations require fresh final evidence for live confirmation.</p>
      </>}
    </CardContent>
  </Card>;
}
