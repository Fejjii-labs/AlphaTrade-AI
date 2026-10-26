"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { BacktestEquityChart } from "@/components/strategy/BacktestEquityChart";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api, ApiError, PROMOTE_RESEARCH_VALIDATION_CANDIDATE } from "@/lib/api";
import type {
  BacktestJournalResult,
  BacktestRunStatus,
  BacktestVerifyResult,
  JournalComparisonCohort,
  JournalComparisonResponse,
  JournalTradeStatsMetrics,
  ResearchValidationStatusResponse,
  SetupEvidenceItem,
  SetupEvidenceResponse,
  SetupEvidenceTier,
} from "@/lib/api/types";
import { formatDate, formatDecimal } from "@/lib/utils";

const POLL_MS = 4000;
const TRADES_PAGE_SIZE = 25;

const ACTIVE_STATUSES: BacktestRunStatus[] = ["queued", "running", "cancel_requested"];

function statusBadgeVariant(
  status: BacktestRunStatus,
): "default" | "success" | "warning" | "danger" | "info" | "muted" {
  switch (status) {
    case "completed":
      return "success";
    case "failed":
      return "danger";
    case "cancel_requested":
      return "warning";
    case "cancelled":
      return "muted";
    case "queued":
    case "running":
      return "info";
    default:
      return "default";
  }
}

function formatStatus(status: BacktestRunStatus): string {
  return status.replace(/_/g, " ");
}

function tierLabel(tier: SetupEvidenceTier): string {
  switch (tier) {
    case "tier1":
      return "Tier 1";
    case "tier2":
      return "Tier 2";
    case "tier3":
      return "Tier 3";
    default:
      return tier;
  }
}

function tierBadgeVariant(tier: SetupEvidenceTier): "success" | "warning" | "muted" {
  switch (tier) {
    case "tier1":
      return "success";
    case "tier2":
      return "warning";
    default:
      return "muted";
  }
}

function cohortLabel(cohort: JournalComparisonCohort): string {
  switch (cohort) {
    case "human":
      return "Human";
    case "paper_system":
      return "Paper system";
    case "backtest":
      return "Backtest";
    default:
      return cohort;
  }
}

function MetricCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-zinc-800 p-3">
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="text-lg font-medium text-zinc-100">{value}</p>
    </div>
  );
}

function CohortMetrics({ metrics }: { metrics: JournalTradeStatsMetrics }) {
  return (
    <ul className="space-y-1 text-xs text-zinc-300">
      <li>Trades: {metrics.trade_count}</li>
      <li>
        Win rate:{" "}
        {metrics.win_rate !== null ? `${(metrics.win_rate * 100).toFixed(1)}%` : "—"}
      </li>
      <li>
        Profit factor:{" "}
        {metrics.profit_factor !== null ? metrics.profit_factor.toFixed(2) : "—"}
      </li>
      <li>Expectancy: {metrics.expectancy ?? "—"}</li>
      <li>Net PnL: {metrics.net_pnl_total ?? "—"}</li>
      <li>Confidence: {metrics.confidence}</li>
      {metrics.warnings.length ? (
        <li className="text-amber-400">
          {metrics.warnings.map((w) => w.message).join(" ")}
        </li>
      ) : null}
    </ul>
  );
}

function SetupEvidenceCard({ item }: { item: SetupEvidenceItem }) {
  const { measured, thresholds } = item;
  return (
    <div className="space-y-2 rounded-lg border border-zinc-800 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={tierBadgeVariant(item.tier)}>{tierLabel(item.tier)}</Badge>
        <span className="text-zinc-300">
          {item.strategy_name} v{item.version}
        </span>
      </div>
      <ul className="space-y-1 text-xs text-zinc-400">
        <li>OOS trades: {measured.oos_trade_count}</li>
        <li>
          OOS profit factor:{" "}
          {measured.oos_profit_factor !== null && measured.oos_profit_factor !== undefined
            ? measured.oos_profit_factor.toFixed(2)
            : "—"}
        </li>
        <li>Confirm trades: {measured.confirm_trade_count}</li>
        <li>Total backtest trades: {measured.total_backtest_trades}</li>
      </ul>
      <p className="text-xs text-zinc-500">
        Thresholds — Tier 1: OOS trades ≥ {thresholds.tier1_oos_min_trades}, PF ≥{" "}
        {thresholds.tier1_oos_min_profit_factor}, confirm ≥ {thresholds.tier1_min_confirm_trades}
        ; Tier 2: trades ≥ {thresholds.tier2_min_trades}, OOS ≥ {thresholds.tier2_oos_min_trades},
        PF ≥ {thresholds.tier2_oos_min_profit_factor}
      </p>
      <p className="text-xs text-amber-400/80">{item.note}</p>
    </div>
  );
}

export default function BacktestRunDetailPage() {
  const params = useParams();
  const runId = String(params.id);

  const loader = useCallback(() => api.backtests.get(runId), [runId]);
  const { data: run, loading, error, reload } = useAsyncData(loader, [runId]);

  const [tradesOffset, setTradesOffset] = useState(0);
  const [tradesTotal, setTradesTotal] = useState(0);
  const [trades, setTrades] = useState<Awaited<ReturnType<typeof api.backtests.listTrades>>["items"]>(
    [],
  );
  const [tradesLoading, setTradesLoading] = useState(false);
  const [tradesError, setTradesError] = useState<string | null>(null);

  const [verifyResult, setVerifyResult] = useState<BacktestVerifyResult | null>(null);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  const [journalDryRun, setJournalDryRun] = useState<BacktestJournalResult | null>(null);
  const [journalCommit, setJournalCommit] = useState<BacktestJournalResult | null>(null);
  const [journalBusy, setJournalBusy] = useState(false);
  const [journalError, setJournalError] = useState<string | null>(null);
  const [journalConfirmOpen, setJournalConfirmOpen] = useState(false);

  const [cancelBusy, setCancelBusy] = useState(false);

  const [comparison, setComparison] = useState<JournalComparisonResponse | null>(null);
  const [evidence, setEvidence] = useState<SetupEvidenceResponse | null>(null);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [comparisonError, setComparisonError] = useState<string | null>(null);

  const [rvStatus, setRvStatus] = useState<ResearchValidationStatusResponse | null>(null);
  const [rvLoading, setRvLoading] = useState(false);
  const [rvError, setRvError] = useState<string | null>(null);
  const [rvConfirm, setRvConfirm] = useState("");
  const [rvPromoting, setRvPromoting] = useState(false);
  const [rvPromoteError, setRvPromoteError] = useState<string | null>(null);

  useEffect(() => {
    if (!run || !ACTIVE_STATUSES.includes(run.status)) return;
    const timer = window.setInterval(() => {
      void reload();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [run?.status, reload, run]);

  const loadTrades = useCallback(
    async (offset: number) => {
      setTradesLoading(true);
      setTradesError(null);
      try {
        const page = await api.backtests.listTrades(runId, {
          limit: TRADES_PAGE_SIZE,
          offset,
        });
        setTrades(page.items);
        setTradesTotal(page.total);
        setTradesOffset(offset);
      } catch (err) {
        setTradesError(err instanceof Error ? err.message : "Failed to load trades");
      } finally {
        setTradesLoading(false);
      }
    },
    [runId],
  );

  useEffect(() => {
    if (run?.status === "completed" || run?.status === "cancelled") {
      void loadTrades(0);
    }
  }, [run?.status, loadTrades]);

  async function handleCancel() {
    setCancelBusy(true);
    try {
      await api.backtests.cancel(runId);
      await reload();
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : "Cancel failed");
    } finally {
      setCancelBusy(false);
    }
  }

  async function handleVerify() {
    setVerifyBusy(true);
    setVerifyError(null);
    try {
      const result = await api.backtests.verify(runId);
      setVerifyResult(result);
    } catch (err) {
      setVerifyError(err instanceof Error ? err.message : "Verify failed");
    } finally {
      setVerifyBusy(false);
    }
  }

  async function handleJournalDryRun() {
    setJournalBusy(true);
    setJournalError(null);
    setJournalConfirmOpen(false);
    try {
      const result = await api.backtests.journalTrades(runId, { dry_run: true });
      setJournalDryRun(result);
    } catch (err) {
      setJournalError(err instanceof Error ? err.message : "Journal dry-run failed");
    } finally {
      setJournalBusy(false);
    }
  }

  async function handleJournalCommit() {
    setJournalBusy(true);
    setJournalError(null);
    try {
      const result = await api.backtests.journalTrades(runId, { dry_run: false });
      setJournalCommit(result);
      setJournalConfirmOpen(false);
    } catch (err) {
      setJournalError(err instanceof Error ? err.message : "Journal commit failed");
    } finally {
      setJournalBusy(false);
    }
  }

  useEffect(() => {
    if (run?.status === "completed") {
      setRvLoading(true);
      setRvError(null);
      void api.researchValidation
        .backtestStatus(runId)
        .then(setRvStatus)
        .catch((err) => {
          if (err instanceof ApiError && err.status === 403) {
            setRvError("You do not have permission to view research validation status.");
          } else {
            setRvError(err instanceof Error ? err.message : "Failed to load research validation.");
          }
        })
        .finally(() => setRvLoading(false));
    }
  }, [run?.status, runId]);

  async function handleResearchPromote() {
    setRvPromoting(true);
    setRvPromoteError(null);
    try {
      await api.researchValidation.promote({
        confirm: rvConfirm,
        backtest_run_id: runId,
      });
      setRvConfirm("");
      const refreshed = await api.researchValidation.backtestStatus(runId);
      setRvStatus(refreshed);
    } catch (err) {
      setRvPromoteError(err instanceof Error ? err.message : "Promotion failed.");
    } finally {
      setRvPromoting(false);
    }
  }

  async function loadComparison() {
    if (!run) return;
    setComparisonLoading(true);
    setComparisonError(null);
    try {
      const filters = {
        strategy_id: run.strategy_id,
        strategy_version_id: run.strategy_version_id ?? undefined,
      };
      const [comparisonResult, evidenceResult] = await Promise.all([
        api.journal.comparison(filters),
        api.journal.setupEvidence(filters),
      ]);
      setComparison(comparisonResult);
      setEvidence(evidenceResult);
    } catch (err) {
      setComparisonError(err instanceof Error ? err.message : "Comparison load failed");
    } finally {
      setComparisonLoading(false);
    }
  }

  function toggleComparison() {
    const next = !comparisonOpen;
    setComparisonOpen(next);
    if (next && !comparison && run) {
      void loadComparison();
    }
  }

  async function copyHash(value: string) {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      /* clipboard may be unavailable in tests */
    }
  }

  if (loading && !run) {
    return <LoadingState label="Loading backtest run…" />;
  }

  if (error || !run) {
    return <ErrorState message={error ?? "Backtest run not found"} />;
  }

  const metrics = run.result?.metrics;
  const research = run.result?.replay?.mode === "sfp_research" ? run.result.replay : null;
  const splitMetrics = run.result?.split_metrics ?? [];
  const oosMetrics = run.result?.oos_metrics;
  const datasetSummary = run.result?.dataset_summary;
  const progressPct =
    run.total_bars && run.processed_bars !== null && run.processed_bars !== undefined
      ? Math.min(100, Math.round((run.processed_bars / run.total_bars) * 100))
      : null;
  const canCancel = run.status === "queued" || run.status === "running";
  const showResults =
    run.status === "completed" || (run.status === "cancelled" && (metrics || research));

  const journalWouldCreate =
    journalDryRun?.results.filter((r) => r.outcome === "would_create").length ?? 0;

  return (
    <div className="space-y-6 p-4 md:p-6" data-testid="backtest-run-detail">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm text-zinc-500">
            <Link href={`/strategy-lab/${run.strategy_id}`} className="text-sky-400 hover:underline">
              Strategy lab
            </Link>
          </p>
          <h1 className="text-2xl font-semibold text-zinc-100">Backtest run</h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={statusBadgeVariant(run.status)} data-testid="backtest-status-badge">
            {formatStatus(run.status)}
          </Badge>
          {canCancel ? (
            <Button variant="outline" size="sm" disabled={cancelBusy} onClick={() => void handleCancel()}>
              {cancelBusy ? "Cancelling…" : "Cancel run"}
            </Button>
          ) : null}
        </div>
      </div>

      <p className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-sm text-amber-200">
        Historical simulation only — not a guarantee of future performance. Real trading remains
        disabled.
      </p>

      {ACTIVE_STATUSES.includes(run.status) && progressPct !== null ? (
        <Card>
          <CardContent className="space-y-2 pt-6">
            <div className="flex justify-between text-sm text-zinc-400">
              <span>Progress</span>
              <span>
                {run.processed_bars ?? 0} / {run.total_bars ?? 0} bars ({progressPct}%)
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded bg-zinc-800">
              <div
                className="h-full bg-sky-500 transition-all"
                style={{ width: `${progressPct}%` }}
                data-testid="backtest-progress-bar"
              />
            </div>
          </CardContent>
        </Card>
      ) : null}

      {run.status === "failed" ? (
        <Card>
          <CardContent className="pt-6 text-sm text-red-400">
            {run.error_message ?? "Backtest failed without a detailed message."}
          </CardContent>
        </Card>
      ) : null}

      {run.status === "cancelled" && !showResults ? (
        <Card>
          <CardContent className="pt-6 text-sm text-zinc-400">
            This run was cancelled before producing results.
          </CardContent>
        </Card>
      ) : null}

      {showResults && research ? (
        <Card data-testid="sfp-replay-research">
          <CardHeader>
            <CardTitle>SFP research replay</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <p>
              No SFP execution plan is authorized. This replay reports setups and evidence.
              Trade returns are unavailable.
            </p>
            {research.samples.map((sample) => (
              <div key={sample.split_label} className="space-y-1">
                <p>{sample.split_label}: {sample.status.replaceAll("_", " ")}</p>
                <p>
                  {sample.candle_count} candles · {sample.setup_count ?? 0} setups · {" "}
                  {sample.candidate_count} confirmed setups
                </p>
                <p className="text-zinc-400">
                  {Object.entries(sample.lifecycle_counts ?? {})
                    .map(([key, count]) => `${key.replaceAll("_", " ")}: ${count}`)
                    .join(" · ")}
                </p>
              </div>
            ))}
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <caption className="pb-2 text-left">
                  Setup sample counts. Quality buckets count measured components;
                  regime bands describe directional efficiency.
                </caption>
                <thead>
                  <tr>
                    {["Window", "Symbol", "Timeframe", "Direction", "Level", "Quality",
                      "Regime", "Setups", "Confirmed"].map((label) => (
                      <th key={label} className="p-2">{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {research.research_buckets?.map((bucket, index) => (
                    <tr key={index}>
                      {[bucket.split_label, bucket.symbol, bucket.timeframe, bucket.direction,
                        bucket.level_type, bucket.quality_bucket, bucket.regime, bucket.setup_count,
                        bucket.confirmed_count].map((value, column) => (
                        <td key={column} className="p-2">{value}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>Missing evidence: {research.missing_evidence.join(", ") || "none recorded"}</p>
            <p>Stale evidence: {research.stale_evidence?.join(", ") || "none recorded"}</p>
            <p>Results are descriptive. No improvement is claimed.</p>
            <ul className="list-disc pl-5 text-zinc-400">
              {run.result?.limitations?.map((limitation) => <li key={limitation}>{limitation}</li>)}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      {showResults && metrics ? (
        <>
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="backtest-metrics">
            <MetricCard label="Trades" value={metrics.trade_count} />
            <MetricCard label="Win rate" value={`${(metrics.win_rate * 100).toFixed(1)}%`} />
            <MetricCard label="Profit factor" value={metrics.profit_factor.toFixed(2)} />
            <MetricCard label="Expectancy" value={formatDecimal(metrics.expectancy)} />
            <MetricCard label="Net PnL" value={formatDecimal(metrics.net_pnl)} />
            <MetricCard label="Max drawdown" value={`${metrics.max_drawdown_pct.toFixed(1)}%`} />
            <MetricCard label="Total fees" value={formatDecimal(metrics.total_fees)} />
            <MetricCard label="Total slippage" value={formatDecimal(metrics.total_slippage)} />
            <MetricCard
              label="Total funding"
              value={formatDecimal(metrics.total_funding ?? "0")}
            />
            <MetricCard label="Return" value={`${metrics.return_pct.toFixed(2)}%`} />
          </section>

          {splitMetrics.length ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Split breakdown</CardTitle>
              </CardHeader>
              <CardContent className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="text-zinc-400">
                      <th className="p-2">Split</th>
                      <th className="p-2">Period</th>
                      <th className="p-2">Trades</th>
                      <th className="p-2">Win rate</th>
                      <th className="p-2">PF</th>
                      <th className="p-2">Expectancy</th>
                      <th className="p-2">Net PnL</th>
                      <th className="p-2">Max DD</th>
                    </tr>
                  </thead>
                  <tbody>
                    {splitMetrics.map((split) => (
                      <tr key={`${split.split_label}-${split.split_index}`} className="border-t border-zinc-800">
                        <td className="p-2">
                          {split.split_label === "in_sample" ? "In-sample" : "Out-of-sample"} #
                          {split.split_index}
                        </td>
                        <td className="p-2">
                          {formatDate(split.start_time)} – {formatDate(split.end_time)}
                        </td>
                        <td className="p-2">{split.trade_count}</td>
                        <td className="p-2">{(split.win_rate * 100).toFixed(1)}%</td>
                        <td className="p-2">{split.profit_factor.toFixed(2)}</td>
                        <td className="p-2">{formatDecimal(split.expectancy)}</td>
                        <td className="p-2">{formatDecimal(split.net_pnl)}</td>
                        <td className="p-2">{split.max_drawdown_pct.toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          ) : null}

          {oosMetrics ? (
            <Card data-testid="backtest-oos-summary">
              <CardHeader>
                <CardTitle className="text-base">Out-of-sample summary</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
                <MetricCard label="OOS trades" value={oosMetrics.trade_count} />
                <MetricCard label="OOS win rate" value={`${(oosMetrics.win_rate * 100).toFixed(1)}%`} />
                <MetricCard label="OOS profit factor" value={oosMetrics.profit_factor.toFixed(2)} />
                <MetricCard label="OOS net PnL" value={formatDecimal(oosMetrics.net_pnl)} />
              </CardContent>
            </Card>
          ) : null}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Equity curve</CardTitle>
            </CardHeader>
            <CardContent>
              <BacktestEquityChart curve={metrics.equity_curve ?? []} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-2">
              <CardTitle className="text-base">Simulated trades</CardTitle>
              <p className="text-xs text-zinc-500">
                {tradesTotal} total · page {Math.floor(tradesOffset / TRADES_PAGE_SIZE) + 1}
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              {tradesError ? <p className="text-red-400">{tradesError}</p> : null}
              {tradesLoading ? <p className="text-zinc-500">Loading trades…</p> : null}
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs" data-testid="backtest-trades-table">
                  <thead>
                    <tr className="text-zinc-400">
                      <th className="p-1">Dir</th>
                      <th className="p-1">Entry</th>
                      <th className="p-1">Exit</th>
                      <th className="p-1">Size</th>
                      <th className="p-1">Fees</th>
                      <th className="p-1">Funding</th>
                      <th className="p-1">Net PnL</th>
                      <th className="p-1">MFE</th>
                      <th className="p-1">MAE</th>
                      <th className="p-1">Capture</th>
                      <th className="p-1">Split</th>
                      <th className="p-1">Exit reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((trade) => (
                      <tr key={`${trade.entry_time}-${trade.exit_time}`} className="border-t border-zinc-800">
                        <td className="p-1">{trade.direction}</td>
                        <td className="p-1">
                          {formatDate(trade.entry_time)}
                          <br />
                          {formatDecimal(trade.entry_price)}
                        </td>
                        <td className="p-1">
                          {formatDate(trade.exit_time)}
                          <br />
                          {formatDecimal(trade.exit_price)}
                        </td>
                        <td className="p-1">{formatDecimal(trade.size)}</td>
                        <td className="p-1">{formatDecimal(trade.fees)}</td>
                        <td className="p-1">{formatDecimal(trade.funding_cost ?? "0")}</td>
                        <td className="p-1">{formatDecimal(trade.net_pnl)}</td>
                        <td className="p-1">{formatDecimal(trade.mfe_amount)}</td>
                        <td className="p-1">{formatDecimal(trade.mae_amount)}</td>
                        <td className="p-1">
                          {trade.capture_pct !== null && trade.capture_pct !== undefined
                            ? `${Number(trade.capture_pct).toFixed(1)}%`
                            : "—"}
                        </td>
                        <td className="p-1">{trade.split_label ?? "—"}</td>
                        <td className="p-1">{trade.exit_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={tradesOffset <= 0 || tradesLoading}
                  onClick={() => void loadTrades(Math.max(0, tradesOffset - TRADES_PAGE_SIZE))}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={tradesOffset + TRADES_PAGE_SIZE >= tradesTotal || tradesLoading}
                  onClick={() => void loadTrades(tradesOffset + TRADES_PAGE_SIZE)}
                >
                  Next
                </Button>
              </div>
            </CardContent>
          </Card>

          <div className="grid gap-4 md:grid-cols-2">
            <Card data-testid="backtest-verify-panel">
              <CardHeader>
                <CardTitle className="text-base">Verify determinism</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <Button size="sm" disabled={verifyBusy} onClick={() => void handleVerify()}>
                  {verifyBusy ? "Verifying…" : "Re-run verify"}
                </Button>
                {verifyError ? <p className="text-red-400">{verifyError}</p> : null}
                {verifyResult ? (
                  <div className="space-y-2 text-xs">
                    <div className="flex flex-wrap gap-2">
                      <Badge variant={verifyResult.match ? "success" : "danger"}>
                        Match: {verifyResult.match ? "yes" : "no"}
                      </Badge>
                      <Badge variant={verifyResult.dataset_ok ? "success" : "warning"}>
                        Dataset OK: {verifyResult.dataset_ok ? "yes" : "no"}
                      </Badge>
                    </div>
                    {verifyResult.detail ? <p className="text-zinc-500">{verifyResult.detail}</p> : null}
                  </div>
                ) : null}
              </CardContent>
            </Card>

            <Card data-testid="backtest-journal-panel">
              <CardHeader>
                <CardTitle className="text-base">Journal trades</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" disabled={journalBusy} onClick={() => void handleJournalDryRun()}>
                    {journalBusy ? "Working…" : "Dry-run import"}
                  </Button>
                  {journalDryRun ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={journalBusy}
                      onClick={() => setJournalConfirmOpen(true)}
                    >
                      Confirm commit
                    </Button>
                  ) : null}
                </div>
                {journalError ? <p className="text-red-400">{journalError}</p> : null}
                {journalDryRun ? (
                  <div className="text-xs text-zinc-400" data-testid="journal-dry-run-summary">
                    <p>Dry-run totals: {journalDryRun.total_rows} rows</p>
                    <p>Would create: {journalWouldCreate}</p>
                    <p>Duplicates: {journalDryRun.duplicate_count}</p>
                    <p>Invalid: {journalDryRun.invalid_count}</p>
                  </div>
                ) : null}
                {journalConfirmOpen ? (
                  <div className="rounded border border-amber-500/30 bg-amber-500/5 p-3 text-xs">
                    <p className="mb-2 text-amber-200">
                      Commit will create journal trades from this backtest. Continue?
                    </p>
                    <div className="flex gap-2">
                      <Button size="sm" disabled={journalBusy} onClick={() => void handleJournalCommit()}>
                        Commit import
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setJournalConfirmOpen(false)}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : null}
                {journalCommit ? (
                  <div className="text-xs text-emerald-300" data-testid="journal-commit-summary">
                    <p>Committed: {journalCommit.created_count} created</p>
                    <p>Duplicates: {journalCommit.duplicate_count}</p>
                    <p>Invalid: {journalCommit.invalid_count}</p>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </div>

          <Card data-testid="backtest-provenance-panel">
            <CardHeader>
              <CardTitle className="text-base">Provenance</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-xs text-zinc-400">
              {datasetSummary ? (
                <div className="space-y-1">
                  <p>
                    Dataset fingerprint
                    <Button
                      variant="outline"
                      size="sm"
                      className="ml-2 h-6 px-2"
                      onClick={() => void copyHash(datasetSummary.dataset_hash)}
                    >
                      Copy
                    </Button>
                  </p>
                  <p>
                    Candles: {datasetSummary.candle_count} · Gaps: {datasetSummary.gap_count} ·
                    Stale: {datasetSummary.stale_count}
                  </p>
                  {Object.keys(datasetSummary.source_counts).length ? (
                    <p>Sources: {JSON.stringify(datasetSummary.source_counts)}</p>
                  ) : null}
                </div>
              ) : null}
              <p>Engine version: {run.engine_version ?? run.result?.engine_version ?? "—"}</p>
              <p>Data quality: {run.result?.data_quality ?? "—"}</p>
              {run.result?.limitations?.length ? (
                <ul className="list-disc pl-4 text-amber-400">
                  {run.result.limitations.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
            </CardContent>
          </Card>
        </>
      ) : null}

      <Card data-testid="backtest-research-validation-panel">
        <CardHeader className="flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-base">Research validation</CardTitle>
          <Link href="/research-validation" className="text-xs text-sky-400 underline">
            Open dashboard
          </Link>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {run.status !== "completed" ? (
            <p className="text-xs text-zinc-500">
              Research validation status is available after the backtest completes.
            </p>
          ) : null}
          {run.status === "completed" && rvLoading ? (
            <p className="text-xs text-zinc-500">Loading research validation…</p>
          ) : null}
          {rvError ? <p className="text-xs text-amber-300">{rvError}</p> : null}
          {rvStatus ? (
            <div className="space-y-3" data-testid="backtest-research-validation-status">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={tierBadgeVariant(rvStatus.evidence.evidence_tier)}>
                  {tierLabel(rvStatus.evidence.evidence_tier)}
                </Badge>
                <Badge variant={rvStatus.evidence.eligible_for_promotion ? "success" : "muted"}>
                  {rvStatus.evidence.eligible_for_promotion ? "Eligible" : "Not eligible"}
                </Badge>
              </div>
              {rvStatus.evidence.warnings.length || rvStatus.evidence.promotion_blocked_reason ? (
                <ul className="space-y-1 text-xs text-amber-300">
                  {rvStatus.evidence.warnings.map((warning) => (
                    <li key={warning}>{warning.replaceAll("_", " ")}</li>
                  ))}
                  {rvStatus.evidence.promotion_blocked_reason ? (
                    <li>{rvStatus.evidence.promotion_blocked_reason}</li>
                  ) : null}
                </ul>
              ) : null}
              {rvStatus.evidence.existing_candidate_id ? (
                <Link
                  href={`/paper-validation/candidates/${rvStatus.evidence.existing_candidate_id}`}
                  className="text-xs text-sky-400 underline"
                >
                  View paper validation candidate
                </Link>
              ) : rvStatus.evidence.eligible_for_promotion &&
                !rvStatus.evidence.promotion_blocked_reason ? (
                <div className="space-y-2 rounded border border-zinc-700 p-3 text-xs">
                  <p className="text-zinc-400">
                    Type{" "}
                    <span className="font-mono text-zinc-100">
                      {PROMOTE_RESEARCH_VALIDATION_CANDIDATE}
                    </span>{" "}
                    to promote into the paper validation queue.
                  </p>
                  <Input
                    value={rvConfirm}
                    onChange={(event) => setRvConfirm(event.target.value)}
                    data-testid="backtest-research-validation-confirm"
                    className="max-w-md font-mono text-xs"
                  />
                  <Button
                    size="sm"
                    disabled={
                      rvPromoting || rvConfirm !== PROMOTE_RESEARCH_VALIDATION_CANDIDATE
                    }
                    onClick={() => void handleResearchPromote()}
                    data-testid="backtest-research-validation-promote"
                  >
                    {rvPromoting ? "Promoting…" : "Promote to paper validation"}
                  </Button>
                  {rvPromoteError ? <p className="text-red-400">{rvPromoteError}</p> : null}
                </div>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-base">Journal comparison &amp; setup evidence</CardTitle>
          <Button variant="outline" size="sm" onClick={() => toggleComparison()}>
            {comparisonOpen ? "Hide" : "Show"}
          </Button>
        </CardHeader>
        {comparisonOpen ? (
          <CardContent className="space-y-4">
            {comparisonLoading ? <p className="text-sm text-zinc-500">Loading comparison…</p> : null}
            {comparisonError ? <p className="text-sm text-red-400">{comparisonError}</p> : null}
            {comparison ? (
              <div className="grid gap-4 md:grid-cols-3" data-testid="journal-comparison-cohorts">
                {(["human", "paper_system", "backtest"] as JournalComparisonCohort[]).map((key) => {
                  const cohort = comparison.cohorts.find((c) => c.cohort === key);
                  return (
                    <div key={key} className="rounded-lg border border-zinc-800 p-3">
                      <p className="mb-2 font-medium text-zinc-200">{cohortLabel(key)}</p>
                      {cohort ? (
                        <>
                          <p className="mb-2 text-xs text-zinc-500">
                            n={cohort.sample_count}
                            {cohort.truncated ? " (truncated)" : ""}
                          </p>
                          <CohortMetrics metrics={cohort.metrics} />
                        </>
                      ) : (
                        <p className="text-xs text-zinc-500">No cohort data</p>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : null}
            {comparison?.note ? (
              <p className="text-xs text-amber-400/80">{comparison.note}</p>
            ) : null}
            {evidence?.items.length ? (
              <div className="space-y-3" data-testid="setup-evidence-list">
                {evidence.items.map((item) => (
                  <SetupEvidenceCard key={item.strategy_version_id} item={item} />
                ))}
              </div>
            ) : evidence ? (
              <p className="text-xs text-zinc-500">No setup evidence items for this strategy.</p>
            ) : null}
            {evidence?.note ? <p className="text-xs text-amber-400/80">{evidence.note}</p> : null}
          </CardContent>
        ) : null}
      </Card>
    </div>
  );
}
