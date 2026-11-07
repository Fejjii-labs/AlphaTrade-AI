"use client";

import { Badge } from "@/components/ui/badge";
import type { PaperPortfolioSafetyBanner as SafetyBanner } from "@/lib/api/types";
import { humanizeToken } from "@/lib/format";

/**
 * Backend schema default for PaperPortfolioSafetyBanner.disclaimer
 * (`backend/src/app/schemas/performance.py`). The field is a free-form string,
 * so non-standard / dynamic disclaimers must stay visible.
 */
export const STANDARD_PAPER_PORTFOLIO_DISCLAIMER =
  "Paper-only simulated portfolio. Not investment advice. Does not indicate readiness for real money.";

function normalizeDisclaimer(value: string | null | undefined): string {
  return (value ?? "").replace(/\s+/g, " ").trim();
}

function normalizeExecutionMode(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

/** True when the payload carries only the standard static disclaimer copy. */
export function isStandardPaperPortfolioDisclaimer(
  disclaimer: string | null | undefined,
): boolean {
  return normalizeDisclaimer(disclaimer) === STANDARD_PAPER_PORTFOLIO_DISCLAIMER;
}

/**
 * Suppress only when verified paper posture is fully confirmed and no dynamic
 * disclaimer/warning would be hidden (FP2-123).
 */
export function shouldSuppressPaperPortfolioSafetyBanner(safety: SafetyBanner): boolean {
  return (
    normalizeExecutionMode(safety.execution_mode) === "paper" &&
    safety.paper_only === true &&
    safety.real_trading_enabled === false &&
    isStandardPaperPortfolioDisclaimer(safety.disclaimer)
  );
}

export function executionModeHonestyLabel(executionMode: string | null | undefined): string {
  const mode = normalizeExecutionMode(executionMode);
  if (!mode) return "Execution mode unverified/unknown";
  if (mode === "paper") return "Paper execution mode";
  if (mode === "live" || mode === "trade") return "Live execution mode";
  return `${humanizeToken(mode)} execution mode`;
}

function executionModeBadgeVariant(
  executionMode: string | null | undefined,
): "success" | "danger" | "warning" | "muted" {
  const mode = normalizeExecutionMode(executionMode);
  if (mode === "paper") return "success";
  if (mode === "live" || mode === "trade") return "danger";
  if (!mode) return "warning";
  return "warning";
}

/**
 * Portfolio safety banner for non-redundant honesty only (FP2-123).
 *
 * When the payload confirms verified paper posture with only the standard
 * static disclaimer, the top bar mode pill plus page-header PaperModeIndicator
 * already communicate that posture — suppress this third surface.
 * Unverified, live, conflicting, or dynamic-disclaimer states always remain visible.
 */
export function PaperPortfolioSafetyBanner({ safety }: { safety: SafetyBanner }) {
  if (shouldSuppressPaperPortfolioSafetyBanner(safety)) {
    return null;
  }

  return (
    <section
      className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4"
      data-testid="paper-portfolio-safety-banner"
      role="alert"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant={executionModeBadgeVariant(safety.execution_mode)}
          data-testid="paper-portfolio-execution-mode"
        >
          {executionModeHonestyLabel(safety.execution_mode)}
        </Badge>
        <Badge
          variant={safety.paper_only ? "success" : "danger"}
          data-testid="paper-portfolio-paper-only"
        >
          {safety.paper_only ? "Paper-only confirmed" : "Paper-only not confirmed"}
        </Badge>
        <Badge
          variant={safety.real_trading_enabled ? "danger" : "success"}
          data-testid="paper-portfolio-real-trading"
        >
          Real trading {safety.real_trading_enabled ? "enabled" : "disabled"}
        </Badge>
      </div>
      <p className="mt-3 text-sm text-zinc-300" data-testid="paper-portfolio-disclaimer">
        {safety.disclaimer?.trim()
          ? safety.disclaimer
          : "Safety disclaimer unavailable from portfolio payload."}
      </p>
      <p className="mt-2 text-xs text-zinc-500" data-testid="paper-portfolio-no-real-money">
        Not investment advice. Does not indicate readiness for real money.
      </p>
    </section>
  );
}
