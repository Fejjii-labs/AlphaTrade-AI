"use client";

import { useCallback, useEffect, useState } from "react";

import { BotActivityPanel } from "@/components/autonomous/BotActivityPanel";
import { BotStrategiesList } from "@/components/autonomous/BotStrategiesList";
import { DemoLeverageControl } from "@/components/autonomous/DemoLeverageControl";
import { ErrorState, LoadingState } from "@/components/states";
import { StatusTile, type StatusTone } from "@/components/ui/status-tile";
import { useAppContext } from "@/contexts/AppContext";
import { useAuth } from "@/contexts/AuthContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { AutonomousSettingsBounds, AutonomousTradingSettingsView } from "@/lib/api/types";
import { describedWorkerHealth } from "@/lib/worker-runtime";

type Tile = { tone: StatusTone; state: string };

/** On / Off / Paused, from the worker's resolved mode and the kill switch. */
export function botState(view: AutonomousTradingSettingsView, paused: boolean): Tile {
  if (paused) return { tone: "down", state: "Paused" };
  if (view.autonomy_mode === "blofin_demo") return { tone: "ok", state: "On" };
  if (view.autonomy_mode === "internal_paper") return { tone: "ok", state: "On (paper)" };
  if (!view.autonomy_mode) return { tone: "unknown", state: "Not reported" };
  return { tone: "unknown", state: "Off" };
}

function workerTile(view: AutonomousTradingSettingsView): Tile {
  const state = describedWorkerHealth(view.worker_runtime_health, view.worker_flags_stale);
  return { tone: state === "Fresh" ? "ok" : state === "Stale" ? "warn" : "unknown", state };
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium text-text-secondary">{title}</h3>
      {children}
    </div>
  );
}

/**
 * Autonomous trading: bot state, leverage, the strategies it trades and its
 * last decisions. Risk caps still apply on the server; they are not shown here.
 */
export function BotSettings() {
  const { membershipRole } = useAuth();
  const { killSwitchStatus } = useAppContext();
  const loader = useCallback(
    () =>
      Promise.all([
        api.autonomous.settings(),
        api.autonomous.bounds().catch((): AutonomousSettingsBounds | null => null),
      ]),
    [],
  );
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const [view, setView] = useState<AutonomousTradingSettingsView | null>(null);
  useEffect(() => {
    if (data) setView(data[0]);
  }, [data]);

  if (loading) return <LoadingState label="Loading bot…" />;
  if (error || !view) return <ErrorState message="Bot settings unavailable." onRetry={() => void reload()} />;
  const paused = killSwitchStatus?.active === true;
  return (
    <div className="space-y-5" data-testid="bot-settings">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <StatusTile name="Bot" {...botState(view, paused)} />
        <StatusTile name="Venue" tone="unknown" state="BloFin demo" />
        <StatusTile name="Worker" {...workerTile(view)} />
      </div>
      <DemoLeverageControl view={view} bounds={data?.[1] ?? null} owner={membershipRole === "owner"} onSaved={setView} />
      <Block title="Strategies">
        <BotStrategiesList />
      </Block>
      <Block title="Last decisions">
        <BotActivityPanel />
      </Block>
    </div>
  );
}
