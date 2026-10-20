import * as React from "react";

import { Eyebrow } from "@/components/ui/eyebrow";
import { cn } from "@/lib/utils";

export function formatSectionNumber(value: string | number): string {
  if (typeof value === "number" && Number.isInteger(value) && value >= 0) {
    return String(value).padStart(2, "0");
  }
  return String(value);
}

export interface SectionHeaderProps {
  title: string;
  /** Uppercase kicker above the title. */
  eyebrow?: string;
  /** Renders as "01 —" before the title. */
  number?: string | number;
  actions?: React.ReactNode;
  className?: string;
  headingId?: string;
}

/** Numbered section title with optional eyebrow and right-side actions. */
export function SectionHeader({
  title,
  eyebrow,
  number,
  actions,
  className,
  headingId,
}: SectionHeaderProps) {
  return (
    <header
      className={cn("flex flex-col gap-1", className)}
      data-testid="section-header"
    >
      {eyebrow ? <Eyebrow>{eyebrow}</Eyebrow> : null}
      <div className="flex items-start justify-between gap-3">
        <h2 id={headingId} className="text-section min-w-0">
          {number != null ? (
            <>
              <Eyebrow as="span" className="mr-2">
                {formatSectionNumber(number)} —
              </Eyebrow>{" "}
            </>
          ) : null}
          {title}
        </h2>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}
