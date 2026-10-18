"use client";

import * as React from "react";

import { resolveAdviceDisplay, resolveExecutionDisplay } from "@/components/layout/status-strip-state";
import { isPaperModeConfirmed } from "@/components/ui/paper-mode-indicator";
import { useSafetyPosture } from "@/contexts/AppContext";
import { cn } from "@/lib/utils";

export type ModePillKind = "paper" | "demo" | "unverified" | "conflict";
export type ModePillContext = "simulator" | "demo";
export type ModePillPause = boolean | null;

export interface ModePillInput {
  executionMode: string | null | undefined;
  realTradingEnabled: boolean | null | undefined;
  postureKnown: boolean;
  /** Account context. Demo is explicit; omitted means the internal simulator. */
  context?: ModePillContext | null;
}

export interface ModePillResolution {
  kind: ModePillKind;
  label: string;
  detail: string;
}

const REAL_MONEY = "Real-money trading: permanently blocked";

const kindClass: Record<ModePillKind, string> = {
  paper: "border-mode-paper-border bg-mode-paper-muted text-mode-paper",
  demo: "border-mode-demo-border bg-mode-demo-muted text-mode-demo",
  unverified: "border-warning bg-transparent text-warning",
  conflict: "border-transparent bg-danger text-text-inverse",
};

/**
 * Resolve the mode pill.
 *
 * PAPER and DEMO both require `isPaperModeConfirmed` (execution_mode exactly
 * "paper" and real_trading_enabled exactly false). DEMO also needs an explicit
 * BloFin demo context. Unknown posture is UNVERIFIED. Every other known posture
 * is SAFETY CONFLICT. There is no live-money label.
 */
export function resolveModePill(input: ModePillInput): ModePillResolution {
  const execution = resolveExecutionDisplay(
    input.executionMode,
    input.realTradingEnabled,
    input.postureKnown,
  );

  if (!input.postureKnown || input.executionMode == null || input.realTradingEnabled == null) {
    return {
      kind: "unverified",
      label: "UNVERIFIED",
      detail: "Trading posture is not confirmed.",
    };
  }

  if (execution.paperConfirmed && isPaperModeConfirmed(input.executionMode, input.realTradingEnabled)) {
    if (input.context === "demo") {
      return {
        kind: "demo",
        label: "DEMO · BloFin",
        detail: "BloFin demo account. The exchange's fake-money environment.",
      };
    }
    return {
      kind: "paper",
      label: "PAPER · Simulator",
      detail: "Internal paper simulator.",
    };
  }

  return {
    kind: "conflict",
    label: "SAFETY CONFLICT",
    detail: execution.announcement,
  };
}

function pausePresentation(paused: ModePillPause): { state: string; label: string; dot: string } {
  if (paused === true) return { state: "paused", label: "Paused", dot: "bg-danger" };
  if (paused === false) return { state: "running", label: "Running", dot: "bg-positive" };
  return { state: "unknown", label: "Pause unknown", dot: "bg-warning" };
}

export interface ModePillProps {
  executionMode?: string | null;
  realTradingEnabled?: boolean | null;
  postureKnown?: boolean;
  context?: ModePillContext | null;
  paused?: ModePillPause;
  riskLabel?: string | null;
  className?: string;
}

/**
 * Always-visible execution pill. Fail-closed: the default is UNVERIFIED.
 * Built on `isPaperModeConfirmed` and `resolveExecutionDisplay`; not mounted by pages yet.
 */
export function ModePill({
  executionMode = null,
  realTradingEnabled = null,
  postureKnown = false,
  context = null,
  paused = null,
  riskLabel = null,
  className,
}: ModePillProps) {
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLSpanElement>(null);
  const buttonRef = React.useRef<HTMLButtonElement>(null);
  const panelId = React.useId();
  const resolution = resolveModePill({
    executionMode,
    realTradingEnabled,
    postureKnown,
    context,
  });
  const pause = pausePresentation(paused);
  const advice =
    resolution.kind === "demo"
      ? "Not financial advice. BloFin demo uses the exchange's fake-money environment and does not guarantee performance."
      : resolveAdviceDisplay(executionMode, realTradingEnabled, postureKnown).text;

  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <span ref={rootRef} className={cn("relative inline-flex", className)}>
      <button
        ref={buttonRef}
        type="button"
        data-testid="mode-pill"
        data-kind={resolution.kind}
        data-pause={pause.state}
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        aria-label={`${resolution.label}, ${pause.label}. ${REAL_MONEY}.`}
        className={cn(
          "inline-flex items-center gap-2 rounded-pill border px-2.5 py-1 text-[0.6875rem] font-semibold tracking-wide",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
          kindClass[resolution.kind],
        )}
        onClick={() => setOpen((current) => !current)}
      >
        <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", pause.dot)} data-testid="mode-pill-dot" />
        {resolution.label}
      </button>
      {open ? (
        <div
          id={panelId}
          role="dialog"
          aria-label="Trading posture"
          data-testid="mode-pill-popover"
          className="absolute right-0 top-full z-50 mt-2 w-72 rounded-card border border-border bg-surface-raised p-3 text-left shadow-elevation2 normal-case tracking-normal"
        >
          <p className="text-[0.8125rem] font-medium text-text-primary">{resolution.detail}</p>
          <dl className="mt-2 space-y-1 text-[length:var(--text-caption)] text-text-secondary">
            <div>
              <dt className="inline">Execution mode: </dt>
              <dd className="inline font-data text-text-primary">{executionMode ?? "unknown"}</dd>
            </div>
            <div>
              <dt className="sr-only">Real money</dt>
              <dd>{REAL_MONEY}</dd>
            </div>
            <div>
              <dt className="inline">Risk: </dt>
              <dd className="inline text-text-primary">{riskLabel ?? "Risk unknown"}</dd>
            </div>
            <div>
              <dt className="inline">Kill switch: </dt>
              <dd className="inline text-text-primary">{pause.label}</dd>
            </div>
          </dl>
          <p className="mt-2 text-[length:var(--text-caption)] text-text-muted">{advice}</p>
        </div>
      ) : null}
    </span>
  );
}

/** Mode pill fed by backend /health. Still fail-closed until posture is known. */
export function VerifiedModePill(props: Omit<ModePillProps, "executionMode" | "realTradingEnabled" | "postureKnown">) {
  const { executionMode, realTradingEnabled, postureKnown } = useSafetyPosture();
  return (
    <ModePill
      {...props}
      executionMode={executionMode}
      realTradingEnabled={realTradingEnabled}
      postureKnown={postureKnown}
    />
  );
}
