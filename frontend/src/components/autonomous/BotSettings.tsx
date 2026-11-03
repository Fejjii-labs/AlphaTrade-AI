"use client";

import { useCallback, useEffect, useState } from "react";

import { BotActivityPanel } from "@/components/autonomous/BotActivityPanel";
import { TradingLimitsCard } from "@/components/autonomous/TradingLimitsCard";
import { ErrorState, LoadingState } from "@/components/states";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusTile, type StatusTone } from "@/components/ui/status-tile";
import { useAuth } from "@/contexts/AuthContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { AutonomousTradingSettingsView, UserRiskSettings } from "@/lib/api/types";
import { humanizeToken } from "@/lib/format";
import { describedWorkerHealth } from "@/lib/worker-runtime";

function roleLabel(role: string | null | undefined): string {
  if (!role) return "unknown";
  return role.charAt(0).toUpperCase() + role.slice(1);
}

function onOff(value: boolean | null | undefined): { tone: StatusTone; state: string } {
  if (value == null) return { tone: "unknown", state: "Not reported" };
  return value ? { tone: "ok", state: "On" } : { tone: "unknown", state: "Off" };
}

function workerTone(state: string): StatusTone {
  if (state === "Fresh") return "ok";
  if (state === "Stale") return "warn";
  return "unknown";
}

/**
 * The paper bot and every limit it trades under: owner switch and status
 * tiles, one limits form (account + bot, side by side), and what the bot did.
 */
export function BotSettings() {
  const { membershipRole } = useAuth();
  const loader = useCallback(
    () => Promise.all([api.autonomous.settings(), api.risk.settings()]),
    [],
  );
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const [view, setView] = useState<AutonomousTradingSettingsView | null>(null);
  const [account, setAccount] = useState<UserRiskSettings | null>(null);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    if (data) {
      setView(data[0]);
      setAccount(data[1]);
    }
  }, [data]);

  if (loading) return <LoadingState label="Loading autonomous trading settings…" />;
  if (error) return <ErrorState message={error} onRetry={() => void reload()} />;
  if (!view || !account)
    return <ErrorState message="Autonomous trading settings unavailable." onRetry={() => void reload()} />;

  async function handlePaperSwitch(enabled: boolean) {
    setSwitching(true);
    setSwitchError(null);
    try {
      setView(await api.autonomous.updateSettings({ internal_paper_enabled: enabled }));
    } catch (err) {
      setSwitchError(err instanceof Error ? err.message : "Unable to change the paper switch.");
    } finally {
      setSwitching(false);
    }
  }

  async function handleAccountSaved(settings: UserRiskSettings) {
    setAccount(settings);
    // The bot's effective cap is min(bot, account); re-read it after an account change.
    try {
      setView(await api.autonomous.settings());
    } catch {
      // Keep the last view; the next load corrects the effective cap.
    }
  }

  const owner = membershipRole === "owner";
  // PATCH /autonomous/settings is owner-only; say why instead of letting it 403.
  const ownerReason = owner ? null : `Owner only (your role: ${roleLabel(membershipRole)})`;
  const worker = describedWorkerHealth(view.worker_runtime_health, view.worker_flags_stale);
  return (
    <>
      <Card data-testid="autonomous-paper-switch">
        <CardHeader>
          <CardTitle>Paper bot</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <label className="flex min-h-11 items-center gap-2 font-medium">
            <input
              type="checkbox"
              data-testid="autonomous-paper-switch-input"
              checked={view.internal_paper_enabled === true}
              disabled={!owner || switching}
              onChange={(event) => void handlePaperSwitch(event.target.checked)}
            />
            <span>Autonomous internal paper</span>
          </label>
          {ownerReason ? (
            <p className="text-text-muted" data-testid="autonomous-switch-owner-reason">
              {ownerReason}
            </p>
          ) : null}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="autonomous-paper-switch-status">
            <StatusTile name="Environment ceiling" {...onOff(view.internal_paper_env_ceiling)} />
            <StatusTile name="Effective" {...onOff(view.internal_paper_effective ?? null)} />
            <StatusTile name="Worker" tone={workerTone(worker)} state={worker} />
            <StatusTile name="Mode" tone="unknown" state={view.autonomy_mode ? humanizeToken(view.autonomy_mode) : "Not reported"} />
          </div>
          <p className="text-xs text-text-muted" data-testid="autonomous-paper-switch-note">
            Turning it off stops new entries and does not close open positions. It cannot enable real money.
          </p>
          {switchError ? (
            <p className="text-sm text-danger" role="alert">
              {switchError}
            </p>
          ) : null}
        </CardContent>
      </Card>
      <TradingLimitsCard
        bot={view}
        account={account}
        owner={owner}
        ownerReason={ownerReason}
        onBotSaved={setView}
        onAccountSaved={(settings) => void handleAccountSaved(settings)}
      />
      <BotActivityPanel view={view} />
    </>
  );
}
