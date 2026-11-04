"use client";

import { useId } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipProps } from "recharts";

import type { JournalDashboardCurvePoint } from "@/lib/api/types";
import { formatMonetary } from "@/lib/format";

import { formatCompactPnl, toNumber } from "./performance-model";

type CurveDatum = { date: string; cumulative: number; daily: number };

function shortDate(iso: string): string {
  return `${iso.slice(5, 7)}/${iso.slice(8, 10)}`;
}

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

function CurveTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const datum = payload[0].payload as CurveDatum;
  return (
    <div className="rounded-control border border-border-subtle bg-surface-2 px-3 py-2 text-xs text-text-primary shadow-elevation2">
      <p className="font-medium text-text-secondary">{datum.date}</p>
      <p className="font-data">Cumulative {formatMonetary(datum.cumulative)}</p>
      <p className="font-data text-text-secondary">Day {formatMonetary(datum.daily)}</p>
    </div>
  );
}

export function CumulativePnlChart({ points }: { points: JournalDashboardCurvePoint[] }) {
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
      className="rounded-card border border-border-subtle bg-surface-1 p-4"
      aria-label="Daily net cumulative P&L"
      data-testid="cumulative-pnl"
    >
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-text-primary">Daily net cumulative P&amp;L</h3>
        <span className="font-data text-xs text-text-secondary" data-testid="cumulative-last">
          {last ? formatMonetary(last.cumulative_net_pnl) : "—"}
        </span>
      </div>
      {data.length ? (
        <div className="mt-2 h-56 w-full" aria-hidden="true">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id={`fill-${gradientId}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset={0} stopColor="var(--color-success)" stopOpacity={0.35} />
                  <stop offset={offset} stopColor="var(--color-success)" stopOpacity={0.08} />
                  <stop offset={offset} stopColor="var(--color-danger)" stopOpacity={0.08} />
                  <stop offset={1} stopColor="var(--color-danger)" stopOpacity={0.35} />
                </linearGradient>
                <linearGradient id={`stroke-${gradientId}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset={offset} stopColor="var(--color-success)" />
                  <stop offset={offset} stopColor="var(--color-danger)" />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--color-border-subtle)" vertical={false} />
              <XAxis
                dataKey="date"
                tickFormatter={shortDate}
                tick={{ fill: "var(--color-text-muted)", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                minTickGap={24}
              />
              <YAxis
                tickFormatter={(value: number) => formatCompactPnl(value)}
                tick={{ fill: "var(--color-text-muted)", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={64}
              />
              <ReferenceLine y={0} stroke="var(--color-border-strong)" />
              <Tooltip content={<CurveTooltip />} />
              <Area
                type="linear"
                dataKey="cumulative"
                stroke={`url(#stroke-${gradientId})`}
                strokeWidth={2}
                fill={`url(#fill-${gradientId})`}
                baseValue={0}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="my-8 text-center text-sm text-text-muted">
          No closed trades with recorded P&amp;L in this range.
        </p>
      )}
      {points.length ? (
        <>
          <p className="sr-only" data-testid="cumulative-summary">
            {curveSummary(points)}
          </p>
          <table className="sr-only">
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
        </>
      ) : null}
    </section>
  );
}
