"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Command, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useEffect, useState } from "react";

import {
  isPrimaryDestinationActive,
  PRIMARY_DESTINATIONS,
} from "@/components/layout/navigation-config";
import { IconButton } from "@/components/ui/icon-button";
import { Tooltip } from "@/components/ui/tooltip";
import { appConfig } from "@/lib/config";
import { cn } from "@/lib/utils";

const SIDEBAR_COLLAPSED_KEY = "alphatrade.sidebar.collapsed";

type DesktopSidebarProps = {
  onOpenCommandMenu?: () => void;
};

export function DesktopSidebar({ onOpenCommandMenu }: DesktopSidebarProps) {
  const pathname = usePathname();
  // Safe SSR/hydration default: expanded. Persist after mount.
  const [collapsed, setCollapsed] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1");
    } catch {
      setCollapsed(false);
    }
    setHydrated(true);
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((current) => {
      const next = !current;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        /* localStorage may be unavailable */
      }
      return next;
    });
  };

  return (
    <aside
      data-testid="desktop-sidebar"
      data-collapsed={collapsed ? "true" : "false"}
      data-hydrated={hydrated ? "true" : "false"}
      className={cn(
        "hidden shrink-0 flex-col border-r border-border-subtle bg-surface-0 lg:flex",
        collapsed ? "w-16" : "w-60",
      )}
      aria-label="Primary"
    >
      <div
        className={cn(
          "flex items-start gap-2 border-b border-border-subtle",
          collapsed ? "flex-col px-2 py-3" : "px-5 py-5",
        )}
      >
        <div className={cn("min-w-0 flex-1", collapsed && "sr-only")}>
          <p className="text-eyebrow">
            {appConfig.appName}
          </p>
          <p className="mt-1 text-sm font-semibold text-text-primary">
            Trading Copilot
          </p>
        </div>
        <IconButton
          label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          variant="ghost"
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
          data-testid="sidebar-collapse-toggle"
        >
          {collapsed ? (
            <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
          ) : (
            <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
          )}
        </IconButton>
      </div>

      <nav
        aria-label="Primary destinations"
        className="flex-1 space-y-1 overflow-y-auto p-2"
      >
        {PRIMARY_DESTINATIONS.map((destination) => {
          const { href, label, icon: Icon, ariaLabel } = destination;
          const active = isPrimaryDestinationActive(pathname, destination);
          const link = (
            <Link
              href={href}
              aria-label={ariaLabel}
              aria-current={active ? "page" : undefined}
              data-destination={destination.id}
              data-primary-workspace={
                destination.id === "agent" ? "true" : undefined
              }
              title={collapsed ? label : undefined}
              className={cn(
                "flex items-center rounded-control border text-sm font-medium transition-colors",
                "border-transparent",
                "min-h-11 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                collapsed ? "justify-center px-2 py-2.5" : "gap-3 px-3 py-2.5",
                active
                  ? "border-accent-border bg-accent-muted text-accent"
                  : "text-text-secondary hover:bg-surface-1 hover:text-text-primary",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className={cn("truncate", collapsed && "sr-only")}>
                {label}
              </span>
            </Link>
          );
          return (
            <div key={destination.id}>
              {collapsed ? <Tooltip content={label}>{link}</Tooltip> : link}
            </div>
          );
        })}
      </nav>

      <div className="space-y-2 border-t border-border-subtle p-2">
        {collapsed ? (
          <Tooltip content="Command menu">
            <button
              type="button"
              onClick={onOpenCommandMenu}
              className={cn(
                "flex w-full items-center justify-center rounded-control px-2 py-2 text-sm text-text-muted",
                "min-h-11 hover:bg-surface-1 hover:text-text-secondary",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
              )}
              aria-label="Open command menu"
              data-testid="sidebar-command-menu"
            >
              <Command className="h-4 w-4" aria-hidden="true" />
            </button>
          </Tooltip>
        ) : (
          <button
            type="button"
            onClick={onOpenCommandMenu}
            className={cn(
              "flex w-full items-center gap-2 rounded-control px-3 py-2 text-sm text-text-muted",
              "min-h-11 hover:bg-surface-1 hover:text-text-secondary",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
            )}
            aria-label="Open command menu"
            data-testid="sidebar-command-menu"
          >
            <Command className="h-4 w-4" aria-hidden="true" />
            <span className="flex-1 text-left">Command menu</span>
            <kbd className="rounded border border-border-subtle px-1.5 py-0.5 text-caption text-text-muted">
              ⌘K
            </kbd>
          </button>
        )}
      </div>
    </aside>
  );
}

export { SIDEBAR_COLLAPSED_KEY };
