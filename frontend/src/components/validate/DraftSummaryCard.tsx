import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { setupConditionLabel } from "@/lib/alert-display";
import type { PaperValidationDraftItem } from "@/lib/api/types";
import {
  draftMissingStructure,
  draftNextAction,
  formatConfidence,
  formatTimestamp,
} from "@/components/validate/validationDisplay";
import { draftDetailHref, sourceAlertHref } from "@/components/validate/validationLinks";

type DraftSummaryCardProps = {
  draft: PaperValidationDraftItem;
};

export function DraftSummaryCard({ draft }: DraftSummaryCardProps) {
  const missing = draftMissingStructure(draft);
  const created = formatTimestamp(draft.created_at);

  return (
    <article
      className="rounded-control border border-border-subtle bg-surface-0/40 px-4 py-3 space-y-3"
      data-testid={`paper-draft-${draft.draft_id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="info">{setupConditionLabel(draft.condition ?? "unknown")}</Badge>
            <span className="text-sm font-medium text-text-primary">
              {draft.symbol ?? "—"} · {draft.timeframe ?? "—"}
            </span>
            <Badge variant="muted">{draft.direction ?? "—"}</Badge>
            {draft.is_ready_for_validation ? (
              <Badge variant="default" data-testid={`paper-draft-ready-${draft.draft_id}`}>
                Ready
              </Badge>
            ) : null}
          </div>
          <p className="text-caption text-text-muted">
            Created {created ?? "unavailable"} · Source alert{" "}
            <Link
              href={sourceAlertHref(draft.source_alert_id)}
              className="underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
            >
              Open
            </Link>
          </p>
        </div>
        <div className="flex flex-col items-end gap-1 text-caption text-text-muted">
          <Badge variant="muted">{draft.status}</Badge>
          <span>Prep: {draft.prep_status ?? "draft"}</span>
          <span>Score: {draft.prep_completion_score ?? 0}%</span>
          <span>Confidence: {formatConfidence(draft.confidence)}</span>
        </div>
      </div>

      <p className="text-sm text-text-secondary">{draft.reason ?? "No reason provided."}</p>

      <div className="space-y-1 text-caption text-text-muted">
        <p>
          Readiness:{" "}
          <span className="text-text-primary">
            {draft.is_ready_for_validation ? "Ready for validation" : "Not ready"}
          </span>
        </p>
        <p data-testid={`paper-draft-missing-${draft.draft_id}`}>
          Missing structure:{" "}
          <span className="text-text-primary">
            {missing.length ? missing.join(", ") : "None reported"}
          </span>
        </p>
        <p data-testid={`paper-draft-next-${draft.draft_id}`}>
          Next action: <span className="text-text-primary">{draftNextAction(draft)}</span>
        </p>
      </div>

      <Link
        href={draftDetailHref(draft.draft_id)}
        className="inline-flex min-h-11 items-center text-sm text-text-primary underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
      >
        View draft detail
      </Link>
    </article>
  );
}
