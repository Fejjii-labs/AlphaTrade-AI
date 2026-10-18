import type { ReactNode } from "react";
import { AlertTriangle, Ban, Clock, Inbox, Info, ShieldOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import { IconTile } from "@/components/ui/icon-tile";
import { SkeletonCard, StatStripSkeleton, TableSkeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const bodyText = "text-[length:var(--text-body)] leading-[1.55]";

export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: string;
  /** One next action. Existing callers omit it. */
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-card border border-dashed border-border bg-surface-1 px-6 py-10 text-center",
        className,
      )}
      data-testid="empty-state"
    >
      <IconTile icon={Inbox} tone="muted" />
      <h3 className="text-section mt-3">{title}</h3>
      {description ? (
        <p className={cn("mt-1 max-w-md text-text-muted", bodyText)}>{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function LoadingState({
  label = "Loading…",
  className,
  preset = "card",
}: {
  label?: string;
  className?: string;
  preset?: "card" | "stat-strip" | "table";
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn("space-y-3", className)}
      data-testid="loading-state"
    >
      <p className="text-[length:var(--text-caption)] leading-[1.4] text-text-muted">{label}</p>
      {preset === "stat-strip" ? <StatStripSkeleton /> : null}
      {preset === "table" ? <TableSkeleton /> : null}
      {preset === "card" ? <SkeletonCard /> : null}
    </div>
  );
}

export function SuccessState({ message, className }: { message: string; className?: string }) {
  return (
    <div
      className={cn(
        "rounded-card border border-success-border bg-success-muted px-6 py-4 text-center text-[length:var(--text-body)] leading-[1.55] text-success",
        className,
      )}
    >
      {message}
    </div>
  );
}

export function ErrorState({
  id,
  message,
  onRetry,
  className,
}: {
  /** Set when a form field must reference this error via `aria-describedby`. */
  id?: string;
  message: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      id={id}
      role="alert"
      data-testid="error-state"
      className={cn(
        "rounded-card border border-danger-border bg-danger-muted px-6 py-8 text-center",
        className,
      )}
    >
      <AlertTriangle className="mx-auto mb-2 h-5 w-5 text-danger" aria-hidden="true" />
      <p className="text-[length:var(--text-body)] leading-[1.55] text-danger">{message}</p>
      {onRetry ? (
        <Button type="button" variant="outline" size="sm" className="mt-3" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

export function StaleState({
  message = "Data may be delayed or stale.",
  ageLabel,
  className,
}: {
  message?: string;
  ageLabel?: string;
  className?: string;
}) {
  return (
    <div
      role="status"
      data-testid="stale-state"
      className={cn(
        "rounded-card border border-stale-border bg-stale-muted px-4 py-3 text-[length:var(--text-body)] leading-[1.55] text-stale",
        className,
      )}
    >
      <div className="flex items-start gap-2">
        <Clock className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <div>
          <p className="font-medium">Stale data{ageLabel ? ` · ${ageLabel}` : ""}</p>
          <p className="mt-1 text-text-secondary">{message}</p>
        </div>
      </div>
    </div>
  );
}

/**
 * Neutral advisory for analytical / coverage / methodology limitations.
 * Not a freshness warning — do not use for stale/delayed market data.
 */
export function LimitationsState({
  title = "Limitations",
  message = "Some metrics carry explicit limitations — treat values as incomplete.",
  items,
  className,
}: {
  title?: string;
  message?: string;
  items?: string[];
  className?: string;
}) {
  return (
    <div
      role="status"
      data-testid="limitations-state"
      className={cn(
        "rounded-card border border-border bg-surface-1 px-4 py-3 text-[length:var(--text-body)] leading-[1.55] text-text-primary",
        className,
      )}
    >
      <div className="flex items-start gap-2">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-info" aria-hidden="true" />
        <div className="min-w-0 space-y-1">
          <p className="font-medium text-text-primary">{title}</p>
          <p className="text-text-secondary">{message}</p>
          {items && items.length > 0 ? (
            <ul className="mt-2 space-y-1 text-[length:var(--text-caption)] leading-[1.4] text-text-secondary">
              {items.map((item) => (
                <li key={item}>• {item}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function BlockedState({
  message,
  className,
}: {
  message: string;
  className?: string;
}) {
  return (
    <div
      role="alert"
      data-testid="blocked-state"
      className={cn(
        "rounded-card border border-blocked-border bg-blocked-muted px-4 py-3 text-[length:var(--text-body)] leading-[1.55]",
        className,
      )}
    >
      <div className="flex items-start gap-2">
        <Ban className="mt-0.5 h-4 w-4 shrink-0 text-blocked" aria-hidden="true" />
        <div>
          <p className="font-semibold uppercase tracking-wide text-blocked">Blocked</p>
          <p className="mt-1 text-text-primary">{message}</p>
        </div>
      </div>
    </div>
  );
}

export function UnavailableState({
  message = "This surface is temporarily unavailable.",
  onRetry,
  className,
}: {
  message?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="status"
      data-testid="unavailable-state"
      className={cn(
        "rounded-card border border-border bg-surface-1 px-6 py-8 text-center",
        className,
      )}
    >
      <ShieldOff className="mx-auto mb-2 h-5 w-5 text-text-muted" aria-hidden="true" />
      <p className="text-[length:var(--text-body)] leading-[1.55] text-text-secondary">{message}</p>
      {onRetry ? (
        <Button type="button" variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}
