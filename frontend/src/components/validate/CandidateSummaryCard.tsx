import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { setupConditionLabel } from "@/lib/alert-display";
import type { PaperValidationCandidateItem } from "@/lib/api/types";
import type { CandidateRunPlanRelation } from "@/components/validate/candidateRunPlan";
import {
  candidateEvidenceCompleteness,
  candidateNextAction,
  formatConfidence,
  formatTimestamp,
} from "@/components/validate/validationDisplay";
import {
  backtestDetailHref,
  candidateDetailHref,
  draftDetailHref,
  relatedObjectAvailable,
  runPlanDetailHref,
} from "@/components/validate/validationLinks";

type CandidateSummaryCardProps = {
  candidate: PaperValidationCandidateItem;
  runPlanRelation?: CandidateRunPlanRelation;
};

function RunPlanRelationDisplay({
  candidateId,
  relation,
}: {
  candidateId: string;
  relation: CandidateRunPlanRelation;
}) {
  if (relation.kind === "source_unavailable") {
    return (
      <dd data-testid={`paper-candidate-run-plan-${candidateId}`}>
        Run plan: relationship source unavailable
      </dd>
    );
  }
  if (relation.kind === "none") {
    return (
      <dd data-testid={`paper-candidate-run-plan-${candidateId}`}>
        Run plan: no active run plan
      </dd>
    );
  }

  const label =
    relation.kind === "historical"
      ? `historical ${relation.status}`
      : relation.status;

  return (
    <dd data-testid={`paper-candidate-run-plan-${candidateId}`}>
      Run plan:{" "}
      <Link
        href={runPlanDetailHref(relation.planId)}
        className="text-text-primary underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        data-testid={`paper-candidate-run-plan-link-${candidateId}`}
      >
        {label}
      </Link>
    </dd>
  );
}

export function CandidateSummaryCard({
  candidate,
  runPlanRelation = { kind: "none" },
}: CandidateSummaryCardProps) {
  const queued = formatTimestamp(candidate.created_at);

  return (
    <article
      className="rounded-control border border-border-subtle bg-surface-0/40 px-4 py-3 space-y-3"
      data-testid={`paper-candidate-${candidate.candidate_id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="info">
              {setupConditionLabel(candidate.condition ?? "unknown")}
            </Badge>
            <span className="text-sm font-medium text-text-primary">
              {candidate.symbol ?? "—"} · {candidate.timeframe ?? "—"}
            </span>
            <Badge variant="muted">{candidate.direction ?? "—"}</Badge>
          </div>
          <p className="text-caption text-text-muted">
            Queued {queued ?? "unavailable"}
            {candidate.promotion_source ? ` · Source: ${candidate.promotion_source}` : ""}
          </p>
        </div>
        <Badge variant="muted" data-testid={`paper-candidate-status-${candidate.candidate_id}`}>
          {candidate.candidate_status}
        </Badge>
      </div>

      <p className="text-sm text-text-secondary">{candidate.thesis ?? "No thesis provided."}</p>

      <dl className="grid gap-2 text-caption text-text-muted sm:grid-cols-2">
        <div>
          <dt className="sr-only">Confidence</dt>
          <dd>Confidence: {formatConfidence(candidate.confidence)}</dd>
        </div>
        <div>
          <dt className="sr-only">Evidence</dt>
          <dd>Evidence: {candidateEvidenceCompleteness(candidate)}</dd>
        </div>
        <div>
          <dt className="sr-only">Run plan</dt>
          <RunPlanRelationDisplay
            candidateId={candidate.candidate_id}
            relation={runPlanRelation}
          />
        </div>
        <div>
          <dt className="sr-only">Next action</dt>
          <dd data-testid={`paper-candidate-next-${candidate.candidate_id}`}>
            Next: {candidateNextAction(candidate)}
          </dd>
        </div>
      </dl>

      <div className="flex flex-wrap gap-3 text-sm">
        <Link
          href={candidateDetailHref(candidate.candidate_id)}
          className="inline-flex min-h-11 items-center underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          View candidate detail
        </Link>
        {relatedObjectAvailable(candidate.draft_id) ? (
          <Link
            href={draftDetailHref(candidate.draft_id)}
            className="inline-flex min-h-11 items-center text-text-secondary underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
          >
            Related draft
          </Link>
        ) : null}
        {relatedObjectAvailable(candidate.backtest_run_id) ? (
          <Link
            href={backtestDetailHref(candidate.backtest_run_id)}
            className="inline-flex min-h-11 items-center text-text-secondary underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
          >
            Related backtest
          </Link>
        ) : null}
      </div>
    </article>
  );
}
