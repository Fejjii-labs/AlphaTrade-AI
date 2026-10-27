"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  getPrimaryDestination,
  isPrimaryDestinationActive,
  MOBILE_BOTTOM_DESTINATION_IDS,
} from "@/components/layout/navigation-config";
import { cn } from "@/lib/utils";

export function MobileBottomNavigation() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Primary mobile"
      data-testid="mobile-bottom-navigation"
      className={cn(
        "fixed inset-x-0 bottom-0 z-50 border-t border-border-subtle bg-surface-0/95 backdrop-blur-md",
        "pb-[env(safe-area-inset-bottom,0px)] lg:hidden",
      )}
    >
      <div className="grid grid-cols-5">
        {MOBILE_BOTTOM_DESTINATION_IDS.map((id) => {
          const destination = getPrimaryDestination(id);
          const { href, label, icon: Icon, ariaLabel } = destination;
          const active = isPrimaryDestinationActive(pathname, destination);
          return (
            <Link
              key={id}
              href={href}
              aria-label={ariaLabel}
              aria-current={active ? "page" : undefined}
              data-destination={id}
              data-primary-workspace={id === "agent" ? "true" : undefined}
              className={cn(
                "flex min-h-14 min-w-0 flex-col items-center justify-center gap-1 border-t-2 px-0 text-[10px] leading-tight min-[360px]:px-0.5 min-[360px]:text-[11px] sm:text-xs",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                active
                  ? "border-accent-solid bg-accent-muted text-accent"
                  : "border-transparent text-text-secondary",
              )}
            >
              <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
              <span className="text-center">
                {label}
              </span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
