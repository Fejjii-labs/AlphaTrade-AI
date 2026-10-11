"use client";
import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { captureRetry } from "@/lib/api/generated/client";
import {
  ENTRY_CATEGORIES,
  ENTRY_LABELS,
  savedEntries,
  type SavedEntry,
  type EntryCategory,
} from "@/lib/api/saved-entries";
export function SavedReceipt({
  capture,
  conversationId,
  sourceDocumentId,
}: {
  capture: {
    saved_entries: SavedEntry[];
    status: string;
    error?: string | null;
    clarification?: string | null;
    source_message_id?: string | null;
  };
  conversationId: string;
  sourceDocumentId?: string;
}) {
  const [entries, setEntries] = useState(capture.saved_entries);
  const [error, setError] = useState(capture.error);
  const [busy, setBusy] = useState(false);
  const [categoryId, setCategoryId] = useState<string | null>(null);
  async function update(entry: SavedEntry, category?: EntryCategory) {
    setBusy(true);
    setError(null);
    try {
      const result = await savedEntries.update(entry.id, {
        expected_revision: entry.revision,
        ...(category ? { category } : { undo: true }),
      });
      setEntries((items) => items.map((e) => (e.id === entry.id ? result : e)));
      setCategoryId(null);
    } catch {
      setError("Could not update the saved entry. Reload it before retrying.");
    } finally {
      setBusy(false);
    }
  }
  async function retry() {
    if (!capture.source_message_id) return;
    setBusy(true);
    setError(null);
    try {
      const result = await captureRetry({
        conversation_id: conversationId,
        source_message_id: capture.source_message_id,
      });
      setEntries(result.items);
    } catch {
      setError(
        "Capture still unavailable. Your original message remains in this conversation.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2 text-sm" data-testid="saved-receipt">
      {sourceDocumentId && (
        <p role="status">
          Source document retained in this conversation.{" "}
          <Link
            href={`/journal?tab=knowledge&document_id=${encodeURIComponent(sourceDocumentId)}`}
            className="text-accent"
          >
            Open original document
          </Link>
        </p>
      )}
      {entries.map((e) => (
        <div
          key={e.id}
          className="space-y-2 rounded-control border border-border-subtle p-3"
          role="status"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs text-text-secondary">
              {e.undone ? "Undone" : `Saved to ${ENTRY_LABELS[e.category]}`}
            </span>
            {!e.undone && <Link
              href={e.category === "journal"
                ? `/journal?saved=${encodeURIComponent(e.id)}`
                : `/journal?tab=knowledge&category=${e.category}&saved=${encodeURIComponent(e.id)}`}
              aria-label={`Open ${e.title} in ${ENTRY_LABELS[e.category]}`}
              className="text-accent"
            >Open</Link>}
          </div>
          <p className="font-medium">{e.title}</p>
          <p className="line-clamp-2 text-text-secondary">{e.summary}</p>
          {!e.undone && (
            <details>
              <summary className="min-h-11 cursor-pointer content-center text-xs text-text-secondary">Saved note details</summary>
              <p className="py-2 text-text-secondary">{e.summary}</p>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => setCategoryId(categoryId === e.id ? null : e.id)}
                >
                  Reclassify
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => void update(e)}
                >
                  Undo
                </Button>
              </div>
              {categoryId === e.id && (
                <select aria-label="Reclassify saved entry" disabled={busy} value={e.category}
                  onChange={(event) => void update(e, event.target.value as EntryCategory)}
                  className="min-h-11 bg-surface-1">
                  {ENTRY_CATEGORIES.map((c) => <option key={c} value={c}>{ENTRY_LABELS[c]}</option>)}
                </select>
              )}
            </details>
          )}
        </div>
      ))}
      {capture.clarification && <p role="status">{capture.clarification}</p>}
      {error && (
        <p role="alert" className="text-warning">
          {error}
          {capture.source_message_id && (
            <Button
              className="ml-2"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void retry()}
            >
              Retry save
            </Button>
          )}
        </p>
      )}
    </div>
  );
}
