import Link from "next/link";

import { cn } from "@/lib/utils";

export type SubNavItem = { href: string; label: string };

export interface SubNavProps {
  items: readonly SubNavItem[];
  activeHref: string | null;
  "aria-label": string;
  className?: string;
}

/**
 * Section links for one destination. A vertical rail from `lg` up and a
 * horizontal row of scrolling chips below it. The current link carries
 * `aria-current="page"`.
 */
export function SubNav({ items, activeHref, className, ...props }: SubNavProps) {
  return (
    <nav aria-label={props["aria-label"]} data-testid="sub-nav" className={cn("min-w-0", className)}>
      <ul className="-mx-gutter flex gap-1 overflow-x-auto px-gutter pb-1 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0 lg:pb-0">
        {items.map(({ href, label }) => {
          const active = href === activeHref;
          return (
            <li key={href} className="shrink-0">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex min-h-9 items-center whitespace-nowrap rounded-pill border px-3 text-[0.8125rem] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                  "lg:flex lg:w-full lg:rounded-control lg:border-transparent lg:px-3",
                  active
                    ? "border-accent-border bg-accent-muted text-accent"
                    : "border-border-subtle bg-surface-1 text-text-secondary hover:text-text-primary lg:bg-transparent lg:hover:bg-surface-2",
                )}
              >
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
