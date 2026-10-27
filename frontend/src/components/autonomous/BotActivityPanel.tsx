"use client";

import Link from "next/link";
import { useCallback } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Row } from "@/components/ui/row";
import { StatusTile, type StatusTone } from "@/components/ui/status-tile";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { AutonomousTradingSettingsView, BotAction } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";

type Tile = { tone: StatusTone; state: string };

/** Running needs the switch effective and a fresh worker; anything else says why not. */
export function botState(view: AutonomousTradingSettingsView): Tile {
  if (view.worker_runtime_health == null || view.worker_runtime_health === "UNAVAILABLE")
    return { tone: "unknown", state: "Not reported" };
  if (view.worker_flags_stale || view.worker_runtime_health === "STALE")
    return { tone: "warn", state: "Stale" };
  if (!view.internal_paper_effective) return { tone: "unknown", state: "Off" };
  return { tone: "ok", state: "Running" };
}

function todayTile(used: number | null | undefined, cap: number): Tile {
  if (used == null) return { tone: "unknown", state: "Unknown" };
  return { tone: used >= cap ? "warn" : "ok", state: `${used} of ${cap}` };
}

const OUTCOME: Record<BotAction["outcome"], { label: string; tone: StatusTone }> = {
  entered: { label: "Entered", tone: "ok" },
  blocked: { label: "Blocked", tone: "warn" },
  rejected: { label: "Rejected", tone: "down" },
};

function sentence(token: string | null | undefined): string | null {
  if (!token) return null;
  const text = token.replaceAll("_", " ").toLowerCase().trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function describe(action: BotAction): string {
  const market = [action.symbol, sentence(action.direction)].filter(Boolean).join(" ");
  return [market, sentence(action.reason)].filter(Boolean).join(" · ") || "No details recorded";
}

/** What the paper bot is doing: state, today's count against its cap, and its last actions. */
export function BotActivityPanel({ view }: { view: AutonomousTradingSettingsView }) {
  const loader = useCallback(() => api.autonomous.activity(), []);
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const strategy = view.strategy_options.find((s) => s.strategy_id === view.strategy_id)?.name;
  const target = [strategy, view.symbol].filter(Boolean).join(" · ") || "No strategy or coin set";
  const actions = data?.recent_actions ?? [];
  const last = actions[0];
  return (
    <Card data-testid="bot-activity">
      <CardHeader className="flex flex-row items-baseline justify-between gap-2">
        <CardTitle>What the bot is doing</CardTitle>
        <Button variant="outline" size="sm" disabled={loading} onClick={() => void reload()}>
          Refresh
        </Button>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-text-secondary" data-testid="bot-activity-target">{target}</p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <StatusTile name="Bot" {...botState(view)} />
          <StatusTile
            name="Trades today"
            {...(loading
              ? { tone: "unknown" as const, state: "Loading" }
              : todayTile(error ? null : data?.trades_today, view.effective_max_trades_per_day))}
          />
          <StatusTile
            name="Last action"
            tone={last ? OUTCOME[last.outcome].tone : "unknown"}
            state={last ? `${OUTCOME[last.outcome].label} · ${formatDateTime(last.at)}` : loading ? "Loading" : "None yet"}
          />
        </div>
        {error ? (
          <p role="status" className="text-text-muted">
            Bot activity unavailable.
          </p>
        ) : null}
        {actions.length > 0 ? (
          <details>
            <summary className="min-h-11 cursor-pointer text-text-secondary">Last {actions.length} actions</summary>
            <div className="divide-y divide-border-subtle">
              {actions.map((action, index) => (
                <Row
                  key={`${action.at}-${index}`}
                  label={formatDateTime(action.at)}
                  value={`${OUTCOME[action.outcome].label} · ${describe(action)}`}
                  action={
                    action.journal_trade_id ? (
                      <Link className="text-accent" href={`/journal?trade_id=${encodeURIComponent(action.journal_trade_id)}`}>
                        Trade
                      </Link>
                    ) : null
                  }
                />
              ))}
            </div>
          </details>
        ) : null}
      </CardContent>
    </Card>
  );
}
