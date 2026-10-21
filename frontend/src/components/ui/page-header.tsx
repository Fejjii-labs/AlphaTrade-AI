import * as React from "react";

import { Eyebrow } from "@/components/ui/eyebrow";
import { cn } from "@/lib/utils";

export interface PageHeaderProps {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
  /** Uppercase kicker above the title, for example "Overview". */
  eyebrow?: string;
  /** Optional status row above the title. Kept for existing callers. */
  meta?: React.ReactNode;
  /** Hides eyebrow and description (mobile Agent). */
  compact?: boolean;
}

/** One page title per screen — eyebrow, title, description, actions. */
export function PageHeader({
  title,
  description,
  actions,
  className,
  eyebrow,
  meta,
  compact = false,
}: PageHeaderProps) {
  return (
    <header className={cn("flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between", className)}>
      <div className="min-w-0 space-y-1">
        {eyebrow && !compact ? <Eyebrow>{eyebrow}</Eyebrow> : null}
        {meta ? <div className="flex flex-wrap items-center gap-2">{meta}</div> : null}
        <h1 className={cn("text-heading", compact && "text-section")}>{title}</h1>
        {description && !compact ? (
          <div className="text-body max-w-2xl">{description}</div>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
