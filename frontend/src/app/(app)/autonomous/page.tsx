"use client";

import { BotSettings } from "@/components/autonomous/BotSettings";
import { RiskProfileSections } from "@/components/autonomous/RiskProfileSections";
import { StrategyStatsPanel } from "@/components/autonomous/StrategyStatsPanel";
import { KillSwitchButton } from "@/components/KillSwitchButton";
import { killSwitchBlockNotice, PortfolioHubChrome } from "@/components/portfolio";
import { SessionScoped } from "@/components/settings/SessionScoped";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusTile } from "@/components/ui/status-tile";
import { describeSafetyPosture } from "@/components/workflows";
import { useAppContext, useSafetyPosture } from "@/contexts/AppContext";
import { formatDateTime } from "@/lib/format";

const JUMP_LINKS = [
  ["#kill-switch", "Kill switch"],
  ["#limits", "Limits"],
  ["#grade-risk", "Grade risk"],
  ["#leverage", "Leverage"],
  ["#strategies", "Per strategy"],
] as const;

/**
 * Autonomous Trading: the paper bot, every limit it trades under, grade risk,
 * leverage and per-strategy results on one page. `/risk` and `/risk/autonomous`
 * redirect here (next.config).
 */
export default function AutonomousPage() {
  return (
    <SessionScoped>
      <AutonomousWorkspace />
    </SessionScoped>
  );
}

function KillSwitchStatusCard() {
  const { killSwitchStatus, killSwitchError } = useAppContext();
  const active = killSwitchStatus?.active ?? null;
  const state = killSwitchError ? "Unavailable" : active == null ? "Loading" : active ? "Active" : "Off";
  return (
    <Card id="kill-switch" data-testid="kill-switch-status" className="scroll-mt-4">
      <CardHeader>
        <CardTitle>Kill switch</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <StatusTile name="Kill switch" tone={active ? "down" : active === false ? "ok" : "unknown"} state={state} />
          <StatusTile
            name="Execution"
            tone={killSwitchStatus?.execution_blocked ? "down" : killSwitchStatus ? "ok" : "unknown"}
            state={killSwitchStatus ? (killSwitchStatus.execution_blocked ? "Blocked" : "Allowed") : "Unknown"}
          />
          {active && killSwitchStatus?.activated_at ? (
            <StatusTile name="Since" tone="unknown" state={formatDateTime(killSwitchStatus.activated_at)} />
          ) : null}
        </div>
        {active && killSwitchStatus?.reason ? (
          <p className="text-text-secondary">Reason: {killSwitchStatus.reason}</p>
        ) : null}
        <p className="text-xs text-text-muted">Use the kill switch button at the top of this page to change it.</p>
      </CardContent>
    </Card>
  );
}

function AutonomousWorkspace() {
  const { executionMode, realTradingEnabled } = useSafetyPosture();
  const { killSwitchStatus, killSwitchError, loading } = useAppContext();
  const block = killSwitchBlockNotice({ killSwitchStatus, killSwitchError, killSwitchLoading: loading });
  return (
    <PortfolioHubChrome
      title="Autonomous Trading"
      posture={describeSafetyPosture(executionMode, realTradingEnabled)}
      riskBlocked={block.blocked}
      riskBlockReason={block.reason}
      actions={<KillSwitchButton compact />}
      testId="autonomous-page"
    >
      <p className="text-sm text-text-muted" data-testid="autonomous-settings-ownership">
        Configuration only. Changes apply to the next autonomous decision. Real-money execution stays blocked.
      </p>
      <nav aria-label="Autonomous Trading sections" className="flex flex-wrap gap-3 text-sm">
        {JUMP_LINKS.map(([href, label]) => (
          <a key={href} href={href} className="text-accent underline-offset-2 hover:underline">
            {label}
          </a>
        ))}
      </nav>
      <KillSwitchStatusCard />
      <BotSettings />
      <RiskProfileSections />
      <StrategyStatsPanel />
      <p className="text-xs text-zinc-500" data-testid="autonomous-real-trading-footer">
        Real trading remains disabled. Risk settings affect paper discipline snapshots, dashboard guidance, and agent
        explanations — never broker execution.
      </p>
    </PortfolioHubChrome>
  );
}
