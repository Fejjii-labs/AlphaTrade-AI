import Link from "next/link";

import { cn } from "@/lib/utils";

export type LinkTab = { id: string; label: string; href: string };

export interface LinkTabsProps {
  items: readonly LinkTab[];
  activeId: string;
  "aria-label": string;
  className?: string;
}

/**
 * Tabs that are links, so each tab has its own URL (`?tab=`) and can be
 * deep-linked or shared. The current tab carries `aria-current="page"`.
 */
export function LinkTabs({ items, activeId, className, ...props }: LinkTabsProps) {
  return (
    <nav
      aria-label={props["aria-label"]}
      data-testid="link-tabs"
      className={cn("flex gap-1 rounded-card border border-border-subtle bg-surface-1 p-1", className)}
    >
      {items.map((tab) => {
        const active = tab.id === activeId;
        return (
          <Link
            key={tab.id}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "min-h-10 flex-1 rounded-control px-3 py-2 text-center text-[length:var(--text-label)] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
              active
                ? "border border-accent-border bg-accent-muted text-accent"
                : "text-text-secondary hover:bg-surface-2",
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
