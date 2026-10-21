import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export type IconTileTone =
  | "accent"
  | "paper"
  | "demo"
  | "muted"
  | "highlight"
  | "warning"
  | "danger";

const tones: Record<IconTileTone, string> = {
  accent: "bg-accent-muted text-accent",
  paper: "bg-mode-paper-muted text-mode-paper",
  demo: "bg-mode-demo-muted text-mode-demo",
  muted: "bg-surface-2 text-text-muted",
  highlight:
    "bg-[color:color-mix(in_srgb,var(--color-highlight)_12%,transparent)] text-highlight",
  warning: "bg-warning-muted text-warning",
  danger: "bg-danger-muted text-danger",
};

export interface IconTileProps {
  icon: LucideIcon;
  tone?: IconTileTone;
  /** Accessible name. Omit when the tile is decorative. */
  label?: string;
  className?: string;
}

/** Rounded 8px icon tile for menus, empty states, and tool indexes. */
export function IconTile({ icon: Icon, tone = "accent", label, className }: IconTileProps) {
  return (
    <span
      className={cn(
        "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-control",
        tones[tone],
        className,
      )}
      data-testid="icon-tile"
      data-tone={tone}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <Icon className="h-4 w-4" aria-hidden="true" />
    </span>
  );
}
