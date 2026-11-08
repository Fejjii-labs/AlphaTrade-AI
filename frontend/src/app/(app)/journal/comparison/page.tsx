"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { useAsyncData } from "@/hooks/useAsyncData";
import { useResolvedTimezone } from "@/hooks/useResolvedTimezone";
import { api } from "@/lib/api";
import type {
  ComparisonBreakdown,
  ComparisonDimensionBucket,
  ComparisonScorecard,
  DecisionQualityMetrics,
  ExecutionActor,
  JournalComparisonCohort,
  JournalComparisonCohortResult,
  JournalComparisonParams,
  JournalEntryMethod,
  JournalTradeSource,
  JournalTradeStatsMetrics,
  MarketRegime,
  SampleConfidence,
} from "@/lib/api/types";
import { formatMonetary, formatPercent, humanizeToken } from "@/lib/format";
import { TIMEZONE_SOURCE_LABELS, zonedDayBoundsUtc } from "@/lib/timezone";

const COHORT_ORDER: JournalComparisonCohort[] = ["human", "paper_system", "backtest"];

const COHORT_LABELS: Record<JournalComparisonCohort, string> = {
  human: "Human",
  paper_system: "Paper system",
  backtest: "Backtest",
};

const ACTOR_LABELS: Record<ExecutionActor, string> = {
  human: "Human",
  system: "System",
};

const SOURCE_OPTIONS: JournalTradeSource[] = [
  "manual",
  "paper_execution",
  "paper_validation",
  "backtest",
  "imported",
  "system",
];

const ENTRY_METHOD_OPTIONS: JournalEntryMethod[] = ["manual", "auto", "import", "backfill"];

const REGIME_OPTIONS: MarketRegime[] = [
  "trending_up",
  "trending_down",
  "ranging",
  "volatile",
  "quiet",
  "unknown",
];

const BREAKDOWN_DIMENSION_LABELS: Record<ComparisonBreakdown["dimension"], string> = {
  setup: "Setup",
  market_regime: "Market regime",
};

const CONFIDENCE_TONE: Record<SampleConfidence, "ok" | "warn" | "critical"> = {
  high: "ok",
  moderate: "ok",
  low: "warn",
  insufficient: "critical",
};

function pct(value: number | null, asRate = false): string {
  if (value === null) return "—";
  return formatPercent(value, 1, { alreadyPercent: !asRate });
}

function num(value: number | null, digits = 2): string {
  return value === null ? "—" : value.toFixed(digits);
}

function ConfidenceBadge({ confidence }: { confidence: SampleConfidence }) {
  return <StatusBadge label={humanizeToken(confidence)} tone={CONFIDENCE_TONE[confidence]} />;
}

function WarningsList({ warnings }: { warnings: { code: string; message: string }[] }) {
  if (!warnings.length) return null;
  return (
    <ul className="list-disc pl-5 text-xs text-amber-300">
      {warnings.map((w) => (
        <li key={w.code}>{w.message}</li>
      ))}
    </ul>
  );
}

function MetricsSummary({ metrics }: { metrics: JournalTradeStatsMetrics }) {
  return (
    <div className="grid gap-2 text-sm text-zinc-300 md:grid-cols-2 xl:grid-cols-3">
      <p>
        Trades: {metrics.trade_count} (W {metrics.wins} / L {metrics.losses} / BE{" "}
        {metrics.breakeven})
      </p>
      <p>Win rate: {pct(metrics.win_rate, true)}</p>
      <p>
        Net PnL: {formatMonetary(metrics.net_pnl_total)} ({metrics.pnl_sample_count} with PnL)
      </p>
      <p>Expectancy: {formatMonetary(metrics.expectancy)}</p>
      <p>
        Avg R: {num(metrics.average_r)} ({metrics.r_sample_count} trades)
      </p>
      <p>Profit factor: {num(metrics.profit_factor)}</p>
      <p>
        Capture:{" "}
        {metrics.average_realized_vs_available_pct === null
          ? "—"
          : `${metrics.average_realized_vs_available_pct.toFixed(1)}%`}{" "}
        ({metrics.capture_sample_count} trades)
      </p>
    </div>
  );
}

function DecisionQualitySummary({ dq }: { dq: DecisionQualityMetrics }) {
  return (
    <div className="grid gap-2 text-sm text-zinc-300 md:grid-cols-2">
      <p>
        Entry timing: {pct(dq.average_entry_timing_pct)} ({dq.timing_sample_count} trades)
      </p>
      <p>
        Early exits:{" "}
        {dq.early_exit_count === null ? "—" : dq.early_exit_count} /{" "}
        {dq.early_exit_sample_count} ({pct(dq.early_exit_rate, true)})
      </p>
      <p>
        Missed profit (avg): {formatMonetary(dq.average_missed_profit)} (
        {dq.missed_profit_sample_count} trades)
      </p>
      <p>Avg capture: {pct(dq.average_capture_pct)}</p>
    </div>
  );
}

function CohortCard({ cohort }: { cohort: JournalComparisonCohortResult }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">{COHORT_LABELS[cohort.cohort]}</CardTitle>
        <ConfidenceBadge confidence={cohort.metrics.confidence} />
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-zinc-500">
          {cohort.sample_count} closed trades
          {cohort.truncated ? " (partial — scan capped)" : ""}
        </p>
        <MetricsSummary metrics={cohort.metrics} />
        <WarningsList warnings={cohort.metrics.warnings} />
      </CardContent>
    </Card>
  );
}

function ScorecardCard({ scorecard }: { scorecard: ComparisonScorecard }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">{ACTOR_LABELS[scorecard.actor]}</CardTitle>
        <ConfidenceBadge confidence={scorecard.metrics.confidence} />
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-zinc-500">
          {scorecard.sample_count} closed trades
          {scorecard.truncated ? " (partial — scan capped)" : ""}
        </p>
        <div>
          <h3 className="mb-2 text-sm font-medium text-zinc-200">Performance</h3>
          <MetricsSummary metrics={scorecard.metrics} />
          <WarningsList warnings={scorecard.metrics.warnings} />
        </div>
        <div>
          <h3 className="mb-2 text-sm font-medium text-zinc-200">Decision quality</h3>
          <DecisionQualitySummary dq={scorecard.decision_quality} />
          <WarningsList warnings={scorecard.decision_quality.warnings} />
        </div>
      </CardContent>
    </Card>
  );
}

function DimensionBucketsSection({
  title,
  buckets,
}: {
  title: string;
  buckets: ComparisonDimensionBucket[];
}) {
  if (!buckets.length) return null;
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-medium">{title}</h2>
      <div className="grid gap-4 lg:grid-cols-2">
        {buckets.map((bucket) => (
          <Card key={bucket.key}>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-base">{bucket.label}</CardTitle>
              <ConfidenceBadge confidence={bucket.metrics.confidence} />
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-zinc-500">{bucket.sample_count} closed trades</p>
              <MetricsSummary metrics={bucket.metrics} />
              <WarningsList warnings={bucket.metrics.warnings} />
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  );
}

function BreakdownSection({ breakdown }: { breakdown: ComparisonBreakdown }) {
  if (!breakdown.buckets.length) return null;
  return (
    <DimensionBucketsSection
      title={`${BREAKDOWN_DIMENSION_LABELS[breakdown.dimension]} breakdown`}
      buckets={breakdown.buckets}
    />
  );
}

function RelatedLinks({
  links,
}: {
  links: {
    journal_trades_path: string;
    journal_statistics_path: string;
    backtests_path: string;
    research_validation_path: string;
    paper_validation_candidates_path: string;
  };
}) {
  const entries = [
    { href: links.journal_trades_path, label: "Journal trades" },
    { href: links.journal_statistics_path, label: "Journal statistics" },
    { href: links.backtests_path, label: "Backtests" },
    { href: links.research_validation_path, label: "Research validation" },
    { href: links.paper_validation_candidates_path, label: "Paper validation queue" },
  ];

  return (
    <div className="flex flex-wrap gap-3 text-sm">
      {entries.map((entry) => (
        <Link key={entry.label} href={entry.href} className="text-sky-400 underline">
          {entry.label}
        </Link>
      ))}
    </div>
  );
}

function parseDateParam(value: string | null): string {
  if (!value) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const parsed = value.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(parsed) ? parsed : "";
}

function hasAnyTrades(data: {
  cohorts: JournalComparisonCohortResult[];
  scorecards: ComparisonScorecard[];
}): boolean {
  return (
    data.cohorts.some((c) => c.sample_count > 0) ||
    data.scorecards.some((s) => s.sample_count > 0)
  );
}

export default function JournalComparisonPage() {
  const searchParams = useSearchParams();

  const initialStrategyId = searchParams.get("strategy_id") ?? "";
  const initialStrategyVersionId = searchParams.get("strategy_version_id") ?? "";
  const initialSetupId = searchParams.get("setup_id") ?? "";
  const initialSymbol = searchParams.get("symbol") ?? "";
  const initialTimeframe = searchParams.get("timeframe") ?? "";
  const initialRegime = (searchParams.get("market_regime") as MarketRegime | null) ?? "";
  const initialEntryMethod =
    (searchParams.get("entry_method") as JournalEntryMethod | null) ?? "";
  const initialSource = (searchParams.get("source") as JournalTradeSource | null) ?? "";
  const initialDateFrom = parseDateParam(searchParams.get("date_from"));
  const initialDateTo = parseDateParam(searchParams.get("date_to"));
  const initialBreakdownLimit = Number(searchParams.get("breakdown_limit") ?? "10");

  const [strategyId, setStrategyId] = useState(initialStrategyId);
  const [strategyVersionId, setStrategyVersionId] = useState(initialStrategyVersionId);
  const [setupId, setSetupId] = useState(initialSetupId);
  const [symbol, setSymbol] = useState(initialSymbol);
  const [timeframe, setTimeframe] = useState(initialTimeframe);
  const [regime, setRegime] = useState<MarketRegime | "">(initialRegime);
  const [entryMethod, setEntryMethod] = useState<JournalEntryMethod | "">(initialEntryMethod);
  const [source, setSource] = useState<JournalTradeSource | "">(initialSource);
  const [dateFrom, setDateFrom] = useState(initialDateFrom);
  const [dateTo, setDateTo] = useState(initialDateTo);
  const [breakdownLimit, setBreakdownLimit] = useState(
    Number.isFinite(initialBreakdownLimit) && initialBreakdownLimit >= 1 && initialBreakdownLimit <= 50
      ? initialBreakdownLimit
      : 10,
  );

  // Day bounds use the shared resolved zone (risk settings → browser → UTC).
  const zone = useResolvedTimezone();
  const loader = useCallback(() => {
    if (!zone.ready && (dateFrom || dateTo)) {
      // Wait for the shared zone instead of querying UTC days first.
      return new Promise<never>(() => {});
    }
    const params: JournalComparisonParams = {
      strategy_id: strategyId.trim() || undefined,
      strategy_version_id: strategyVersionId.trim() || undefined,
      setup_id: setupId.trim() || undefined,
      symbol: symbol.trim() || undefined,
      timeframe: timeframe.trim() || undefined,
      market_regime: regime || undefined,
      entry_method: entryMethod || undefined,
      source: source || undefined,
      date_from: dateFrom ? zonedDayBoundsUtc(dateFrom, zone.timezone).start : undefined,
      date_to: dateTo ? zonedDayBoundsUtc(dateTo, zone.timezone).end : undefined,
      breakdown_limit: breakdownLimit,
    };
    return api.journal.comparison(params);
  }, [
    strategyId,
    strategyVersionId,
    setupId,
    symbol,
    timeframe,
    regime,
    entryMethod,
    source,
    dateFrom,
    dateTo,
    breakdownLimit,
    zone.ready,
    zone.timezone,
  ]);

  const { data, loading, error, reload } = useAsyncData(loader, [loader]);

  const orderedCohorts = useMemo(() => {
    if (!data) return [];
    const byKey = new Map(data.cohorts.map((c) => [c.cohort, c]));
    return COHORT_ORDER.map((key) => byKey.get(key)).filter(
      (c): c is JournalComparisonCohortResult => Boolean(c),
    );
  }, [data]);

  const orderedScorecards = useMemo(() => {
    if (!data) return [];
    const order: ExecutionActor[] = ["human", "system"];
    const byKey = new Map(data.scorecards.map((s) => [s.actor, s]));
    return order.map((key) => byKey.get(key)).filter((s): s is ComparisonScorecard => Boolean(s));
  }, [data]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Human vs System</h1>
        <p className="text-sm text-zinc-400">
          Record-only comparison of human and system execution over closed canonical journal trades.
          Advisory metrics only — never feeds execution or risk decisions.
        </p>
      </div>

      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-4">
          <div className="space-y-2">
            <Label htmlFor="cmp-strategy-id">Strategy ID</Label>
            <Input
              id="cmp-strategy-id"
              placeholder="UUID"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cmp-strategy-version-id">Strategy version ID</Label>
            <Input
              id="cmp-strategy-version-id"
              placeholder="UUID"
              value={strategyVersionId}
              onChange={(e) => setStrategyVersionId(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cmp-setup-id">Setup ID</Label>
            <Input
              id="cmp-setup-id"
              placeholder="UUID"
              value={setupId}
              onChange={(e) => setSetupId(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cmp-symbol">Symbol</Label>
            <Input
              id="cmp-symbol"
              placeholder="e.g. BTCUSDT"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cmp-timeframe">Timeframe</Label>
            <Input
              id="cmp-timeframe"
              placeholder="e.g. 1h"
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cmp-regime">Market regime</Label>
            <select
              id="cmp-regime"
              className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
              value={regime}
              onChange={(e) => setRegime(e.target.value as MarketRegime | "")}
            >
              <option value="">All regimes</option>
              {REGIME_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {humanizeToken(value)}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="cmp-entry-method">Entry method</Label>
            <select
              id="cmp-entry-method"
              className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
              value={entryMethod}
              onChange={(e) => setEntryMethod(e.target.value as JournalEntryMethod | "")}
            >
              <option value="">All methods</option>
              {ENTRY_METHOD_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {humanizeToken(value)}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="cmp-source">Source</Label>
            <select
              id="cmp-source"
              className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
              value={source}
              onChange={(e) => setSource(e.target.value as JournalTradeSource | "")}
            >
              <option value="">All sources</option>
              {SOURCE_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {humanizeToken(value)}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="cmp-date-from">From</Label>
            <Input
              id="cmp-date-from"
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cmp-date-to">To</Label>
            <Input
              id="cmp-date-to"
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
            />
            <p className="text-xs text-text-muted" data-testid="cmp-date-timezone">
              Days in {zone.timezone} ({TIMEZONE_SOURCE_LABELS[zone.source]})
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="cmp-breakdown-limit">Breakdown limit</Label>
            <Input
              id="cmp-breakdown-limit"
              type="number"
              min={1}
              max={50}
              value={breakdownLimit}
              onChange={(e) => {
                const next = Number(e.target.value);
                if (Number.isFinite(next)) {
                  setBreakdownLimit(Math.min(50, Math.max(1, next)));
                }
              }}
            />
          </div>
        </div>
      </div>

      {loading ? <LoadingState label="Loading human vs system comparison…" /> : null}
      {error ? <ErrorState message={error} onRetry={() => void reload()} /> : null}

      {data ? (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <ConfidenceBadge confidence={data.confidence} />
            {data.warnings.length ? (
              <p className="text-xs text-amber-300">
                {data.warnings.map((w) => w.message).join(" · ")}
              </p>
            ) : null}
          </div>

          {data.note ? (
            <p className="rounded-md border border-zinc-800 bg-zinc-900/40 p-3 text-xs text-zinc-400">
              {data.note}
            </p>
          ) : null}

          {data.cohorts.some((c) => c.truncated) || data.scorecards.some((s) => s.truncated) ? (
            <p className="rounded-md border border-amber-800 bg-amber-950/40 p-3 text-sm text-amber-300">
              Scan capped at {data.max_rows} closed trades — some cohorts may be partial. Narrow
              filters or date range for full coverage.
            </p>
          ) : null}

          {!hasAnyTrades(data) ? (
            <EmptyState
              title="No closed journal trades"
              description="Close canonical journal trades across human, paper-system, or backtest sources to compare performance and decision quality."
            />
          ) : (
            <>
              <section className="space-y-3">
                <h2 className="text-lg font-medium">Cohorts (AT-034)</h2>
                <div className="grid gap-4 lg:grid-cols-3">
                  {orderedCohorts.map((cohort) => (
                    <CohortCard key={cohort.cohort} cohort={cohort} />
                  ))}
                </div>
              </section>

              {orderedScorecards.length ? (
                <section className="space-y-3">
                  <h2 className="text-lg font-medium">Actor scorecards</h2>
                  <div className="grid gap-4 lg:grid-cols-2">
                    {orderedScorecards.map((scorecard) => (
                      <ScorecardCard key={scorecard.actor} scorecard={scorecard} />
                    ))}
                  </div>
                </section>
              ) : null}

              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle className="text-base">Overall decision quality</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <DecisionQualitySummary dq={data.decision_quality} />
                  <WarningsList warnings={data.decision_quality.warnings} />
                </CardContent>
              </Card>

              {data.breakdowns.map((breakdown) => (
                <BreakdownSection key={breakdown.dimension} breakdown={breakdown} />
              ))}

              <DimensionBucketsSection title="By entry method" buckets={data.by_entry_method} />
              <DimensionBucketsSection title="By source" buckets={data.by_source} />
              <DimensionBucketsSection title="Rule compliance" buckets={data.rule_compliance} />
            </>
          )}

          <section className="space-y-2">
            <h2 className="text-lg font-medium">Related</h2>
            <RelatedLinks links={data.links} />
          </section>

          <p className="text-xs text-zinc-500">
            Generated {new Date(data.generated_at).toLocaleString()}
          </p>
        </>
      ) : null}
    </div>
  );
}
