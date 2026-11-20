"use client";

import { Search, UserRound } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { KillSwitchButton } from "@/components/KillSwitchButton";
import { resolveExecutionDisplay } from "@/components/layout/status-strip-state";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { useSafetyPosture } from "@/contexts/AppContext";
import { useAuth } from "@/contexts/AuthContext";

export function TopBar({
  onOpenCommandMenu,
}: {
  onOpenCommandMenu?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [position, setPosition] = useState({ top: 0, right: 8 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const { executionMode, realTradingEnabled, postureKnown } = useSafetyPosture();
  const { user, organization, logout } = useAuth();
  const mode = resolveExecutionDisplay(
    executionMode,
    realTradingEnabled,
    postureKnown,
  );

  const place = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition({
      top: rect.bottom + 8,
      right: Math.max(8, window.innerWidth - rect.right),
    });
  }, []);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!open) return;
    place();
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, place]);

  const closeAnd = (action: () => void) => {
    setOpen(false);
    action();
  };

  const panel =
    mounted && open
      ? createPortal(
          <div
            ref={panelRef}
            id={panelId}
            role="dialog"
            aria-label="Account"
            style={{ top: position.top, right: position.right }}
            className="fixed z-[55] flex w-[min(22rem,calc(100vw-1rem))] max-h-[min(32rem,calc(100dvh-5rem))] flex-col gap-3 overflow-y-auto rounded-card border border-border bg-surface-1 p-4 text-text-primary shadow-elevation2"
          >
            <Button
              type="button"
              aria-label="Search pages and destinations"
              variant="outline"
              className="h-11 w-full justify-start"
              onClick={() => closeAnd(() => onOpenCommandMenu?.())}
            >
              <Search className="h-4 w-4" aria-hidden="true" />
              Search pages
            </Button>
            <div className="space-y-2 border-t border-border-subtle pt-3">
              <div>
                <p className="break-words text-sm">{user?.email ?? "Signed in"}</p>
                <p className="text-caption text-text-muted">
                  {organization?.name ?? "Workspace"}
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                className="h-11 w-full justify-start"
                onClick={() => void logout()}
              >
                Log out
              </Button>
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <header className="sticky top-0 z-40 border-b border-border-subtle bg-surface-0/90 backdrop-blur-md">
      <div className="flex h-14 min-w-0 items-center gap-2 overflow-x-auto px-gutter lg:px-gutter-lg">
        <p
          data-testid="topbar-page-identity"
          className="min-w-0 flex-1 truncate text-base font-semibold"
        >
          AlphaTrade
        </p>
        <div
          className="flex shrink-0 items-center gap-2"
          role="status"
          aria-label="Execution status"
          aria-live="polite"
          data-testid="execution-safety-strip"
        >
          <StatusBadge
            label={mode.label}
            tone={mode.tone}
            className="px-3 py-1 text-[length:var(--text-label)] font-semibold shadow-[0_0_0_1px_rgba(255,255,255,0.06)]"
          />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <KillSwitchButton compact />
          <div data-testid="header-account-menu" data-open={open ? "true" : "false"}>
            <Button
              ref={triggerRef}
              type="button"
              variant="outline"
              size="sm"
              className="h-11 w-11 rounded-full px-0"
              aria-label="Account menu"
              aria-expanded={open}
              aria-haspopup="dialog"
              aria-controls={panelId}
              onClick={() => {
                place();
                setOpen((value) => !value);
              }}
            >
              <UserRound className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        </div>
      </div>
      {panel}
    </header>
  );
}
