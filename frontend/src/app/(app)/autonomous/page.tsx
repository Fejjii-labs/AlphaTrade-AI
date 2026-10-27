"use client";

import { AccountLimitsCard } from "@/components/autonomous/AccountLimitsCard";
import { BotSettings } from "@/components/autonomous/BotSettings";
import { KillSwitchButton } from "@/components/KillSwitchButton";
import { killSwitchBlockNotice, PortfolioHubChrome } from "@/components/portfolio";
import { SessionScoped } from "@/components/settings/SessionScoped";
import { describeSafetyPosture } from "@/components/workflows";
import { useAppContext, useSafetyPosture } from "@/contexts/AppContext";

/** The paper bot, its limits and the account limits on one page (UI overhaul B3a). */
export default function AutonomousPage() {
  return (
    <SessionScoped>
      <AutonomousWorkspace />
    </SessionScoped>
  );
}

function AutonomousWorkspace() {
  const { executionMode, realTradingEnabled } = useSafetyPosture();
  const { killSwitchStatus, killSwitchError, loading } = useAppContext();
  const block = killSwitchBlockNotice({ killSwitchStatus, killSwitchError, killSwitchLoading: loading });
  return (
    <PortfolioHubChrome
      title="Autonomous"
      posture={describeSafetyPosture(executionMode, realTradingEnabled)}
      riskBlocked={block.blocked}
      riskBlockReason={block.reason}
      actions={<KillSwitchButton compact />}
      testId="autonomous-page"
    >
      <p className="text-sm text-text-muted" data-testid="autonomous-settings-ownership">
        Configuration only. Changes apply to the next autonomous decision. Real-money execution stays blocked.
      </p>
      <BotSettings between={<AccountLimitsCard />} />
      <p className="text-xs text-zinc-500" data-testid="autonomous-real-trading-footer">
        Real trading remains disabled. Risk settings affect paper discipline snapshots, dashboard guidance, and agent
        explanations — never broker execution.
      </p>
    </PortfolioHubChrome>
  );
}
