"use client";

import { useCallback } from "react";

import { useAsyncData } from "@/hooks/useAsyncData";
import { formatStrategyLabel } from "@/lib/format";
import { api } from "@/lib/api";
import type { BrainStrategy } from "@/lib/api/brain-types";

const FAMILY: Record<string, string> = {
  "operational_nested_continuation/v1": "Nested",
  "swing_failure_pattern/v1": "SFP",
};

function side(direction: string): string {
  return direction.toLowerCase() === "short" ? "Short" : "Long";
}

/** "Nested · BTCUSDT · Short · 15m" */
export function strategyLine(strategy: BrainStrategy): string {
  return [
    FAMILY[strategy.spec.kind] ?? formatStrategyLabel(strategy.name),
    strategy.spec.symbol,
    side(strategy.spec.direction),
    strategy.spec.trigger_timeframe,
  ].join(" · ");
}

/** The strategies the bot may trade (enabled first), each with its coin, side and timeframe. */
export function BotStrategiesList() {
  const loader = useCallback(() => api.strategyBrain.overview(), []);
  const { data, loading, error } = useAsyncData(loader, []);
  if (loading) return <p className="text-sm text-text-muted">Loading strategies…</p>;
  if (error || !data) return <p className="text-sm text-text-muted">Strategies unavailable.</p>;
  const rows = [...data.strategies].sort((a, b) => Number(b.enabled) - Number(a.enabled) || strategyLine(a).localeCompare(strategyLine(b)));
  if (!rows.length) return <p className="text-sm text-text-muted">No strategies yet.</p>;
  return (
    <ul className="divide-y divide-border-subtle text-sm" data-testid="bot-strategies">
      {rows.map((s) => (
        <li key={s.version_id} className="flex min-h-11 items-center justify-between gap-3">
          <span className={s.enabled ? "" : "text-text-muted"}>{strategyLine(s)}</span>
          <span className={s.enabled ? "text-positive" : "text-text-muted"}>{s.enabled ? "On" : "Off"}</span>
        </li>
      ))}
    </ul>
  );
}
