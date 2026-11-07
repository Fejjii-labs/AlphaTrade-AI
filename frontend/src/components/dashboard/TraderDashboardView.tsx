"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { NativeActivity } from "@/components/activity/NativeActivity";
import { useAuth } from "@/contexts/AuthContext";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";

import { AttentionCard } from "./AttentionCard";
import { DailyReviewCard } from "./DailyReviewCard";
import { BloFinDemoAccountCard } from "./BloFinDemoAccountCard";
import { PerformanceDashboard } from "./performance/PerformanceDashboard";
import {
  DEFAULT_SCOPE,
  EXTRA_SCOPE_OPTIONS,
  SCOPE_LABELS,
  SCOPE_OPTIONS,
} from "./performance/performance-model";

import {
  closedStrategyRows,
  importantAlerts,
  marketEvidenceSummary,
  portfolioEquity,
  portfolioExpectancy,
  portfolioPnl,
  portfolioWinRate,
  watcherTraderLabel,
} from "@/components/dashboard/trader-dashboard";
import { TradingMetric } from "@/components/dashboard/TradingMetric";
import { EmptyState, UnavailableState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataNumber } from "@/components/ui/data-number";
import { PageHeader } from "@/components/ui/page-header";
import { SegmentedControl } from "@/components/ui/segmented-control";
import type { SafetyPostureDisplay } from "@/components/workflows/safetyPostureDisplay";
import type { SourceResult } from "@/components/workflows/sourceResult";
import {
  formatCount,
  formatDateTime,
  formatMonetary,
  humanizeToken,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  CanonicalMarketMonitorStatusRead,
  DashboardSummary,
  JournalDashboardScope,
  JournalStatsResponse,
  PaginatedCanonicalJournalTrades,
  PaperAlert,
  PaperPortfolioResponse,
  WatcherMonitoringSnapshot,
} from "@/lib/api/types";

export type TraderDashboardData = {
  portfolio: SourceResult<PaperPortfolioResponse>;
  journal: SourceResult<PaginatedCanonicalJournalTrades>;
  strategyStats: SourceResult<JournalStatsResponse>;
  summary: SourceResult<DashboardSummary>;
  watcher: SourceResult<WatcherMonitoringSnapshot>;
  market: SourceResult<CanonicalMarketMonitorStatusRead>;
  alerts: SourceResult<{ items: PaperAlert[]; total: number }>;
};

/** One venue for the whole page: performance, account card and activity. */
export type DashboardVenue = JournalDashboardScope;

export function DashboardVenueControl({
  value,
  onChange,
  realTradingEnabled,
}: {
  value: DashboardVenue;
  onChange: (next: DashboardVenue) => void;
  realTradingEnabled: boolean | null | undefined;
}) {
  // Real is only selectable when the runtime posture verifiably enables it;
  // false or unverified keeps it disabled.
  const realDisabled = realTradingEnabled !== true;
  const extra = EXTRA_SCOPE_OPTIONS.some((option) => option.value === value);
  return (
    <div
      className="flex flex-wrap items-center gap-3 text-sm"
      data-testid="dashboard-venue-control"
    >
      <span className="text-text-secondary">Venue</span>
      <SegmentedControl
        aria-label="Dashboard venue"
        value={extra ? ("" as DashboardVenue) : value}
        onChange={onChange}
        options={SCOPE_OPTIONS.map((option) => ({
          ...option,
          disabled: option.value === "real" && realDisabled,
        }))}
      />
      <label className="inline-flex items-center gap-1.5 text-xs text-text-secondary">
        <span className="sr-only sm:not-sr-only">Other</span>
        <select
          aria-label="Other trade scopes"
          value={extra ? value : ""}
          onChange={(event) =>
            onChange((event.target.value || DEFAULT_SCOPE) as DashboardVenue)
          }
          className="min-h-9 rounded-control border border-border bg-surface-1 px-2 text-xs text-text-primary"
        >
          <option value="">—</option>
          {EXTRA_SCOPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      {realDisabled ? (
        <span className="text-xs text-text-muted" data-testid="dashboard-venue-real-disabled">
          Real is unavailable while real trading is disabled.
        </span>
      ) : null}
    </div>
  );
}

function SectionEmpty({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <EmptyState title={title} description={description} className="py-6" />
  );
}

export function TraderDashboardView({
  data,
  posture,
  onRetry,
  refreshing = false,
  demoRefreshKey = 0,
  venue: controlledVenue,
  onVenueChange,
  realTradingEnabled = false,
}: {
  data: TraderDashboardData;
  posture: SafetyPostureDisplay;
  onRetry?: () => void;
  refreshing?: boolean;
  demoRefreshKey?: number;
  /** Page-owned venue (so loaders can be gated on it); local state otherwise. */
  venue?: DashboardVenue;
  onVenueChange?: (next: DashboardVenue) => void;
  realTradingEnabled?: boolean | null;
}) {
  const { user, organization } = useAuth();
  const [session, setSession] = useState(sessionGeneration);
  useEffect(() => onSessionCleared(() => setSession(sessionGeneration())), []);
  const accountContext = JSON.stringify([organization?.id, user?.id, session]);
  const [ownVenue, setOwnVenue] = useState<DashboardVenue>(DEFAULT_SCOPE);
  const venue = controlledVenue ?? ownVenue;
  const setVenue = onVenueChange ?? setOwnVenue;
  // Demo = configured BloFin demo account; Paper = internal simulator history.
  const demo = venue === "demo";
  const simulator = venue === "paper";
  const winRate = portfolioWinRate(data.portfolio);
  const expectancy = portfolioExpectancy(data.portfolio);
  const closedByStrategy = closedStrategyRows(data.portfolio);
  const alerts = importantAlerts(data.alerts);
  const market = marketEvidenceSummary(data.market);
  const portfolioPending = Boolean(data.portfolio.pending);
  const metric = (value: string) => (portfolioPending ? "Loading…" : value);
  const daily = data.summary.available
    ? data.summary.data?.daily_discipline
    : null;
  const watcherLabel = data.watcher.pending
    ? "Loading…"
    : data.watcher.available
      ? watcherTraderLabel(data.watcher.data?.paper_monitoring_status)
      : "Unavailable";
  const marketLabel = data.market.pending ? "Loading…" : market.label;
  const unavailable = [
    ["Portfolio", data.portfolio],
    ["Recent trades", data.journal],
    ["Journal statistics", data.strategyStats],
    ["Daily status", data.summary],
    ["Watcher", data.watcher],
    ["Market evidence", data.market],
    ["Alerts", data.alerts],
  ]
    .filter(([, source]) => {
      const result = source as SourceResult<unknown>;
      return !result.available && !result.pending;
    })
    .map(([name]) => name)
    .join(", ");

  return (
    <div className="page-sections" data-testid="trader-dashboard">
      <PageHeader
        title="Dashboard"
        description="Journal performance, BloFin demo account, recent activity, and attention areas."
        actions={
          <>
            <Link
              href="/agent"
              className={cn(
                buttonVariants({ variant: "outline" }),
                "h-11 border-accent-border bg-accent-muted text-accent hover:bg-accent-muted",
              )}
            >
              Open Agent
            </Link>
            <Button variant="outline" className="h-11" onClick={onRetry} disabled={refreshing}>
              {refreshing ? "Refreshing…" : "Refresh"}
            </Button>
          </>
        }
      />
      {posture.conflictMessage ? (
        <p
          role="alert"
          className="text-sm text-danger"
          data-testid="dashboard-safety-conflict"
        >
          {posture.conflictMessage}
        </p>
      ) : null}
      {simulator && unavailable ? (
        <div
          role="status"
          className="rounded-control border border-warning-border bg-warning-muted p-3 text-sm text-warning"
        >
          Unavailable: {unavailable}. Available sections remain visible.
        </div>
      ) : null}
      {refreshing ? (
        <p role="status" className="text-sm text-text-secondary">
          Refreshing dashboard; showing the previous snapshot.
        </p>
      ) : null}

      <DashboardVenueControl
        value={venue}
        onChange={setVenue}
        realTradingEnabled={realTradingEnabled}
      />

      <PerformanceDashboard
        key={`performance-${accountContext}`}
        refreshKey={demoRefreshKey}
        scope={venue}
      />

      {!demo && !simulator ? (
        <p
          className="rounded-control border border-dashed border-border bg-surface-1 px-3 py-2 text-sm text-text-muted"
          data-testid="dashboard-venue-no-account"
        >
          No account snapshot for {SCOPE_LABELS[venue]}. The performance figures above
          cover journal trades in this scope only.
        </p>
      ) : null}
      {demo && user && organization ? (
        <div key={accountContext} className="page-sections">
          <BloFinDemoAccountCard refreshKey={demoRefreshKey} />
          <NativeActivity statistics refreshKey={demoRefreshKey} />
        </div>
      ) : null}
      {simulator ? (
        <>
          <Card>
            <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
              <CardTitle>Internal simulator</CardTitle>
              <p className="text-xs text-text-secondary">
                {data.portfolio.available && data.portfolio.data?.account.as_of
                  ? `As of ${formatDateTime(data.portfolio.data.account.as_of)}`
                  : "Account snapshot unavailable"}
              </p>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3 lg:grid-cols-3">
              <p className="col-span-2 text-xs text-text-secondary lg:col-span-3">
                All metrics in this view cover internal simulator proposal and
                validation history.
              </p>
              <TradingMetric
                label="Portfolio value"
                value={metric(portfolioEquity(data.portfolio))}
                note="Paper account equity"
                testId="dashboard-equity"
              />
              <TradingMetric
                label="Realized PnL"
                value={metric(portfolioPnl(data.portfolio))}
                amount={data.portfolio.data?.metrics.net_pnl}
                note="Closed paper trades · net of costs"
                testId="dashboard-pnl"
              />
              <TradingMetric
                label="Open positions"
                value={metric(
                  formatCount(data.portfolio.data?.account.open_trade_count),
                )}
                note="Internal simulator · proposal and validation history"
                testId="dashboard-open-count"
              />
              <TradingMetric
                label="Win rate"
                value={metric(winRate.value)}
                note={winRate.note ?? "Portfolio unavailable"}
                testId="dashboard-win-rate"
              />
              <TradingMetric
                label="Expectancy"
                value={metric(expectancy.value)}
                amount={data.portfolio.data?.metrics.expectancy}
                note={expectancy.note}
                testId="dashboard-expectancy"
              />
              <TradingMetric
                label="Today’s realized PnL"
                value={formatMonetary(daily?.realized_pnl_today_paper)}
                amount={daily?.realized_pnl_today_paper}
                note={
                  daily
                    ? `${daily.date} · ${daily.timezone}`
                    : "Daily status unavailable"
                }
                testId="dashboard-daily-pnl"
              />
            </CardContent>
          </Card>
        </>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <AttentionCard />
        <DailyReviewCard compact />
      </div>

      {simulator ? (
        <details>
          <summary className="min-h-11 cursor-pointer text-sm">
            Simulator daily status &amp; monitoring
          </summary>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card data-testid="dashboard-daily-status">
              <CardHeader>
                <CardTitle>Daily status</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {data.summary.pending ? (
                  <p className="text-sm text-text-muted" data-testid="dashboard-daily-loading">
                    Loading…
                  </p>
                ) : daily ? (
                  <>
                    <Badge
                      variant={
                        daily.loss_lock_active
                          ? "danger"
                          : daily.discipline_status === "calm"
                            ? "success"
                            : "warning"
                      }
                    >
                      {humanizeToken(daily.discipline_status)}
                    </Badge>
                    <p className="text-sm leading-relaxed text-text-primary">
                      {daily.recommended_action}
                    </p>
                    <p className="text-xs text-text-secondary">
                      Proposal and validation activity · {daily.date} ·{" "}
                      {daily.timezone}:{" "}
                      {formatCount(daily.paper_trades_opened_today)} opened ·{" "}
                      {formatCount(daily.paper_trades_closed_today)} closed ·{" "}
                      {formatCount(daily.remaining_trades_allowed)} remaining
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {daily.loss_lock_active ? (
                        <Badge variant="danger">Loss lock active</Badge>
                      ) : null}
                      {daily.green_day_protection_active ? (
                        <Badge variant="warning">Green day protection</Badge>
                      ) : null}
                      {daily.overtrading_warning_active ? (
                        <Badge variant="warning">Overtrading warning</Badge>
                      ) : null}
                    </div>
                    {daily.reasons.length ? (
                      <ul className="space-y-1 text-xs text-text-secondary">
                        {daily.reasons.map((reason) => (
                          <li key={reason}>{reason}</li>
                        ))}
                      </ul>
                    ) : null}
                    {daily.limitations.length ? (
                      <p className="text-xs text-text-secondary">
                        {daily.limitations.join(" · ")}
                      </p>
                    ) : null}
                  </>
                ) : (
                  <UnavailableState
                    message="Daily status unavailable"
                    onRetry={onRetry}
                    className="py-4"
                  />
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Monitoring</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div data-testid="dashboard-watcher-status">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm text-text-secondary">Watcher</p>
                    <Badge
                      variant={
                        watcherLabel === "Watching"
                          ? "success"
                          : watcherLabel === "Unavailable" ||
                              watcherLabel === "Stopped"
                            ? "muted"
                            : "warning"
                      }
                    >
                      {watcherLabel}
                    </Badge>
                  </div>
                  {data.watcher.available && data.watcher.data?.last_scan_at ? (
                    <p className="mt-2 text-xs text-text-secondary">
                      Last scan {formatDateTime(data.watcher.data.last_scan_at)}
                    </p>
                  ) : null}
                  <Link
                    href="/watcher"
                    className="mt-2 inline-flex min-h-11 items-center text-sm text-accent hover:underline"
                  >
                    Watcher details
                  </Link>
                </div>
                <div
                  className="border-t border-border-subtle pt-3"
                  data-testid="dashboard-market-evidence"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm text-text-secondary">
                      Market evidence
                    </p>
                    <Badge
                      variant={
                        marketLabel === "Healthy"
                          ? "success"
                          : marketLabel === "Unavailable"
                            ? "muted"
                            : "warning"
                      }
                    >
                      {marketLabel}
                    </Badge>
                  </div>
                  {market.symbol ? (
                    <p className="mt-2 break-words text-xs text-text-secondary">
                      {market.symbol}
                    </p>
                  ) : null}
                  <Link
                    href="/market"
                    className="inline-flex min-h-11 items-center text-sm text-accent hover:underline"
                  >
                    Market monitor
                  </Link>
                </div>
              </CardContent>
            </Card>
            <Card
              data-testid="dashboard-alerts"
              className="order-first lg:order-none"
            >
              <CardHeader className="flex-row flex-wrap items-center justify-between">
                <CardTitle>Important alerts</CardTitle>
                <Link
                  href="/alerts"
                  className="inline-flex min-h-11 items-center text-sm text-accent hover:underline"
                >
                  All alerts
                </Link>
              </CardHeader>
              <CardContent>
                {data.alerts.pending ? (
                  <p className="text-sm text-text-muted">Loading…</p>
                ) : alerts == null ? (
                  <UnavailableState
                    message="Alerts unavailable"
                    onRetry={onRetry}
                    className="py-4"
                  />
                ) : alerts.length === 0 ? (
                  <SectionEmpty
                    title="No alerts"
                    description="New alerts will appear here for review."
                  />
                ) : (
                  <ul className="space-y-3">
                    {alerts.map((alert) => (
                      <li
                        key={alert.id}
                        className="border-b border-border-subtle pb-3 last:border-b-0 last:pb-0"
                      >
                        <div className="flex flex-wrap gap-2">
                          <Badge
                            variant={
                              alert.severity === "critical" ||
                              alert.severity === "high"
                                ? "danger"
                                : "muted"
                            }
                          >
                            {humanizeToken(alert.severity)}
                          </Badge>
                          {!alert.read_at ? (
                            <span className="text-xs text-text-secondary">
                              Unread
                            </span>
                          ) : null}
                        </div>
                        <p className="mt-2 break-words text-sm leading-relaxed text-text-primary">
                          {alert.message}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </div>
        </details>
      ) : null}
      {simulator ? <Card>
        <CardHeader>
          <CardTitle>Recent simulator activity</CardTitle>
        </CardHeader>
        <CardContent>
          {data.journal.pending ? (
            <p className="text-sm text-text-muted">Loading…</p>
          ) : data.journal.available ? (
            <ul className="space-y-2">
              {data.journal.data?.items
                .slice(0, 5)
                .map((t) => (
                  <li
                    key={t.id}
                    className="flex flex-wrap items-center justify-between gap-2 text-sm"
                  >
                    <span>
                      {t.symbol} · {t.direction} · {t.status}
                      {t.source === "manual_demo_test"
                        ? " · Connectivity test"
                        : ""}
                    </span>
                    <Link
                      href={`/journal?trade_id=${t.id}`}
                      className="min-h-11 inline-flex items-center text-accent"
                    >
                      Open exact trade
                    </Link>
                  </li>
                ))}
            </ul>
          ) : (
            <p className="text-sm text-text-muted">Activity unavailable.</p>
          )}
          <Link
            href="/journal"
            className="inline-flex min-h-11 items-center text-sm text-accent"
          >
            Open Trades
          </Link>
        </CardContent>
      </Card> : null}
      {simulator ? (
        <>
          <p className="text-sm text-text-secondary">
            Internal simulator history · proposal and validation activity.{" "}
            <Link href="/portfolio" className="text-accent underline">
              Open simulator portfolio
            </Link>
          </p>
          <Card data-testid="dashboard-strategy-performance">
            <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
              <CardTitle>Performance by strategy</CardTitle>
              <Link
                href="/portfolio"
                className="inline-flex min-h-11 items-center text-sm text-accent hover:underline"
              >
                Simulator statistics
              </Link>
            </CardHeader>
            <CardContent className="grid gap-6 lg:grid-cols-2">
              <div className="min-w-0">
                <h4 className="mb-3 text-sm font-medium">
                  Closed paper results
                </h4>
                {portfolioPending ? (
                  <p className="text-sm text-text-muted">Loading…</p>
                ) : closedByStrategy == null ? (
                  <UnavailableState
                    message="Closed paper strategy performance unavailable"
                    onRetry={onRetry}
                    className="py-4"
                  />
                ) : closedByStrategy.length === 0 ? (
                  <SectionEmpty
                    title="No closed paper strategy results"
                    description="Results appear after paper trades close."
                  />
                ) : (
                  <ul>
                    {closedByStrategy.map((row) => (
                      <li
                        key={row.key}
                        className="flex items-center justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0"
                      >
                        <div className="min-w-0">
                          <p className="break-words text-sm font-medium">
                            {row.key}
                          </p>
                          <p className="mt-1 text-xs text-text-secondary">
                            {formatCount(row.metrics.trade_count)} closed trades
                          </p>
                        </div>
                        <DataNumber
                          value={formatMonetary(row.metrics.net_pnl)}
                          className="shrink-0"
                        />
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}
