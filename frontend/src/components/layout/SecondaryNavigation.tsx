"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  getDestinationId,
  getSecondaryItems,
  resolveSecondaryActiveHref,
} from "@/components/layout/navigation-config";
import { cn } from "@/lib/utils";

export function SecondaryNavigation() {
  const pathname = usePathname();
  // The parent destination is already present in primary navigation and the page heading.
  if (pathname === "/settings") return null;
  const destinationId = getDestinationId(pathname);
  if (!destinationId) return null;

  const items = getSecondaryItems(destinationId);
  if (items.length === 0) return null;

  const activeHref = resolveSecondaryActiveHref(pathname, items);
  const primaryItems = items.filter((item) => !item.advanced);
  const advancedItems = items.filter((item) => item.advanced);

  return (
    <nav
      aria-label={`${destinationId} secondary`}
      data-testid="secondary-navigation"
      data-destination={destinationId}
      className="border-b border-border-subtle bg-surface-0"
    >
      <div className="flex gap-1 overflow-x-auto px-gutter py-2 lg:px-gutter-lg motion-safe:scroll-smooth">
        {primaryItems.map(({ href, label }) => {
          const active = activeHref === href;
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              data-active={active ? "true" : undefined}
              className={cn(
                "shrink-0 rounded-control px-3 py-2 text-sm font-medium transition-colors",
                "min-h-11 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                active
                  ? "bg-surface-2 text-text-primary"
                  : "text-text-muted hover:bg-surface-1 hover:text-text-secondary",
              )}
            >
              {label}
            </Link>
          );
        })}
        {advancedItems.length > 0 ? (
          <div className="ml-2 flex shrink-0 items-center gap-1 border-l border-border-subtle pl-2">
            <span className="px-1 text-caption uppercase tracking-wide text-text-muted">
              Advanced
            </span>
            {advancedItems.map(({ href, label }) => {
              const active = activeHref === href;
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  data-active={active ? "true" : undefined}
                  className={cn(
                    "shrink-0 rounded-control px-3 py-2 text-sm font-medium transition-colors",
                    "min-h-11 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                    active
                      ? "bg-surface-2 text-text-primary"
                      : "text-text-muted hover:bg-surface-1 hover:text-text-secondary",
                  )}
                >
                  {label}
                </Link>
              );
            })}
          </div>
        ) : null}
      </div>
    </nav>
  );
}
