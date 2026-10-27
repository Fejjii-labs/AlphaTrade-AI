import * as React from "react";

import { cn } from "@/lib/utils";

export type TechnicalDetail = { label: string; value: string | null | undefined };

export interface TechnicalDetailsProps {
  items: TechnicalDetail[];
  /** Raw provenance text (for example recorded evidence) shown under the list. */
  raw?: string | null;
  className?: string;
}

/**
 * Collapsed home for raw provenance (venue codes, record and order IDs).
 * Main views stay readable; tests treat `data-technical-details` as the only
 * place identifiers may render.
 */
export function TechnicalDetails({ items, raw, className }: TechnicalDetailsProps) {
  const rows = items.filter((item): item is { label: string; value: string } => Boolean(item.value));
  if (rows.length === 0 && !raw) return null;
  return (
    <details
      data-technical-details=""
      className={cn("rounded-control border border-border-subtle p-3 text-xs", className)}
    >
      <summary className="cursor-pointer rounded-control text-text-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2">
        Technical details
      </summary>
      {rows.length > 0 ? (
        <dl className="mt-2 space-y-1">
          {rows.map((row, index) => (
            <div key={`${row.label}-${index}`} className="flex flex-wrap gap-x-2">
              <dt className="text-text-secondary">{row.label}</dt>
              <dd className="min-w-0 break-all font-mono text-text-primary">{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {raw ? (
        <p className="mt-2 whitespace-pre-wrap break-words font-mono [overflow-wrap:anywhere]">{raw}</p>
      ) : null}
    </details>
  );
}
