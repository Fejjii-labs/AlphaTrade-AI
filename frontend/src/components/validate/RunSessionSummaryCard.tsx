import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { setupConditionLabel } from "@/lib/alert-display";
import type { PaperValidationRunSessionItem } from "@/lib/api/types";
import {
  elapsedLabel,
  formatTimestamp,
  runSessionNextAction,
} from "@/components/validate/validationDisplay";
import {
  candidateDetailHref,
  relatedObjectAvailable,
  runPlanDetailHref,
  runSessionDetailHref,
} from "@/components/validate/validationLinks";

type RunSessionSummaryCardProps = {
  session: PaperValidationRunSessionItem;
  observationCount?: number | null;
  outcomeStatus?: string | null;
  paperConfirmed?: boolean;
};

export function RunSessionSummaryCard({
  session,
  observationCount = null,
  outcomeStatus = null,
  paperConfirmed = false,
}: RunSessionSummaryCardProps) {
  const started = formatTimestamp(session.started_at);
  const elapsed = elapsedLabel(session.started_at, session.ended_at);

  return (
    <article
      className="rounded-control border border-border-subtle bg-surface-0/40 px-4 py-3 space-y-3"
      data-testid={`paper-run-session-${session.session_id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="info">{setupConditionLabel(session.condition ?? "unknown")}</Badge>
            <span className="text-sm font-medium text-text-primary">
              {session.symbol ?? "—"} · {session.timeframe ?? "—"}
            </span>
            <Badge variant="muted">{session.direction ?? "—"}</Badge>
          </div>
          <p className="text-caption text-text-muted">
            Started {started ?? "unavailable"} · Elapsed {elapsed}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Badge variant="muted" data-testid={`paper-run-session-status-${session.session_id}`}>
            {session.session_status}
          </Badge>
          <Badge variant={paperConfirmed ? "paper" : "warning"}>
            {paperConfirmed ? "Paper only" : "Paper mode not confirmed"}
          </Badge>
        </div>
      </div>

      <dl className="grid gap-2 text-caption text-text-muted sm:grid-cols-2">
        <div>
          <dt className="font-medium text-text-secondary">Associated plan</dt>
          <dd className="mt-0.5">
            {relatedObjectAvailable(session.run_plan_id) ? (
              <Link
                href={runPlanDetailHref(session.run_plan_id)}
                className="text-text-primary underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
              >
                Open plan
              </Link>
            ) : (
              "unavailable"
            )}
          </dd>
        </div>
        <div>
          <dt className="font-medium text-text-secondary">Associated candidate</dt>
          <dd className="mt-0.5">
            {relatedObjectAvailable(session.candidate_id) ? (
              <Link
                href={candidateDetailHref(session.candidate_id)}
                className="text-text-primary underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
              >
                Open candidate
              </Link>
            ) : (
              "unavailable"
            )}
          </dd>
        </div>
        <div>
          <dt className="font-medium text-text-secondary">Observations collected</dt>
          <dd className="mt-0.5 text-text-primary" data-testid={`paper-run-session-obs-${session.session_id}`}>
            {observationCount == null ? "open detail to load" : String(observationCount)}
          </dd>
        </div>
        <div>
          <dt className="font-medium text-text-secondary">Outcome status</dt>
          <dd className="mt-0.5 text-text-primary">
            {outcomeStatus ?? (session.session_status === "completed" ? "see detail" : "not recorded")}
          </dd>
        </div>
      </dl>

      <p
        className="text-caption text-text-muted"
        data-testid={`paper-run-session-next-${session.session_id}`}
      >
        Next action: <span className="text-text-primary">{runSessionNextAction(session)}</span>
      </p>

      <Link
        href={runSessionDetailHref(session.session_id)}
        className="inline-flex min-h-11 items-center text-sm text-text-primary underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
      >
        View run session detail
      </Link>
    </article>
  );
}
