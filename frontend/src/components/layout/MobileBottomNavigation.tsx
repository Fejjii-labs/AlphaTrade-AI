"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Ellipsis } from "lucide-react";
import { useEffect, useState } from "react";

import {
  getPrimaryDestination,
  isPrimaryDestinationActive,
  MOBILE_BOTTOM_DESTINATION_IDS,
  MOBILE_MORE_DESTINATION_IDS,
} from "@/components/layout/navigation-config";
import { Sheet } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

const tabClass = (active: boolean) =>
  cn(
    "flex min-h-14 min-w-0 flex-col items-center justify-center gap-1 border-t-2 px-0 text-[10px] leading-tight min-[360px]:px-0.5 min-[360px]:text-[11px] sm:text-xs",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
    active ? "border-accent-solid bg-accent-muted text-accent" : "border-transparent text-text-secondary",
  );

/** Phone tab bar: Dashboard · Trades · Strategies · Agent · More (Autonomous, Knowledge, Settings). */
export function MobileBottomNavigation() {
  const pathname = usePathname();
  const [moreOpen, setMoreOpen] = useState(false);
  useEffect(() => setMoreOpen(false), [pathname]);
  const more = MOBILE_MORE_DESTINATION_IDS.map(getPrimaryDestination);
  const moreActive = more.some((destination) => isPrimaryDestinationActive(pathname, destination));

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
              className={tabClass(active)}
            >
              <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
              <span className="text-center">{label}</span>
            </Link>
          );
        })}
        <button
          type="button"
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          data-active={moreActive ? "true" : undefined}
          data-testid="mobile-more-button"
          onClick={() => setMoreOpen(true)}
          className={tabClass(moreActive)}
        >
          <Ellipsis className="h-5 w-5 shrink-0" aria-hidden="true" />
          <span className="text-center">More</span>
        </button>
      </div>
      <Sheet open={moreOpen} onClose={() => setMoreOpen(false)} title="More" side="bottom">
        <ul className="grid gap-1 pb-2" data-testid="mobile-more-sheet">
          {more.map((destination) => {
            const { id, href, label, icon: Icon } = destination;
            const active = isPrimaryDestinationActive(pathname, destination);
            return (
              <li key={id}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  data-destination={id}
                  onClick={() => setMoreOpen(false)}
                  className={cn(
                    "flex min-h-12 items-center gap-3 rounded-control px-3 text-sm",
                    active ? "bg-accent-muted text-accent" : "text-text-primary hover:bg-surface-2",
                  )}
                >
                  <Icon className="h-5 w-5" aria-hidden="true" />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </Sheet>
    </nav>
  );
}
