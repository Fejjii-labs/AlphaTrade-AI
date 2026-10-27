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
      <BotSettings between={<AccountLimitsCard />} />
    </PortfolioHubChrome>
  );
}
