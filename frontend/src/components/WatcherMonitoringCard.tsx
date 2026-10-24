"use client";

import Link from "next/link";

import { StatusBadge } from "@/components/StatusBadge";
import { WorkerRuntimeReadout } from "@/components/WorkerRuntimeReadout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime, UNAVAILABLE } from "@/lib/format";
import type { WatcherMonitoringSnapshot } from "@/lib/api/types";
import {
  displayedWatcherStatus,
  formatReasonCode,
  watcherStatusLabel,
  watcherStatusTone,
} from "@/lib/watcher-monitoring";

function listOrNone(values: string[]): string {
  return values.length ? values.join(", ") : UNAVAILABLE;
}

export function WatcherMonitoringCard({
  snapshot,
  compact = false,
  onRefresh,
}: {
  snapshot: WatcherMonitoringSnapshot;
  compact?: boolean;
  onRefresh?: () => void;
}) {
  const status = displayedWatcherStatus(snapshot);
  const strategies = snapshot.approved_strategies.map((item) => item.name);
  const assessmentStates = snapshot.setup_assessments.map((item) => item.state);
  const lease = snapshot.leases[0];

  return (
    <Card data-testid="watcher-monitoring-card" className="min-w-0">
      <CardHeader className={compact ? "pb-2" : undefined}>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">Watcher paper monitoring</CardTitle>
            <p className="mt-1 text-xs text-text-muted">
              Runtime evidence only. Configuration flags are not treated as RUNNING.
            </p>
          </div>
          {onRefresh ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="min-h-11"
              onClick={onRefresh}
              data-testid="watcher-monitoring-refresh"
            >
              Refresh
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm text-zinc-300">
        <div className="flex flex-wrap gap-2" data-testid="watcher-monitoring-status-row">
          <StatusBadge
            label={watcherStatusLabel(status)}
            tone={watcherStatusTone(status)}
          />
          <StatusBadge
            label={`Paper monitoring ${snapshot.paper_monitoring_status}`}
            tone={watcherStatusTone(snapshot.paper_monitoring_status)}
          />
          <span data-testid="watcher-monitoring-paper-only">
            <StatusBadge label="Paper only" tone="paper" />
          </span>
          <span data-testid="watcher-monitoring-real-trading">
            {snapshot.paper_posture.real_trading_enabled ? (
              <StatusBadge label="Real trading ON" tone="blocked" />
            ) : (
              <StatusBadge label="Real trading OFF" tone="healthy" />
            )}
          </span>
          {snapshot.paper_posture.runtime_evidence ? (
            <StatusBadge label="Runtime evidence" tone="healthy" />
          ) : (
            <StatusBadge label="No runtime evidence" tone="muted" />
          )}
        </div>

        <dl className="grid min-w-0 gap-2 sm:grid-cols-2" data-testid="watcher-monitoring-facts">
          <div>
            <dt className="text-xs text-text-muted">Symbols monitored</dt>
            <dd className="break-words" data-testid="watcher-monitoring-symbols">
              {listOrNone(snapshot.symbols_monitored)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">Approved strategies</dt>
            <dd data-testid="watcher-monitoring-strategies">
              {strategies.length ? strategies.join(", ") : "None approved"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">Last scan</dt>
            <dd data-testid="watcher-monitoring-last-scan">
              {snapshot.last_scan_at
                ? `${formatDateTime(snapshot.last_scan_at)} (${snapshot.last_scan_status ?? UNAVAILABLE})`
                : "never"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">Next scan</dt>
            <dd data-testid="watcher-monitoring-next-scan">
              {snapshot.next_scan_at
                ? `${formatDateTime(snapshot.next_scan_at)}${snapshot.next_scan_basis ? ` · ${formatReasonCode(snapshot.next_scan_basis)}` : ""}`
                : UNAVAILABLE}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">Market freshness</dt>
            <dd data-testid="watcher-monitoring-freshness">
              {snapshot.market_freshness.status}
              {snapshot.market_freshness.symbol ? ` · ${snapshot.market_freshness.symbol}` : ""}
              {snapshot.market_freshness.usable_as_current_market_price
                ? " · live mark"
                : " · not a live perpetual mark"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">Freshness clocks</dt>
            <dd data-testid="watcher-monitoring-freshness-clocks">
              {`quote ${snapshot.market_freshness.quote_fresh ? "fresh" : "not fresh"} · stream ${
                snapshot.market_freshness.trade_stream_fresh ? "fresh" : "not fresh"
              } · candle ${
                snapshot.market_freshness.closed_candle_final == null
                  ? "unknown"
                  : snapshot.market_freshness.closed_candle_final
                    ? "final"
                    : "forming"
              } · historical ${
                snapshot.market_freshness.historical_evidence_valid == null
                  ? "unknown"
                  : snapshot.market_freshness.historical_evidence_valid
                    ? "valid"
                    : "invalid"
              } · setup ${
                snapshot.market_freshness.setup_lifetime_expired == null
                  ? "unknown"
                  : snapshot.market_freshness.setup_lifetime_expired
                    ? "expired"
                    : "open"
              } · quote policy ${snapshot.market_freshness.quote_max_age_seconds ?? 10}s`}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">Provider health</dt>
            <dd data-testid="watcher-monitoring-providers">
              {snapshot.provider_health.length
                ? snapshot.provider_health
                    .map((item) => `${item.name}: ${item.health}`)
                    .join(", ")
                : UNAVAILABLE}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">SetupAssessment</dt>
            <dd data-testid="watcher-monitoring-assessments">
              {assessmentStates.length ? assessmentStates.join(", ") : "None persisted"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">Scanner candidates</dt>
            <dd data-testid="watcher-monitoring-candidates">
              {snapshot.scanner_candidates.count > 0
                ? `${snapshot.scanner_candidates.count} · ${snapshot.scanner_candidates.conditions.join(", ")}`
                : "None"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-text-muted">Canonical candidates</dt>
            <dd data-testid="watcher-monitoring-canonical-candidates">
              {snapshot.canonical_candidates.length
                ? snapshot.canonical_candidates
                    .map((item) => `${item.candidate_id} · ${item.state}`)
                    .join(", ")
                : "None"}
            </dd>
          </div>
        </dl>

        {snapshot.limitations.length ? (
          <ul className="text-xs text-text-muted" data-testid="watcher-monitoring-limitations">
            {snapshot.limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : null}

        {snapshot.block_reasons.length ? (
          <ul className="text-xs text-danger" data-testid="watcher-monitoring-block-reasons">
            {snapshot.block_reasons.map((reason) => (
              <li key={reason}>{formatReasonCode(reason)}</li>
            ))}
          </ul>
        ) : null}

        {snapshot.warnings.length ? (
          <ul className="text-xs text-amber-400" data-testid="watcher-monitoring-warnings">
            {snapshot.warnings.map((warning) => (
              <li key={warning}>{formatReasonCode(warning)}</li>
            ))}
          </ul>
        ) : null}

        <div data-testid="watcher-monitoring-worker-flags">
          <p className="mb-1 text-xs text-text-muted">
            Flags below come from the worker report. A missing report is not the API process.
          </p>
          <WorkerRuntimeReadout
            observation={
              snapshot.worker_effective?.source === "worker" ? snapshot.worker_effective : null
            }
          />
        </div>

        <p className="text-xs text-text-muted" data-testid="watcher-monitoring-lease">
          Lease/worker:{" "}
          {lease
            ? `${lease.fenced ? "fenced" : "unfenced"} · ${lease.orchestration_state ?? UNAVAILABLE}`
            : "no orchestration lease"}
          {` · worker heartbeat ${snapshot.worker.heartbeat_live ? "live" : "idle"}`}
        </p>

        {snapshot.recent_errors.length ? (
          <ul className="text-xs text-amber-400" data-testid="watcher-monitoring-errors">
            {snapshot.recent_errors.slice(0, compact ? 2 : 5).map((item) => (
              <li key={`${item.source}-${item.reason_code ?? item.message}`}>
                {item.source}: {item.message}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-text-muted" data-testid="watcher-monitoring-errors-empty">
            No recent Watcher errors.
          </p>
        )}

        <p className="text-xs text-text-muted" data-testid="watcher-monitoring-reason">
          Reason: {formatReasonCode(snapshot.reason_code)}
        </p>

        <div className="flex flex-wrap gap-3 text-xs">
          <Link href="/watcher" className="text-sky-400 underline">
            Watcher scanner
          </Link>
          <Link href="/market-watcher" className="text-sky-400 underline">
            Market Watcher
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
