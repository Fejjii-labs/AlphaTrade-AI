import type { ReactNode } from "react";

import {
  WorkflowFreshnessAdapter,
  type FreshnessSourceInput,
} from "@/components/workflows";
import type { SafetyPostureDisplay } from "@/components/workflows/safetyPostureDisplay";
import { PageHeader } from "@/components/ui/page-header";
import { PaperModeIndicator } from "@/components/ui/paper-mode-indicator";
import { RiskBlock } from "@/components/ui/risk-block";

type PortfolioHubChromeProps = {
  title: string;
  description?: string;
  /** Right-aligned header actions. */
  actions?: ReactNode;
  posture: SafetyPostureDisplay;
  providerMode?: string;
  /** Omitted by hub siblings that aggregate no sources of their own. */
  freshnessSources?: FreshnessSourceInput[];
  attentionSummary?: string | null;
  attentionTone?: "ok" | "warn" | "blocked" | "muted";
  riskBlocked?: boolean;
  riskBlockReason?: string | null;
  testId?: string;
  children: ReactNode;
};

const NO_FRESHNESS_SOURCES: FreshnessSourceInput[] = [];

/**
 * Shared chrome for the Portfolio & Risk hub and its sibling pages
 * (`/positions`, `/risk`): one page header with verified paper posture, the
 * safety-conflict alert, the attention banner, and the risk BLOCK panel.
 */
export function PortfolioHubChrome({
  title,
  description,
  actions,
  posture,
  providerMode,
  freshnessSources = NO_FRESHNESS_SOURCES,
  attentionSummary = null,
  attentionTone = "muted",
  riskBlocked = false,
  riskBlockReason = null,
  testId = "portfolio-command-centre",
  children,
}: PortfolioHubChromeProps) {
  void providerMode;

  return (
    <div className="space-y-section pb-24 md:pb-section" data-testid={testId}>
      <WorkflowFreshnessAdapter sources={freshnessSources} />

      <PageHeader
        title={title}
        description={description}
        actions={actions}
        meta={<PaperModeIndicator active={posture.paperConfirmed} />}
      />

      {posture.conflictMessage ? (
        <p className="text-sm text-danger" role="alert" data-testid="portfolio-safety-conflict">
          {posture.conflictMessage}
        </p>
      ) : null}

      {attentionSummary ? (
        <div
          role="status"
          data-testid="portfolio-risk-attention"
          className={
            attentionTone === "blocked"
              ? "rounded-control border border-danger-border bg-danger-muted/40 px-3 py-3 text-sm text-danger"
              : attentionTone === "warn"
                ? "rounded-control border border-warning-border bg-warning-muted/40 px-3 py-3 text-sm text-warning"
                : attentionTone === "ok"
                  ? "rounded-control border border-success-border bg-success-muted/40 px-3 py-3 text-sm text-success"
                  : "rounded-control border border-border-subtle bg-surface-1 px-3 py-3 text-sm text-text-secondary"
          }
        >
          <p className="font-medium text-text-primary">Primary risk condition</p>
          <p className="mt-1" data-testid="portfolio-risk-attention-summary">
            {attentionSummary}
          </p>
        </div>
      ) : null}

      {riskBlocked ? (
        <div data-testid="portfolio-risk-block">
          <RiskBlock
            reason={
              riskBlockReason ??
              "Risk engine BLOCK is final. There is no override control on Portfolio."
            }
            ruleReference="risk_engine.BLOCK"
          />
        </div>
      ) : null}

      {children}
    </div>
  );
}
