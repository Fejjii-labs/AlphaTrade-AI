import { cn } from "@/lib/utils";

export type StatusTone = "ok" | "warn" | "down" | "unknown";

export interface StatusTileProps {
  name: string;
  tone: StatusTone;
  /** One word: OK, Degraded, Down, Fallback, Unknown. */
  state: string;
  /** Grey tag such as "Simulated". */
  tag?: string | null;
  className?: string;
}

const DOT: Record<StatusTone, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  down: "bg-danger",
  unknown: "bg-text-muted",
};

const BORDER: Record<StatusTone, string> = {
  ok: "border-success-border",
  warn: "border-warning-border",
  down: "border-danger-border",
  unknown: "border-border-subtle",
};

/** Name, coloured dot and a one-word state. The state is text, not colour alone. */
export function StatusTile({ name, tone, state, tag, className }: StatusTileProps) {
  return (
    <div
      data-testid="status-tile"
      data-tone={tone}
      className={cn(
        "flex min-h-14 flex-col justify-center gap-1 rounded-card border bg-surface-1 px-3 py-2",
        BORDER[tone],
        className,
      )}
    >
      <span className="truncate text-xs text-text-muted">{name}</span>
      <span className="flex items-center gap-2 text-sm font-medium text-text-primary">
        <span aria-hidden="true" className={cn("size-2 shrink-0 rounded-full", DOT[tone])} />
        {state}
        {tag ? (
          <span className="rounded-pill bg-surface-2 px-2 py-0.5 text-[0.6875rem] font-normal text-text-muted">
            {tag}
          </span>
        ) : null}
      </span>
    </div>
  );
}
