import { UNAVAILABLE } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface GaugeProps {
  /** 0–100. Null, undefined, and non-finite values render an unavailable dash. */
  value: number | null | undefined;
  label: string;
  className?: string;
}

const RADIUS = 46;
const CIRCUMFERENCE = Math.PI * RADIUS;

function finiteValue(value: number | null | undefined): number | null {
  if (value == null || !Number.isFinite(value)) return null;
  return Math.min(100, Math.max(0, value));
}

/** Semicircle 0–100 meter. The number and label are always present. */
export function Gauge({ value, label, className }: GaugeProps) {
  const shown = finiteValue(value);
  const unavailable = shown == null;
  const offset = unavailable ? CIRCUMFERENCE : CIRCUMFERENCE * (1 - shown / 100);
  const display = unavailable ? UNAVAILABLE : String(Math.round(shown));

  return (
    <div
      className={cn("inline-flex w-36 flex-col items-center", className)}
      data-testid="gauge"
      data-unavailable={unavailable ? "true" : "false"}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={unavailable ? undefined : shown}
      aria-valuetext={unavailable ? "Not available yet" : `${shown} out of 100`}
    >
      <svg viewBox="0 0 120 70" className="w-full" aria-hidden="true">
        <path
          d="M 14 58 A 46 46 0 0 1 106 58"
          fill="none"
          stroke="var(--color-border-strong)"
          strokeWidth="8"
          strokeLinecap="round"
        />
        <path
          d="M 14 58 A 46 46 0 0 1 106 58"
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={`${CIRCUMFERENCE} ${CIRCUMFERENCE}`}
          strokeDashoffset={offset}
        />
        <text
          x="60"
          y="54"
          textAnchor="middle"
          className="font-data"
          fill={unavailable ? "var(--color-text-muted)" : "var(--color-text-primary)"}
          fontSize="18"
          fontFamily="var(--font-mono), ui-monospace, monospace"
        >
          {display}
        </text>
      </svg>
      <p className="text-[0.8125rem] font-medium text-text-secondary">{label}</p>
    </div>
  );
}
