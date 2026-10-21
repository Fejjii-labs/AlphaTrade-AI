"use client";

import { Line, LineChart } from "recharts";

import { UNAVAILABLE } from "@/lib/format";
import { cn } from "@/lib/utils";

export type SparklineTone = "positive" | "negative" | "neutral";

const toneClass: Record<SparklineTone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-accent",
};

export interface SparklineProps {
  values: number[];
  /** Spoken description, for example "BTC 7 day trend". */
  label: string;
  /** Neutral (blue) unless the series is explicitly profit or loss. */
  tone?: SparklineTone;
  width?: number;
  height?: number;
  className?: string;
}

/** Axis-free trend line for table cells and cards. */
export function Sparkline({
  values,
  label,
  tone = "neutral",
  width = 96,
  height = 28,
  className,
}: SparklineProps) {
  const finite = values.filter((value) => Number.isFinite(value));
  if (finite.length === 0) {
    return (
      <span
        className={cn("font-data text-[length:var(--text-caption)] leading-[1.4] text-text-muted", className)}
        data-testid="sparkline"
        data-unavailable="true"
        role="img"
        aria-label={`${label}: not available yet`}
        title="Not available yet"
      >
        {UNAVAILABLE}
      </span>
    );
  }

  const data = finite.map((value, index) => ({ index, value }));
  return (
    <span
      className={cn("inline-flex", toneClass[tone], className)}
      data-testid="sparkline"
      data-tone={tone}
      role="img"
      aria-label={label}
    >
      <LineChart
        width={width}
        height={height}
        data={data}
        margin={{ top: 2, right: 2, bottom: 2, left: 2 }}
      >
        <Line
          type="monotone"
          dataKey="value"
          stroke="currentColor"
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </span>
  );
}
