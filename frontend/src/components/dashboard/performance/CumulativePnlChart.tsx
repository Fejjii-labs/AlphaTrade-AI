"use client";

import { useId } from "react";
import dynamic from "next/dynamic";

import { Skeleton } from "@/components/ui/skeleton";
import type { JournalDashboardCurvePoint } from "@/lib/api/types";
import { formatMonetary } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { CurveDatum } from "./CumulativePnlPlot";
import { pnlTone, toNumber } from "./performance-model";

const lastTone = {
  profit: "text-success",
  loss: "text-danger",
  flat: "text-text-primary",
  none: "text-text-muted",
} as const;

// Recharts loads only when there is a curve to draw.
const CumulativePnlPlot = dynamic(() => import("./CumulativePnlPlot"), {
  ssr: false,
  loading: () => <Skeleton className="h-full w-full" />,
});

/** Gradient stop where the curve crosses zero (fraction from the top). */
export function zeroOffset(values: number[]): number {
  if (!values.length) return 0;
  const max = Math.max(...values);
  const min = Math.min(...values);
  if (max <= 0) return 0;
  if (min >= 0) return 1;
  return max / (max - min);
}

/** One-sentence text alternative for the curve. */
export function curveSummary(points: JournalDashboardCurvePoint[]): string {
  const first = points[0];
  const last = points.at(-1);
  if (!first || !last) return "";
  const values = points.map((point) => toNumber(point.cumulative_net_pnl) ?? 0);
  return (
    `Cumulative net P&L over ${points.length} trading day${points.length === 1 ? "" : "s"}, ` +
    `from ${formatMonetary(first.cumulative_net_pnl)} on ${first.date} ` +
    `to ${formatMonetary(last.cumulative_net_pnl)} on ${last.date}; ` +
    `high ${formatMonetary(Math.max(...values))}, low ${formatMonetary(Math.min(...values))}.`
  );
}

export function CumulativePnlChart({
  points,
  maxDrawdown = null,
  tradeCoverage,
  loading = false,
}: {
  points: JournalDashboardCurvePoint[];
  /** Peak-to-trough drop (positive magnitude) from the response's risk block. */
  maxDrawdown?: string | null;
  tradeCoverage?: string;
  loading?: boolean;
}) {
  const gradientId = useId().replace(/:/g, "");
  const data: CurveDatum[] = points.map((point) => ({
    date: point.date,
    cumulative: toNumber(point.cumulative_net_pnl) ?? 0,
    daily: toNumber(point.daily_net_pnl) ?? 0,
  }));
  const offset = zeroOffset(data.map((d) => d.cumulative));
  const last = points.at(-1);
  return (
    <section
      className="relative rounded-card border border-border-subtle bg-surface-1 p-4"
      aria-label="Daily net cumulative P&L"
      data-testid="cumulative-pnl"
      aria-busy={loading || undefined}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold text-text-primary">Daily net cumulative P&amp;L</h3>
        <div className="text-right">
          <span
            className={cn("block font-data text-base font-semibold", lastTone[pnlTone(last?.cumulative_net_pnl)])}
            data-testid="cumulative-last"
          >
            {last ? formatMonetary(last.cumulative_net_pnl) : "—"}
          </span>
          {last && tradeCoverage && !loading ? (
            <span className="block text-xs text-text-secondary">{tradeCoverage}</span>
          ) : null}
          {toNumber(maxDrawdown) !== null && !loading ? (
            <span className="block text-[11px] text-text-muted" data-testid="cumulative-drawdown">
              Max drawdown <span className="font-data text-danger">−{Math.abs(toNumber(maxDrawdown) ?? 0).toFixed(2)}</span>
            </span>
          ) : null}
        </div>
      </div>
      {loading ? (
        <Skeleton className="mt-2 h-56 w-full" data-testid="cumulative-loading" />
      ) : data.length ? (
        <div className="mt-2 h-56 w-full" aria-hidden="true">
          <CumulativePnlPlot data={data} offset={offset} gradientId={gradientId} />
        </div>
      ) : (
        <p className="my-8 text-center text-sm text-text-muted">
          No closed trades in this range.
        </p>
      )}
      {points.length ? (
        <>
          <p className="sr-only" data-testid="cumulative-summary">
            {curveSummary(points)}
          </p>
          <div className="sr-only">
            <table>
            <caption>Daily and cumulative net P&amp;L</caption>
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Day net</th>
                <th scope="col">Cumulative net</th>
              </tr>
            </thead>
            <tbody>
              {points.map((point) => (
                <tr key={point.date}>
                  <th scope="row">{point.date}</th>
                  <td>{formatMonetary(point.daily_net_pnl)}</td>
                  <td>{formatMonetary(point.cumulative_net_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </>
      ) : null}
    </section>
  );
}
