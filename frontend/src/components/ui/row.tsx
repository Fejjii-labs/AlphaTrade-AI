import * as React from "react";

import { cn } from "@/lib/utils";

/** Badges inside a Row sit on the text baseline instead of centring against taller neighbours. */
export const rowBadgeClass = "self-baseline leading-none";

// Column templates by how many trailing parts (meta, action) the row has.
const columns = [
  "sm:grid-cols-[minmax(8rem,14rem)_minmax(0,1fr)]",
  "sm:grid-cols-[minmax(8rem,14rem)_minmax(0,1fr)_auto]",
  "sm:grid-cols-[minmax(8rem,14rem)_minmax(0,1fr)_auto_auto]",
];

export interface RowProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "children"> {
  label: React.ReactNode;
  value?: React.ReactNode;
  meta?: React.ReactNode;
  action?: React.ReactNode;
  /** Render label and value as `<dt>`/`<dd>` for use inside a `<dl>`. */
  definition?: boolean;
}

/**
 * One readout or list line: label · value · meta · action on a shared text baseline.
 * Stacks on narrow screens and becomes one grid row from `sm` up.
 */
export function Row({
  label,
  value,
  meta,
  action,
  definition = false,
  className,
  ...props
}: RowProps) {
  const Label = definition ? "dt" : "div";
  const Value = definition ? "dd" : "div";
  const hasMeta = meta !== undefined && meta !== null;
  const hasAction = action !== undefined && action !== null;
  return (
    <div
      className={cn(
        "grid min-h-10 grid-cols-1 items-baseline gap-x-3 gap-y-1 py-1.5",
        columns[Number(hasMeta) + Number(hasAction)],
        className,
      )}
      {...props}
    >
      <Label data-row-part="label" className="min-w-0 text-sm text-text-muted">
        {label}
      </Label>
      <Value data-row-part="value" className="min-w-0 break-words text-sm text-text-primary">
        {value}
      </Value>
      {hasMeta ? (
        <div
          data-row-part="meta"
          className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1 text-xs text-text-muted"
        >
          {meta}
        </div>
      ) : null}
      {hasAction ? (
        <div data-row-part="action" className="flex items-baseline gap-2 sm:justify-self-end">
          {action}
        </div>
      ) : null}
    </div>
  );
}
