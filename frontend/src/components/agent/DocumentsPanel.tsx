"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { SavedEntryDetail } from "@/components/journal/JournalKnowledgeWorkspace";
import { Button } from "@/components/ui/button";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import { savedEntries } from "@/lib/api/saved-entries";
import type { RagDocument } from "@/lib/api/types";
import { formatDate } from "@/lib/format";

const PAGE_SIZE = 50;
const BACK = "/agent?tab=documents";

/** Documents the agent reads from, excluding notes the agent captured itself. */
export function isUserDocument(document: RagDocument): boolean {
  return !(document.ingestion_metadata as Record<string, unknown> | null)?.agent_capture;
}

function DocumentRow({ document, onDeleted }: { document: RagDocument; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setDeleting(true);
    setError(null);
    try {
      await api.knowledge.deleteDocument(document.id);
      onDeleted();
    } catch {
      setError("Delete failed.");
      setConfirming(false);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <li className="space-y-1 py-2" data-testid="document-row">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link
          href={`${BACK}&document_id=${encodeURIComponent(document.id)}`}
          className="min-w-0 break-words font-medium text-text-primary hover:text-accent"
        >
          {document.title}
        </Link>
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-muted">{formatDate(document.created_at)}</span>
          {confirming ? (
            <>
              <Button size="sm" variant="destructive" disabled={deleting} onClick={() => void remove()}>
                Confirm delete
              </Button>
              <Button size="sm" variant="outline" disabled={deleting} onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </>
          ) : (
            <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
              Delete<span className="sr-only"> {document.title}</span>
            </Button>
          )}
        </div>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}
    </li>
  );
}

function DocumentList() {
  const [limit, setLimit] = useState(PAGE_SIZE);
  const loader = useCallback(() => api.knowledge.listDocuments({ limit, offset: 0 }), [limit]);
  const { data, loading, error, reload } = useAsyncData(loader, [limit]);
  if (loading && !data) return <p role="status">Loading documents…</p>;
  if (error || !data) return <p role="alert">Documents unavailable.</p>;
  const documents = data.items.filter(isUserDocument);
  return (
    <div className="space-y-3">
      {documents.length === 0 ? (
        <p className="text-sm text-text-muted">No documents yet. Attach one in the chat.</p>
      ) : (
        <ul className="divide-y divide-border-subtle" data-testid="documents-list">
          {documents.map((document) => (
            <DocumentRow key={document.id} document={document} onDeleted={() => void reload()} />
          ))}
        </ul>
      )}
      {data.total > limit ? (
        <Button variant="ghost" size="sm" onClick={() => setLimit(limit + PAGE_SIZE)}>
          More
        </Button>
      ) : null}
    </div>
  );
}

function DocumentText({ documentId }: { documentId: string }) {
  const loader = useCallback(() => api.knowledge.listChunks({ document_id: documentId, limit: 50, offset: 0 }), [documentId]);
  const { data, loading } = useAsyncData(loader, [documentId]);
  if (loading) return <p role="status">Loading…</p>;
  if (!data || data.total === 0) return <p role="alert">Document unavailable.</p>;
  const title = data.items[0]?.title;
  return (
    <article className="space-y-3" data-testid="document-text">
      {title ? <h2 className="text-section">{title}</h2> : null}
      {data.items.map((chunk) => (
        <p key={chunk.id} className="whitespace-pre-wrap break-words text-sm leading-relaxed text-text-secondary">
          {chunk.content}
        </p>
      ))}
    </article>
  );
}

function SavedNote({ savedId }: { savedId: string }) {
  const loader = useCallback(() => savedEntries.get(savedId), [savedId]);
  const { data, loading, reload } = useAsyncData(loader, [savedId]);
  if (loading) return <p role="status">Loading…</p>;
  if (!data) return <p role="alert">Note unavailable.</p>;
  return <SavedEntryDetail key={data.id} entry={data} onChanged={() => void reload()} />;
}

/** Agent → Documents: the list (open, delete), one document's text, or a saved note. */
export function DocumentsPanel({ documentId, savedId }: { documentId: string | null; savedId: string | null }) {
  if (documentId || savedId)
    return (
      <div className="space-y-3">
        <Link href={BACK} className="inline-flex min-h-11 items-center text-sm text-accent">
          ← Documents
        </Link>
        {documentId ? <DocumentText documentId={documentId} /> : <SavedNote savedId={savedId!} />}
      </div>
    );
  return <DocumentList />;
}
