import { isPaperModeConfirmed } from "@/components/ui/paper-mode-indicator";
import type { KillSwitchStatus } from "@/lib/api/types";

export type StatusTone = "paper" | "warn" | "blocked" | "success" | "muted" | "info";

export type ExecutionDisplay = {
  label: string;
  tone: StatusTone;
  /** True only when paper + real trading disabled. */
  paperConfirmed: boolean;
  /** Spoken form of `label` for the status strip's live region. */
  announcement: string;
};

/**
 * Resolve execution badge copy/tone.
 * Never applies paper label or paper styling unless paper is fully confirmed.
 */
export function resolveExecutionDisplay(
  executionMode: string | null | undefined,
  realTradingEnabled: boolean | null | undefined,
  postureKnown: boolean,
): ExecutionDisplay {
  const paperConfirmed = isPaperModeConfirmed(executionMode, realTradingEnabled);
  if (paperConfirmed) {
    return {
      label: "Demo",
      tone: "paper",
      paperConfirmed: true,
      announcement: "execution Demo, verified",
    };
  }

  if (executionMode === "paper" && realTradingEnabled === true) {
    return {
      label: "Safety conflict",
      tone: "blocked",
      paperConfirmed: false,
      announcement: "safety conflict between execution mode and real-trading flag",
    };
  }

  if (!postureKnown || executionMode == null || realTradingEnabled == null) {
    return {
      label: "Execution unverified",
      tone: "warn",
      paperConfirmed: false,
      announcement: "execution unverified",
    };
  }

  return {
    label: String(executionMode).toUpperCase(),
    tone: "warn",
    paperConfirmed: false,
    announcement: `execution ${String(executionMode).toUpperCase()}, not paper`,
  };
}

export type AdviceDisplay = {
  text: string;
  tone: "muted" | "warn" | "blocked";
};

/**
 * Advice notice must match runtime posture truth.
 * "Paper-only research" is allowed only when paper is fully confirmed.
 */
export function resolveAdviceDisplay(
  executionMode: string | null | undefined,
  realTradingEnabled: boolean | null | undefined,
  postureKnown: boolean,
): AdviceDisplay {
  if (isPaperModeConfirmed(executionMode, realTradingEnabled) && postureKnown) {
    return {
      text: "Not financial advice. Paper-only research — simulated results do not guarantee performance.",
      tone: "muted",
    };
  }

  if (!postureKnown || executionMode == null || realTradingEnabled == null) {
    return {
      text: "Trading environment not verified. Not financial advice.",
      tone: "warn",
    };
  }

  if (realTradingEnabled === true) {
    return {
      text: "Not financial advice. Real trading appears enabled — paper-only claims do not apply.",
      tone: "blocked",
    };
  }

  return {
    text: `Not financial advice. Runtime execution mode is ${String(executionMode).toUpperCase()} — not paper-only.`,
    tone: "warn",
  };
}

export type RiskDisplay = {
  label: string;
  /** Passed to RiskBadge; null means unknown (never invent "low"). */
  level: string | null;
  known: boolean;
};

/**
 * Resolve risk badge from kill-switch status truth.
 * Missing/loading/error must not become Risk low.
 */
export function resolveRiskDisplay(input: {
  killSwitchStatus: KillSwitchStatus | null;
  killSwitchError: string | null;
  statusLoading: boolean;
}): RiskDisplay {
  const { killSwitchStatus, killSwitchError, statusLoading } = input;

  if (killSwitchError) {
    return { label: "Risk unknown", level: null, known: false };
  }
  if (statusLoading && killSwitchStatus == null) {
    return { label: "Risk unknown", level: null, known: false };
  }
  if (killSwitchStatus == null) {
    return { label: "Risk unknown", level: null, known: false };
  }
  if (killSwitchStatus.execution_blocked) {
    return { label: "Risk critical", level: "critical", known: true };
  }
  return { label: "Risk low", level: "low", known: true };
}

/**
 * Single sentence announced by the status strip's polite live region.
 *
 * The visible badges convey the same facts; this collapses them into one
 * utterance so assistive technology reports a posture change once, in order of
 * safety importance, instead of reading each badge separately.
 */
export function buildPostureAnnouncement(input: {
  execution: ExecutionDisplay;
  realTradingEnabled: boolean | null | undefined;
  risk: RiskDisplay;
}): string {
  const { execution, realTradingEnabled, risk } = input;
  const realTrading =
    realTradingEnabled === true
      ? "real trading enabled"
      : realTradingEnabled === false
        ? "real trading disabled"
        : "real trading unverified";
  return `Trading posture: ${execution.announcement}; ${realTrading}; ${risk.label.toLowerCase()}.`;
}
