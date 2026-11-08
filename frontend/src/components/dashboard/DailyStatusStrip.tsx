"use client";

import { Badge } from "@/components/ui/badge";
import type { SourceResult } from "@/components/workflows/sourceResult";
import type { DashboardSummary } from "@/lib/api/types";
import { formatCount, humanizeToken } from "@/lib/format";

export type KillSwitchDisplay = {
  /** False while the status is loading or could not be refreshed. */
  known: boolean;
  blocked: boolean;
};

/**
 * Persistent breaker and discipline strip shown in every dashboard venue, so
 * the trader never reads equity or PnL without today's pause state next to it.
 * The daily loss limit is display only (AT-ADR-152): the account daily equity
 * stop is the brake and it arms the kill switch.
 */
export function DailyStatusStrip({
  summary,
  killSwitch,
}: {
  summary: SourceResult<DashboardSummary>;
  killSwitch?: KillSwitchDisplay;
}) {
  const daily = summary.available ? summary.data?.daily_discipline ?? null : null;
  return (
    <section
      aria-label="Today's status"
      data-testid="dashboard-status-strip"
      className="flex flex-wrap items-center gap-2 rounded-control border border-border-subtle bg-surface-1 px-3 py-2 text-xs text-text-secondary"
    >
      {killSwitch?.known ? (
        killSwitch.blocked ? (
          <Badge variant="danger" data-testid="dashboard-strip-kill-switch">
            Kill switch active · new entries paused
          </Badge>
        ) : (
          <Badge variant="success" data-testid="dashboard-strip-kill-switch">
            Kill switch off
          </Badge>
        )
      ) : (
        <Badge variant="warning" data-testid="dashboard-strip-kill-switch">
          Kill switch status unknown
        </Badge>
      )}
      {summary.pending ? (
        <span data-testid="dashboard-strip-loading">Loading today’s status…</span>
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
            data-testid="dashboard-strip-discipline"
          >
            {humanizeToken(daily.discipline_status)}
          </Badge>
          {daily.loss_lock_active ? (
            <Badge variant="warning" title="Display only; the account daily equity stop arms the kill switch.">
              Daily loss limit reached (display only)
            </Badge>
          ) : null}
          {daily.overtrading_warning_active ? (
            <Badge variant="warning">Overtrading warning</Badge>
          ) : null}
          {daily.green_day_protection_active ? (
            <Badge variant="warning">Green day protection</Badge>
          ) : null}
          <span className="font-data">
            {formatCount(daily.remaining_trades_allowed)} trades remaining
            {daily.max_trades_per_day != null
              ? ` of ${formatCount(daily.max_trades_per_day)}`
              : ""}
          </span>
          <span className="text-text-muted">
            {daily.date} · {daily.timezone}
          </span>
        </>
      ) : (
        <span data-testid="dashboard-strip-unavailable">Daily status unavailable</span>
      )}
    </section>
  );
}
