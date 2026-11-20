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

export type ScoredDatum = { axis: string; score: number; raw: string };

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

/** The recharts radar, loaded on demand so the chart library stays out of the first load. */
export default function ScoreRadarPlot({ scored, polygon }: { scored: ScoredDatum[]; polygon: string }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
            <RadarChart data={scored} outerRadius="70%">
              <PolarGrid stroke="var(--color-border-subtle)" />
              <PolarAngleAxis dataKey="axis" tick={{ fill: "var(--color-text-secondary)", fontSize: 11 }} />
              <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
              <Radar
                dataKey="score"
                stroke={polygon}
                strokeWidth={2}
                fill={polygon}
                fillOpacity={0.25}
                dot={{ r: 3, fill: polygon }}
                isAnimationActive={false}
              />
              <Tooltip content={<RadarTooltip />} />
            </RadarChart>
          </ResponsiveContainer>
  );
}
