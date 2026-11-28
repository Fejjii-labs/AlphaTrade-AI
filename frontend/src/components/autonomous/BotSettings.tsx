"use client";

import { useCallback, useEffect, useState } from "react";

import { BotActivityPanel } from "@/components/autonomous/BotActivityPanel";
import { BotStrategyGrid } from "@/components/autonomous/BotStrategyGrid";
import { DemoLeverageControl } from "@/components/autonomous/DemoLeverageControl";
import { ScannerStatusLine } from "@/components/autonomous/ScannerStatusLine";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { StatusTile, type StatusTone } from "@/components/ui/status-tile";
import { WatcherWatchlistSection } from "@/components/WatcherWatchlistSection";
import { useAppContext } from "@/contexts/AppContext";
import { useAuth } from "@/contexts/AuthContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { AutonomousTradingSettingsView } from "@/lib/api/types";

type Tile = { tone: StatusTone; state: string };

/** On / Off / Paused, from the worker's resolved mode and the kill switch. */
export function botState(view: AutonomousTradingSettingsView, paused: boolean): Tile {
  if (paused) return { tone: "down", state: "Paused" };
  if (view.autonomy_mode === "blofin_demo") return { tone: "ok", state: "On" };
  if (view.autonomy_mode === "internal_paper") return { tone: "ok", state: "On (paper)" };
  if (!view.autonomy_mode) return { tone: "unknown", state: "Not reported" };
  return { tone: "unknown", state: "Off" };
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium text-text-secondary">{title}</h3>
      {children}
    </div>
  );
}

/** The scanner line, and the coin watchlist editor behind one button. */
function Scanner() {
  const [editing, setEditing] = useState(false);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ScannerStatusLine />
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-expanded={editing}
          data-testid="edit-coins"
          onClick={() => setEditing((open) => !open)}
        >
          {editing ? "Close coins" : "Edit coins"}
        </Button>
      </div>
      {editing ? <WatcherWatchlistSection /> : null}
    </div>
  );
}

/**
 * Autonomous trading: bot state, scanner, leverage, the strategies it trades
 * and its last decisions. Risk caps still apply on the server; they are not shown here.
 */
export function BotSettings() {
  const { membershipRole } = useAuth();
  const { killSwitchStatus } = useAppContext();
  const loader = useCallback(() => api.autonomous.settings(), []);
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const [view, setView] = useState<AutonomousTradingSettingsView | null>(null);
  useEffect(() => {
    if (data) setView(data);
  }, [data]);

  if (loading) return <LoadingState label="Loading bot…" />;
  if (error || !view) return <ErrorState message="Bot settings unavailable." onRetry={() => void reload()} />;
  const paused = killSwitchStatus?.active === true;
  return (
    <div className="space-y-5" data-testid="bot-settings">
      <div className="grid grid-cols-2 gap-2">
        <StatusTile name="Bot" {...botState(view, paused)} />
        <StatusTile name="Venue" tone="unknown" state="BloFin demo" />
      </div>
      <Block title="Scanner">
        <Scanner />
      </Block>
      <DemoLeverageControl view={view} owner={membershipRole === "owner"} onSaved={setView} />
      <Block title="Strategies">
        <BotStrategyGrid scope={view.worker_trading_scope} />
      </Block>
      <Block title="Last decisions">
        <BotActivityPanel />
      </Block>
    </div>
  );
}
