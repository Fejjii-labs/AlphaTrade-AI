"use client";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { KnowledgeDocumentCard } from "@/components/knowledge/KnowledgeDocumentCard";
import { KnowledgeDetailPanel } from "@/components/knowledge/KnowledgeDetailPanel";
import { JournalReturnLink, journalReturnKey } from "./JournalReturnLink";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/input";
import { useAsyncData } from "@/hooks/useAsyncData";
import { useAuth } from "@/contexts/AuthContext";
import { api } from "@/lib/api";
import {
  ENTRY_CATEGORIES,
  ENTRY_LABELS,
  savedEntries,
  type SavedEntry,
  type EntryCategory,
} from "@/lib/api/saved-entries";
import { formatDateTime } from "@/lib/format";
import { isAlphaTradeBloFinExecution } from "@/lib/journal-activity";
import { NativeActivity } from "@/components/activity/NativeActivity";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";

export function SavedEntryDetail({
  entry,
  onChanged,
}: {
  entry: SavedEntry;
  onChanged: () => void;
}) {
  const [current, setCurrent] = useState(entry);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [summary, setSummary] = useState(entry.summary);
  async function update(body: Parameters<typeof savedEntries.update>[1]) {
    setBusy(true);
    setError(null);
    try {
      const result = await savedEntries.update(current.id, body);
      setCurrent(result);
      setEditing(false);
      onChanged();
    } catch {
      setError(
        "Save failed. Your correction is retained; reload before retrying a conflicting revision.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="space-y-3 rounded-card border border-border-subtle p-4">
      <h2 className="text-xl font-semibold">{current.title}</h2>
      <p className="text-sm text-text-muted">
        {ENTRY_LABELS[current.category]} · {formatDateTime(current.updated_at)}{" "}
        · {current.undone ? "Undone" : `v${current.revision}`}
      </p>
      <p className="whitespace-pre-wrap break-words text-base leading-relaxed">{current.summary}</p>
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm">
          Category{" "}
          <Select
            aria-label="Saved entry category"
            value={current.category}
            disabled={busy || current.undone}
            onChange={(e) =>
              void update({
                expected_revision: current.revision,
                category: e.target.value as EntryCategory,
              })
            }
          >
            {ENTRY_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {ENTRY_LABELS[c]}
              </option>
            ))}
          </Select>
        </label>
        <Button
          variant="outline"
          disabled={busy || current.undone}
          onClick={() => setEditing((v) => !v)}
        >
          Correct summary
        </Button>
        <Button
          variant="outline"
          disabled={busy || current.undone}
          onClick={() =>
            void update({ expected_revision: current.revision, undo: true })
          }
        >
          Undo
        </Button>
      </div>
      {editing && (
        <div className="space-y-2">
          <Textarea
            aria-label="Corrected summary"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
          />
          <Button
            disabled={busy || !summary.trim()}
            onClick={() =>
              void update({ expected_revision: current.revision, summary })
            }
          >
            Save correction
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex gap-4 text-sm text-accent">
        <Link href={`/agent?conversation=${current.conversation_id}`}>
          Original conversation
        </Link>
        {current.source_document_id && (
          <Link
            href={`/journal?tab=knowledge&document_id=${current.source_document_id}`}
          >
            Original document
          </Link>
        )}
        {current.trade_id && (
          <Link href={`/journal?trade_id=${current.trade_id}`}>
            Exact trade
          </Link>
        )}
      </div>
      <details>
        <summary className="min-h-11 cursor-pointer text-sm">
          Original content &amp; evidence
        </summary>
        <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{current.original_text}</p>
        {current.draft && (
          <pre className="whitespace-pre-wrap break-words text-xs">
            {JSON.stringify(current.draft, null, 2)}
          </pre>
        )}
      </details>
    </article>
  );
}

export function JournalKnowledgeWorkspace() {
  const { user, organization } = useAuth();
  const [session, setSession] = useState(sessionGeneration);
  useEffect(() => onSessionCleared(() => setSession(sessionGeneration())), []);
  return <JournalWorkspaceContent key={JSON.stringify([organization?.id, user?.id, session])} />;
}

function JournalWorkspaceContent() {
  const params = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const { user, organization } = useAuth();
  const knowledge =
    params.get("tab") === "knowledge" || pathname === "/knowledge";
  const category = params.get("category") ?? "";
  const query = params.get("q") ?? "";
  const savedId = params.get("saved");
  const documentId = params.get("document_id") ?? params.get("document");
  const page = Math.max(0, Number(params.get("page")) || 0);
  const sourcePage = Math.max(0, Number(params.get("source_page")) || 0);
  const chunkPage = Math.max(0, Number(params.get("chunk_page")) || 0);
  const loader = useCallback(async () => {
    if (savedId || documentId) {
      // Exact source navigation must not wait for unrelated library listings.
      const [linked, chunks] = await Promise.allSettled([
        savedId ? savedEntries.get(savedId) : Promise.resolve(null),
        documentId ? api.knowledge.listChunks({
          document_id: documentId, limit: 50, offset: chunkPage * 50,
        }) : Promise.resolve(null),
      ]);
      return {
        entries: [], entryTotal: 0, sourceTotal: 0, trades: [], documents: [],
        linked: linked.status === "fulfilled" ? linked.value : null,
        chunks: chunks.status === "fulfilled" ? chunks.value : null,
      };
    }
    const [entries, trades, documents, linked, chunks] =
      await Promise.allSettled([
        savedEntries.list({
          view: knowledge ? "knowledge" : "journal",
          category: knowledge ? category || undefined : "journal",
          q: query,
          limit: 50,
          offset: page * 50,
        }),
        knowledge
          ? Promise.resolve(null)
          : api.journal.listTrades({ limit: 50, offset: sourcePage * 50 }),
        knowledge
          ? api.knowledge.listDocuments({ limit: 50, offset: sourcePage * 50 })
          : Promise.resolve(null),
        savedId ? savedEntries.get(savedId) : Promise.resolve(null),
        documentId
          ? api.knowledge.listChunks({
              document_id: documentId,
              limit: 50,
              offset: chunkPage * 50,
            })
          : Promise.resolve(null),
      ]);
    return {
      entries:
        entries.status === "fulfilled"
          ? entries.value.items.filter((e) =>
              knowledge ? e.category !== "journal" : true,
            )
          : null,
      entryTotal: entries.status === "fulfilled" ? entries.value.total : 0,
      sourceTotal: knowledge
        ? documents.status === "fulfilled"
          ? (documents.value?.total ?? 0)
          : 0
        : trades.status === "fulfilled"
          ? (trades.value?.total ?? 0)
          : 0,
      trades: trades.status === "fulfilled" ? trades.value?.items : null,
      documents:
        documents.status === "fulfilled" ? documents.value?.items : null,
      linked: linked.status === "fulfilled" ? linked.value : null,
      chunks: chunks.status === "fulfilled" ? chunks.value : null,
    };
  }, [
    knowledge,
    category,
    query,
    savedId,
    documentId,
    page,
    sourcePage,
    chunkPage,
  ]);
  const { data, loading, reload } = useAsyncData(loader, [
    knowledge,
    category,
    query,
    savedId,
    documentId,
    page,
    sourcePage,
    chunkPage,
  ]);
  const readinessDeadline = useRef(0);
  const hasPending = Boolean(data?.documents?.some(document =>
    document.ingestion_metadata?.indexing?.vector_index_status === "pending"));
  useEffect(() => {
    if (!knowledge || savedId || documentId || !hasPending) { readinessDeadline.current = 0; return; }
    if (!readinessDeadline.current) readinessDeadline.current = Date.now() + 60_000;
    if (Date.now() >= readinessDeadline.current) return;
    const timer = setTimeout(() => { void reload(); }, 2_000);
    return () => clearTimeout(timer);
  }, [knowledge, savedId, documentId, hasPending, data, reload]);
  const returnKey = journalReturnKey(user?.id, organization?.id);
  useEffect(() => {
    if (loading || savedId || documentId) return;
    try {
      const stored = JSON.parse(sessionStorage.getItem(returnKey) ?? "null");
      if (stored?.href === `${pathname}?${params}`)
        window.scrollTo(0, stored.scroll ?? 0);
    } catch {
      /* no stored return */
    }
  }, [loading, returnKey, pathname, params, savedId, documentId]);
  const remember = () => {
    try {
      sessionStorage.setItem(
        returnKey,
        JSON.stringify({
          href: `${pathname}?${params}`,
          scroll: window.scrollY,
        }),
      );
    } catch {
      /* storage unavailable */
    }
  };
  function filter(key: string, value: string) {
    const q = new URLSearchParams(params);
    q.delete("saved");
    q.delete("document_id");
    q.delete("document");
    q.delete("chunk_page");
    if (key !== "page" && key !== "source_page" && key !== "document_id") {
      q.delete("page");
      q.delete("source_page");
    }
    if (value) q.set(key, value);
    else q.delete(key);
    router.replace(`${pathname}?${q}`, { scroll: false });
  }
  function changeChunkPage(next: number) {
    const q = new URLSearchParams(params);
    if (next) q.set("chunk_page", String(next));
    else q.delete("chunk_page");
    router.replace(`${pathname}?${q}`, { scroll: false });
  }
  const native = data?.trades?.filter(isAlphaTradeBloFinExecution);
  const visibleTrades = native?.filter((t) =>
    `${t.symbol} ${t.direction} ${t.strategy_label ?? ""}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const documents = data?.documents?.filter(
    (d) =>
      !(d.ingestion_metadata as Record<string, unknown> | null)
        ?.agent_capture &&
      `${d.title}`.toLowerCase().includes(query.toLowerCase()) &&
      (!category ||
        (category === "rules"
          ? d.source_type === "risk_policy"
          : category === "strategies"
            ? ["strategy_template", "trading_playbook"].includes(d.source_type)
            : category === "lessons"
              ? ["review_note", "mistakes_database"].includes(d.source_type)
              : d.source_type === "general_note")),
  );
  return (
    <div className="space-y-4 [&_input]:text-base [&_select]:text-base [&_textarea]:text-base lg:[&_input]:text-sm lg:[&_select]:text-sm lg:[&_textarea]:text-sm" data-testid="journal-knowledge-workspace">
      <div className="flex flex-wrap justify-between gap-3">
        <h1 className="text-2xl font-semibold">Journal &amp; Knowledge</h1>
        <Link href="/agent" className="text-accent">
          Add with Agent
        </Link>
      </div>
      <nav
        aria-label="Journal and Knowledge"
        className="flex gap-3 border-b border-border-subtle"
      >
        <Link
          onClick={remember}
          href="/journal"
          aria-current={!knowledge ? "page" : undefined}
          className="min-h-11 px-3 py-3"
        >
          Journal
        </Link>
        <Link
          onClick={remember}
          href="/journal?tab=knowledge"
          aria-current={knowledge ? "page" : undefined}
          className="min-h-11 px-3 py-3"
        >
          Knowledge
        </Link>
      </nav>
      {savedId || documentId ? <JournalReturnLink /> : null}
      {!knowledge && !savedId && !documentId ? <NativeActivity /> : null}
      {!savedId && !documentId ? <div data-testid="knowledge-filters" className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 sm:flex sm:flex-wrap">
        <Input
          aria-label="Search entries"
          placeholder="Search"
          value={query}
          onChange={(e) => filter("q", e.target.value)}
          className="col-span-2 min-w-0 sm:w-auto sm:flex-1"
        />
        {knowledge && (
          <Select
            aria-label="Knowledge category"
            className="min-w-0 sm:w-auto"
            value={category}
            onChange={(e) => filter("category", e.target.value)}
          >
            <option value="">All categories</option>
            {ENTRY_CATEGORIES.filter((c) => c !== "journal").map((c) => (
              <option value={c} key={c}>
                {ENTRY_LABELS[c]}
              </option>
            ))}
          </Select>
        )}
        <Button
          disabled={loading}
          variant="outline"
          size="sm"
          onClick={() => { readinessDeadline.current = 0; void reload(); }}
        >
          Refresh
        </Button>
      </div> : null}
      {loading ? (
        <p role="status">Loading entries…</p>
      ) : (
        <>
          {!knowledge && !savedId && !documentId ? <h2 className="text-base font-semibold">Saved notes &amp; recorded trade reviews</h2> : null}
          {savedId ? (
            data?.linked ? (
              <SavedEntryDetail
                key={data.linked.id}
                entry={data.linked}
                onChanged={() => void reload()}
              />
            ) : (
              <p role="alert">Saved entry unavailable.</p>
            )
          ) : null}
          {documentId && (
            <>
              {(!data?.chunks || data.chunks.total === 0) && (
                <p role="alert" data-testid="knowledge-document-stale">
                  Original document unavailable or has no readable text. No
                  unrelated document was opened.
                </p>
              )}
              <KnowledgeDetailPanel
                documentId={documentId}
                chunks={
                  data?.chunks
                    ? {
                        available: true,
                        data: data.chunks,
                        error: null,
                        fallbackUsed: false,
                      }
                    : null
                }
                loading={false}
                onRetry={() => void reload()}
              />
              {data?.chunks && data.chunks.total > 50 && (
                <nav
                  aria-label="Original document pages"
                  className="flex items-center gap-3 text-sm"
                >
                  <Button
                    variant="outline"
                    disabled={loading || chunkPage === 0}
                    onClick={() => changeChunkPage(chunkPage - 1)}
                  >
                    Previous
                  </Button>
                  <span>
                    Text page {chunkPage + 1} of{" "}
                    {Math.ceil(data.chunks.total / 50)}
                  </span>
                  <Button
                    variant="outline"
                    disabled={
                      loading || (chunkPage + 1) * 50 >= data.chunks.total
                    }
                    onClick={() => changeChunkPage(chunkPage + 1)}
                  >
                    Next
                  </Button>
                </nav>
              )}
            </>
          )}
          {data?.entries == null && (
            <p role="alert">Saved entries unavailable.</p>
          )}
          {!savedId && !documentId && (
            <div className="grid gap-3 xl:grid-cols-2">
              {data?.entries?.map((e) => (
                <article
                  key={e.id}
                  className="space-y-2 rounded-card border border-border-subtle bg-surface-1 p-4"
                >
                  <div className="flex justify-between gap-3">
                    <h2 className="font-semibold">{e.title}</h2>
                    <span className="text-caption text-text-muted">
                      {ENTRY_LABELS[e.category]}
                    </span>
                  </div>
                  <p className="line-clamp-3 text-sm text-text-secondary">
                    {e.summary}
                  </p>
                  <Link
                    onClick={remember}
                    href={`/journal?${knowledge ? `tab=knowledge&category=${e.category}&` : ""}saved=${encodeURIComponent(e.id)}`}
                    className="inline-flex min-h-11 items-center text-sm text-accent"
                  >
                    Open
                  </Link>
                </article>
              ))}
              {!knowledge &&
                visibleTrades?.map((t) => (
                  <article
                    key={t.id}
                    className="space-y-2 rounded-card border border-border-subtle p-4"
                  >
                    <h2 className="font-medium">
                      {t.symbol} · {t.direction}
                    </h2>
                    <p className="text-sm text-text-muted">
                      Recorded trade review · {t.status} · {formatDateTime(t.entry_time)}
                      {t.source === "manual_demo_test"
                        ? " · Connectivity test"
                        : ""}
                    </p>
                    <Link
                      onClick={remember}
                      href={`/journal?trade_id=${t.id}`}
                      className="inline-flex min-h-11 items-center text-accent"
                    >
                      Open exact trade
                    </Link>
                  </article>
                ))}
              {knowledge &&
                documents?.map((d) => (
                  <KnowledgeDocumentCard
                    key={d.id}
                    document={d}
                    onToggleExpand={() => {
                      remember();
                      filter("document_id", d.id);
                    }}
                  />
                ))}
              {data?.entries?.length === 0 &&
                (knowledge
                  ? documents?.length === 0
                  : visibleTrades?.length === 0) && (
                  <p className="text-sm text-text-muted">
                    {knowledge ? "No matching entries." : "No matching saved notes or recorded reviews."}
                  </p>
                )}
            </div>
          )}
          {!savedId && !documentId && data && (
            <div className="flex flex-wrap gap-4 text-sm">
              {[
                ["page", page, data.entryTotal, "Saved notes"],
                [
                  "source_page",
                  sourcePage,
                  data.sourceTotal,
                  knowledge ? "Documents" : "Recorded reviews",
                ],
              ].filter(([, index, total]) => Number(total) > 50 || Number(index) > 0).map(([key, index, total, label]) => (
                <nav
                  key={String(key)}
                  aria-label={`${label} pages`}
                  className="flex items-center gap-3"
                >
                  <span>
                    {label} · page {Number(index) + 1} of{" "}
                    {Math.max(1, Math.ceil(Number(total) / 50))}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={Number(index) === 0}
                    onClick={() =>
                      filter(String(key), String(Number(index) - 1))
                    }
                  >
                    Previous
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={(Number(index) + 1) * 50 >= Number(total)}
                    onClick={() =>
                      filter(String(key), String(Number(index) + 1))
                    }
                  >
                    Next
                  </Button>
                </nav>
              ))}
            </div>
          )}
          {knowledge && data?.documents == null && (
            <p role="alert">Documents unavailable.</p>
          )}
          {!knowledge && data?.trades == null && (
            <p role="alert">BloFin activity unavailable.</p>
          )}
        </>
      )}
      {!knowledge && (
        <details className="text-sm">
          <summary className="min-h-11 cursor-pointer text-text-muted">
            Historical records
          </summary>
          <Link href="/journal?view=record" className="text-accent">
            Open preserved legacy records
          </Link>
        </details>
      )}
    </div>
  );
}
