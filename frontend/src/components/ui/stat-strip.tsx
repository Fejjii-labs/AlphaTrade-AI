import * as React from "react";

import { DataNumber } from "@/components/ui/data-number";
import { Tooltip } from "@/components/ui/tooltip";
import { UNAVAILABLE } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface StatProps {
  label: string;
  /** Display value. Null or undefined renders the unavailable dash. */
  value?: React.ReactNode;
  unavailable?: boolean;
  /** Signed delta. Zero is shown without an arrow. Null is unavailable. */
  delta?: number | null;
  deltaLabel?: React.ReactNode;
  testId?: string;
  className?: string;
}

function UnavailableValue() {
  return (
    <Tooltip content="Not available yet">
      <span
        className="font-data text-[length:var(--text-display)] font-bold leading-[1.15] text-text-muted"
        tabIndex={0}
        title="Not available yet"
      >
        {UNAVAILABLE}
      </span>
    </Tooltip>
  );
}

/** One stat-strip cell: muted label, mono value, optional signed delta. */
export function Stat({
  label,
  value,
  unavailable = false,
  delta,
  deltaLabel,
  testId = "stat",
  className,
}: StatProps) {
  const missing = unavailable || value == null;
  const deltaTone =
    delta == null || delta === 0 ? "muted" : delta > 0 ? "positive" : "negative";

  return (
    <div className={cn("min-w-0 flex-1 px-4 py-3", className)} data-testid={testId}>
      <dt className="text-[0.8125rem] font-medium uppercase tracking-wide text-text-muted">
        {label}
      </dt>
      <dd className="mt-1">
        {missing ? (
          <UnavailableValue />
        ) : (
          <span className="font-data block text-[length:var(--text-display)] font-bold leading-[1.15] tracking-[-0.015em] text-text-primary">
            {value}
          </span>
        )}
        {delta !== undefined ? (
          <div className="mt-1">
            {delta == null ? (
              <span
                className="font-data text-[length:var(--text-caption)] leading-[1.4] text-text-muted"
                title="Not available yet"
              >
                {UNAVAILABLE}
              </span>
            ) : (
              <DataNumber
                value={deltaLabel ?? String(delta)}
                tone={deltaTone}
                signed={delta !== 0}
                numeric={delta}
              />
            )}
          </div>
        ) : null}
      </dd>
    </div>
  );
}

export interface StatStripProps {
  children: React.ReactNode;
  label?: string;
  className?: string;
}

/** Horizontal summary row. Two columns on small screens, one row from `lg`. */
export function StatStrip({ children, label = "Summary", className }: StatStripProps) {
  return (
    <dl
      aria-label={label}
      data-testid="stat-strip"
      className={cn(
        "grid grid-cols-2 overflow-hidden rounded-card border border-border-subtle bg-surface-1 sm:grid-cols-3 lg:flex lg:divide-x lg:divide-border-subtle",
        className,
      )}
    >
      {children}
    </dl>
  );
}
