"use client";

import { Search, RefreshCw } from "lucide-react";
import { useEffect, useRef } from "react";
import { FreshnessPill } from "@/components/ui/freshness-pill";
import { KillSwitchButton } from "@/components/KillSwitchButton";
import { resolveExecutionDisplay } from "@/components/layout/status-strip-state";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { useAppContext, useSafetyPosture } from "@/contexts/AppContext";
import { useAuth } from "@/contexts/AuthContext";
import { useShellFreshness } from "@/contexts/ShellFreshnessContext";

export function TopBar({
  onOpenCommandMenu,
}: {
  onOpenCommandMenu?: () => void;
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (menu.current?.open && !menu.current.contains(event.target as Node))
        menu.current.open = false;
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);
  const {
    refreshStatus,
    loading,
    providers,
    killSwitchStatus,
    killSwitchError,
  } = useAppContext();
  const { executionMode, realTradingEnabled, postureKnown } =
    useSafetyPosture();
  const { user, organization, logout } = useAuth();
  const { freshness } = useShellFreshness();
  const mode = resolveExecutionDisplay(
    executionMode,
    realTradingEnabled,
    postureKnown,
  );
  const known = killSwitchStatus != null && !killSwitchError;
  const paused = known && killSwitchStatus.execution_blocked;
  return (
    <header className="sticky top-0 z-30 border-b border-border-subtle bg-surface-0/95 backdrop-blur">
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-2 px-gutter py-2 sm:flex lg:px-gutter-lg">
        <p
          data-testid="topbar-page-identity"
          className="order-1 min-w-0 flex-1 truncate text-base font-semibold"
        >
          AlphaTrade
        </p>
        <div
          className="order-3 flex min-w-0 flex-wrap items-center gap-2 sm:order-2"
          role="status"
          aria-label="Execution status"
          aria-live="polite"
        >
          <StatusBadge label={mode.label} tone={mode.tone} />
          <StatusBadge
            label={
              !known ? "Pause unknown" : paused ? "Paused" : "No global pause"
            }
            tone={!known ? "warn" : paused ? "blocked" : "muted"}
          />
        </div>
        <div className="order-4 justify-self-end sm:order-3">
          <KillSwitchButton compact />
        </div>
        <details
          ref={menu}
          className="relative order-2 justify-self-end sm:order-4"
          data-testid="header-status-menu"
          onKeyDown={(event) => {
            if (event.key === "Escape" && menu.current?.open) {
              menu.current.open = false;
              menu.current.querySelector("summary")?.focus();
            }
          }}
        >
          <summary className="min-h-11 cursor-pointer rounded-control border border-border-subtle px-3 py-2.5 text-sm">
            Status
          </summary>
          <div className="absolute right-0 z-40 mt-2 max-h-[calc(100dvh-5rem)] w-[min(22rem,calc(100vw-2rem))] space-y-3 overflow-y-auto rounded-card border border-border-subtle bg-surface-0 p-4 shadow-lg">
            <Button
              aria-label="Search pages and destinations"
              variant="outline"
              className="w-full justify-start"
              onClick={() => {
                if (menu.current) menu.current.open = false;
                onOpenCommandMenu?.();
              }}
            >
              <Search className="h-4 w-4" aria-hidden="true" />
              Search pages
            </Button>
            <p className="text-sm">
              {realTradingEnabled == null
                ? "Real trading unknown"
                : realTradingEnabled
                  ? "Real trading ON"
                  : "Real trading OFF"}
            </p>
            <div data-testid="topbar-freshness">
              <FreshnessPill
                state={freshness.state ?? "unavailable"}
                ageLabel={freshness.ageLabel}
              />
            </div>
            <p className="text-caption text-text-muted">
              {providers
                ? `${providers.providers.filter((p) => p.is_mock).length} simulated providers`
                : "Providers unknown"}
            </p>
            <p className="text-caption text-text-muted">
              Strategy and account gates still apply.
            </p>
            {killSwitchError && (
              <p role="alert" className="text-sm text-danger">
                Pause status could not be refreshed.
              </p>
            )}
            <Button
              variant="secondary"
              size="sm"
              disabled={loading}
              onClick={() => void refreshStatus()}
            >
              <RefreshCw className="h-4 w-4" />
              Refresh status
            </Button>
            <div className="border-t border-border-subtle pt-3">
              <p className="break-words text-sm">
                {user?.email ?? "Signed in"}
              </p>
              <p className="text-caption text-text-muted">
                {organization?.name ?? "Workspace"}
              </p>
              <Button
                className="mt-2"
                variant="outline"
                size="sm"
                onClick={() => void logout()}
              >
                Log out
              </Button>
            </div>
          </div>
        </details>
      </div>
    </header>
  );
}
