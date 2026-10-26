"use client";

import Link from "next/link";
import { useState } from "react";

import { JournalEntryCard } from "@/components/JournalEntryCard";
import { DisciplineAnalysisPanel } from "@/components/journal/DisciplineAnalysisPanel";
import {
  journalEntryHref,
  relatedLessonsHref,
  relatedPlanHref,
} from "@/components/journal/journalContext";
import { Button } from "@/components/ui/button";
import type { JournalEntry } from "@/lib/api/types";

type RecentJournalEntriesProps = {
  entries: JournalEntry[] | null;
  available: boolean;
  error?: string | null;
  highlightedEntryId?: string | null;
  staleEntryMessage?: string | null;
  mutationError?: string | null;
  onRetry?: () => void;
  busy?: boolean;
  disciplineId?: string | null;
  discipline?: {
    comparison: Parameters<typeof DisciplineAnalysisPanel>[0]["comparison"];
    lesson_candidate_ids?: string[];
  } | null;
  disciplineError?: string | null;
  onAnalyze?: (entryId: string) => void;
  onCreateLesson?: (entry: JournalEntry) => void;
  onDelete?: (entryId: string) => void;
};

export function RecentJournalEntries({
  entries,
  available,
  error,
  highlightedEntryId,
  staleEntryMessage,
  mutationError,
  onRetry,
  busy,
  disciplineId,
  discipline,
  disciplineError,
  onAnalyze,
  onCreateLesson,
  onDelete,
}: RecentJournalEntriesProps) {
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  return (
    <section
      aria-labelledby="recent-journal-entries-heading"
      data-testid="recent-journal-entries"
      className="space-y-3"
    >
      <div>
        <h2
          id="recent-journal-entries-heading"
          className="text-lg font-semibold text-text-primary"
        >
          Recent entries
        </h2>
        <p className="mt-1 text-sm text-text-muted">
          What you journaled recently from the loaded journal entry source.
        </p>
      </div>

      {staleEntryMessage ? (
        <div
          role="alert"
          data-testid="journal-stale-entry"
          className="rounded-control border border-warning-border bg-warning-muted/40 px-3 py-2 text-sm text-warning"
        >
          {staleEntryMessage}
        </div>
      ) : null}

      {mutationError ? (
        <div
          role="alert"
          data-testid="journal-mutation-error"
          className="rounded-control border border-danger-border bg-danger-muted/40 px-3 py-2 text-sm text-danger"
        >
          {mutationError}
        </div>
      ) : null}

      {!available ? (
        <div
          role="alert"
          data-testid="recent-entries-unavailable"
          className="rounded-control border border-danger-border bg-danger-muted/40 px-3 py-2 text-sm text-danger"
        >
          <p>{error ?? "Recent journal entries are unavailable."}</p>
          <p className="mt-1 text-caption">
            This is not an empty journal. Counts and empty success copy are suppressed.
          </p>
          {onRetry ? (
            <Button type="button" size="sm" variant="outline" className="mt-2" onClick={onRetry}>
              Retry
            </Button>
          ) : null}
        </div>
      ) : null}

      {available && entries && entries.length === 0 ? (
        <div
          role="status"
          data-testid="recent-entries-empty"
          className="rounded-control border border-border-subtle px-3 py-2 text-sm text-text-secondary"
        >
          No journal entries yet. Use quick entry below to capture your first trade reflection.
        </div>
      ) : null}

      {available && entries && entries.length > 0 ? (
        <ul className="grid gap-4" data-testid="recent-entries-list">
          {entries.map((entry) => {
            const highlighted = highlightedEntryId === entry.id;
            const confirmingDelete = pendingDeleteId === entry.id;
            return (
              <li
                key={entry.id}
                id={`journal-entry-${entry.id}`}
                data-testid={`recent-entry-${entry.id}`}
                className={
                  highlighted
                    ? "rounded-control ring-2 ring-accent-border space-y-2 p-1"
                    : "space-y-2"
                }
              >
                <JournalEntryCard entry={entry} />
                <div className="flex flex-wrap gap-2">
                  {entry.linked_proposal_id ? (
                    <Link
                      href={relatedPlanHref(entry.linked_proposal_id)}
                      className="inline-flex h-10 items-center rounded-control border border-border px-3 text-sm text-text-secondary underline"
                      data-testid={`related-plan-${entry.id}`}
                    >
                      Related plan
                    </Link>
                  ) : null}
                  {entry.linked_position_id ? (
                    <span
                      className="inline-flex h-10 items-center rounded-control border border-border px-3 text-sm text-text-muted"
                      data-testid={`related-position-${entry.id}`}
                    >
                      Linked to a position
                    </span>
                  ) : null}
                  <Link
                    href={journalEntryHref(entry.id)}
                    className="inline-flex h-10 items-center rounded-control border border-border px-3 text-sm text-text-secondary underline"
                  >
                    Open entry context
                  </Link>
                  <Link
                    href={relatedLessonsHref()}
                    className="inline-flex h-10 items-center rounded-control border border-border px-3 text-sm text-text-secondary underline"
                  >
                    Review lessons
                  </Link>
                  {onAnalyze ? (
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      disabled={busy}
                      onClick={() => onAnalyze(entry.id)}
                    >
                      Discipline analysis
                    </Button>
                  ) : null}
                  {onDelete && !confirmingDelete ? (
                    <Button
                      type="button"
                      variant="destructive"
                      size="sm"
                      disabled={busy}
                      onClick={() => setPendingDeleteId(entry.id)}
                      data-testid={`delete-entry-${entry.id}`}
                    >
                      Delete entry
                    </Button>
                  ) : null}
                </div>
                {onDelete && confirmingDelete ? (
                  <div
                    role="group"
                    aria-label="Confirm delete journal entry"
                    data-testid={`delete-confirm-${entry.id}`}
                    className="space-y-2 rounded-control border border-danger-border bg-danger-muted/30 px-3 py-2"
                  >
                    <p className="text-sm text-danger">
                      Delete this journal entry permanently? This cannot be undone from the UI.
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="destructive"
                        size="sm"
                        disabled={busy}
                        onClick={() => {
                          onDelete(entry.id);
                          setPendingDeleteId(null);
                        }}
                        data-testid={`confirm-delete-${entry.id}`}
                      >
                        Confirm delete
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        onClick={() => setPendingDeleteId(null)}
                        data-testid={`cancel-delete-${entry.id}`}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : null}
                {disciplineId === entry.id ? (
                  <DisciplineAnalysisPanel
                    comparison={discipline?.comparison ?? null}
                    error={disciplineError}
                    lessonCandidateIds={discipline?.lesson_candidate_ids ?? []}
                    journalEntryId={entry.id}
                    onCreateLesson={
                      discipline?.lesson_candidate_ids?.length
                        ? undefined
                        : onCreateLesson
                          ? () => onCreateLesson(entry)
                          : undefined
                    }
                    createBusy={busy}
                  />
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
