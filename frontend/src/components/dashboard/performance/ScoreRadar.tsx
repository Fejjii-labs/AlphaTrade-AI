"use client";

import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import type { TooltipProps } from "recharts";

import type { JournalDashboardScoreComponent } from "@/lib/api/types";
import { UNAVAILABLE } from "@/lib/format";

type RadarDatum = { axis: string; score: number | null; raw: string };
type ScoredDatum = RadarDatum & { score: number };

function rawValue(component: JournalDashboardScoreComponent): string {
  if (component.value === null) return component.note ?? UNAVAILABLE;
  if (component.key === "win_rate" || component.key === "consistency" || component.key === "max_drawdown") {
    return `${(component.value * 100).toFixed(1)}%`;
  }
  return component.value.toFixed(2);
}

function RadarTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const datum = payload[0].payload as ScoredDatum;
  return (
    <div className="rounded-control border border-border-subtle bg-surface-2 px-3 py-2 text-xs text-text-primary shadow-elevation2">
      <p className="font-medium text-text-secondary">{datum.axis}</p>
      <p className="font-data">
        Score {datum.score.toFixed(0)} · {datum.raw}
      </p>
    </div>
  );
}

export function ScoreRadar({
  overall,
  components,
}: {
  overall: number | null;
  components: JournalDashboardScoreComponent[];
}) {
  const rows: RadarDatum[] = components.map((component) => ({
    axis: component.label,
    score: component.score,
    raw: rawValue(component),
  }));
  // Unscored axes are left off the polygon (never drawn as 0) and listed as n/a.
  const scored = rows.filter((row): row is ScoredDatum => row.score !== null);
  const unscored = rows.filter((row) => row.score === null);
  const marker = overall === null ? null : Math.max(0, Math.min(100, overall));
  return (
    <section
      className="flex flex-col rounded-card border border-border-subtle bg-surface-1 p-4"
      aria-label="Performance score"
      data-testid="score-radar"
    >
      <h3 className="text-sm font-semibold text-text-primary">Performance score</h3>
      {scored.length >= 3 ? (
        <div className="h-56 w-full" aria-hidden="true" data-testid="score-radar-chart">
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart data={scored} outerRadius="70%">
              <PolarGrid stroke="var(--color-border-subtle)" />
              <PolarAngleAxis dataKey="axis" tick={{ fill: "var(--color-text-secondary)", fontSize: 11 }} />
              <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
              <Radar
                dataKey="score"
                stroke="var(--color-accent)"
                strokeWidth={2}
                fill="var(--color-accent)"
                fillOpacity={0.25}
                dot={{ r: 3, fill: "var(--color-accent)" }}
                isAnimationActive={false}
              />
              <Tooltip content={<RadarTooltip />} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="my-8 text-center text-sm text-text-muted">
          Not enough closed trades to score yet.
        </p>
      )}
      {unscored.length && scored.length >= 3 ? (
        <ul className="mb-2 space-y-0.5 text-[11px] text-text-muted" data-testid="score-radar-unscored">
          {unscored.map((row) => (
            <li key={row.axis}>
              <span className="font-medium text-text-secondary">{row.axis}: n/a</span> · {row.raw}{" "}
              (not plotted)
            </li>
          ))}
        </ul>
      ) : null}
      <table className="sr-only">
        <caption>Score components (0–100)</caption>
        <tbody>
          {rows.map((datum) => (
            <tr key={datum.axis}>
              <th scope="row">{datum.axis}</th>
              <td>{datum.score === null ? UNAVAILABLE : datum.score.toFixed(0)}</td>
              <td>{datum.raw}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-auto flex items-end gap-4 border-t border-border-subtle pt-3">
        <div>
          <p className="text-[11px] text-text-muted">Overall score</p>
          <p className="font-data text-2xl font-semibold text-text-primary" data-testid="score-overall">
            {overall === null ? UNAVAILABLE : overall.toFixed(1)}
          </p>
        </div>
        <div className="flex-1 pb-2">
          <div
            className="relative h-2 rounded-full"
            style={{
              background:
                "linear-gradient(90deg, var(--color-danger), var(--color-warning), var(--color-success))",
            }}
          >
            {marker !== null ? (
              <span
                className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface-1 bg-text-primary"
                style={{ left: `${marker}%` }}
                aria-hidden="true"
              />
            ) : null}
          </div>
          <div className="mt-1 flex justify-between font-data text-[10px] text-text-muted">
            <span>0</span>
            <span>50</span>
            <span>100</span>
          </div>
        </div>
      </div>
    </section>
  );
}
