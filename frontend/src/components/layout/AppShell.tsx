"use client";

import { useState } from "react";

import { CommandMenu } from "@/components/layout/CommandMenu";
import { DesktopSidebar } from "@/components/layout/DesktopSidebar";
import { MobileBottomNavigation } from "@/components/layout/MobileBottomNavigation";
import { SecondaryNavigation } from "@/components/layout/SecondaryNavigation";
import { SkipLink } from "@/components/layout/SkipLink";
import { TopBar } from "@/components/layout/TopBar";
import { ShellFreshnessProvider } from "@/contexts/ShellFreshnessContext";

/**
 * Shell chrome only — the shared AppProvider (health/providers/kill-switch)
 * lives in the app layout above AuthProvider (single /health source, FP2-105).
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const [commandOpen, setCommandOpen] = useState(false);

  return (
    <ShellFreshnessProvider>
      <div
        data-testid="app-shell"
        className="min-h-screen overflow-x-hidden bg-background text-text-primary"
      >
        <SkipLink />
        <div className="flex min-h-screen min-w-0">
          <DesktopSidebar onOpenCommandMenu={() => setCommandOpen(true)} />
          <div className="flex min-h-screen min-w-0 flex-1 flex-col pb-[calc(4.5rem+env(safe-area-inset-bottom,0px))] lg:pb-0">
            <TopBar onOpenCommandMenu={() => setCommandOpen(true)} />
            <SecondaryNavigation />
            <main
              id="main"
              tabIndex={-1}
              className="mx-auto w-full min-w-0 max-w-content flex-1 space-y-section overflow-x-hidden px-gutter py-5 focus:outline-none lg:px-gutter-lg"
            >
              {children}
            </main>
          </div>
        </div>
        <MobileBottomNavigation />
        <CommandMenu open={commandOpen} onOpenChange={setCommandOpen} />
      </div>
    </ShellFreshnessProvider>
  );
}
