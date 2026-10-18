"use client";

import Link from "next/link";

import { cn } from "@/lib/utils";
import {
  firstActionableStep,
  type WorkflowStep,
  type WorkflowStepStatus,
} from "@/lib/workflow-steps";

const STATUS_DOT: Record<WorkflowStepStatus, string> = {
  complete: "border-border-strong bg-surface-2 text-text-secondary",
  current: "border-accent-border bg-accent-muted text-accent",
  blocked: "border-warning-border bg-warning-muted text-warning",
  upcoming: "border-border-subtle bg-surface-0 text-text-muted",
};

const STATUS_LABEL: Record<WorkflowStepStatus, string> = {
  complete: "Complete",
  current: "Next",
  blocked: "Blocked",
  upcoming: "Upcoming",
};

export function WorkflowStepper({ steps }: { steps: WorkflowStep[] }) {
  const focus = firstActionableStep(steps);

  return (
    <section
      className="rounded-card border border-border-subtle bg-surface-1 p-4"
      data-testid="workflow-stepper"
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-section">Workflow</h2>
        <span className="text-[length:var(--text-caption)] leading-[1.4] text-text-muted">
          Idea → Structure → Backtest → Paper → Lessons → Improve
        </span>
      </div>
      <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {steps.map((step, index) => (
          <li key={step.key} data-testid={`workflow-step-${step.key}`}>
            <Link
              href={step.href}
              className="flex items-start gap-3 rounded-control border border-border-subtle p-3 transition hover:border-border"
            >
              <span
                className={cn(
                  "flex h-6 w-6 flex-none items-center justify-center rounded-full border text-xs font-semibold",
                  STATUS_DOT[step.status],
                )}
              >
                {index + 1}
              </span>
              <span className="min-w-0">
                <span className="flex items-center gap-2">
                  <span className="text-[length:var(--text-body)] font-medium leading-[1.55] text-text-primary">
                    {step.label}
                  </span>
                  <span
                    className="text-[0.6875rem] uppercase tracking-wide text-text-muted"
                    data-testid={`workflow-step-status-${step.key}`}
                  >
                    {STATUS_LABEL[step.status]}
                  </span>
                </span>
                <span className="mt-0.5 block text-[length:var(--text-caption)] leading-[1.4] text-text-secondary">
                  {step.nextAction}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ol>
      {focus ? (
        <p
          className="mt-3 text-[length:var(--text-caption)] leading-[1.4] text-text-secondary"
          data-testid="workflow-next-action"
        >
          What to do next: <span className="text-text-primary">{focus.nextAction}</span>
        </p>
      ) : null}
    </section>
  );
}
