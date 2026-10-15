"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { FileImportPreview, IngestDocumentResponse, DocumentSourceType } from "@/lib/api/types";

type KnowledgeStorePanelProps = {
  onStored?: () => void;
  initialMode?: "paste" | "file";
  initialSourceType?: "risk_policy" | "trading_playbook" | "general_note";
};

function storageMessage(result: IngestDocumentResponse): string {
  const saved = `${result.duplicate ? "Already stored" : "Stored"} document ${result.document_id} (${result.chunk_count} chunks).`;
  if (result.vector_index_status === "pending") return `${saved} Search indexing is pending.`;
  if (result.vector_index_status === "failed") return `${saved} Search indexing failed. Retry indexing from the document.`;
  if (result.vector_index_status === "ready") return `${saved} Search indexing is ready.`;
  if (result.vector_index_status !== "upsert_acknowledged") {
    return `${saved} Search indexing status is unverified.`;
  }
  if (result.fallback_used || result.vector_backend === "in-memory-vector") {
    return `${saved} Search used a local or fallback index; hosted search readiness is not established.`;
  }
  return `${saved} Search index acknowledged by ${result.vector_backend || "the configured backend"}.`;
}

/** Preview before file save; pasted text retains canonical ingestion. */
export function KnowledgeStorePanel({
  onStored,
  initialSourceType = "trading_playbook",
  initialMode = "paste",
}: KnowledgeStorePanelProps) {
  const [title, setTitle] = useState("");
  const [sourceType, setSourceType] = useState<DocumentSourceType>(initialSourceType);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"paste" | "file">(initialMode);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<FileImportPreview | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function invalidatePreview() {
    setPreview(null);
    setError(null);
    setMessage(null);
  }

  async function previewFile() {
    if (busy || !file || !title.trim()) return;
    setBusy(true);
    invalidatePreview();
    try {
      setPreview(await api.knowledge.previewFile(file, title.trim(), sourceType));
    } catch (err) {
      setError(err instanceof Error ? err.message : "File preview failed");
    } finally {
      setBusy(false);
    }
  }

  async function ingest() {
    if (busy || !title.trim() || (mode === "file" ? !file || !preview : !text.trim())) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result =
        mode === "file" && file && preview
          ? await api.knowledge.importFile(
              file,
              title.trim(),
              sourceType,
              preview.preview_receipt,
            )
          : await api.knowledge.ingest({
              title: title.trim(),
              text: text.trim(),
              source_type: sourceType,
            });
      setMessage(storageMessage(result));
      if (mode === "file") {
        setFile(null);
        setPreview(null);
        setFileInputKey((current) => current + 1);
      } else {
        setText("");
      }
      onStored?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ingest failed");
      if (mode === "file") setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="knowledge-store-heading"
      data-testid="knowledge-store-panel"
      className="space-y-3"
    >
      <div>
        <h2
          id="knowledge-store-heading"
          className="text-lg font-semibold text-text-primary"
        >
          Add knowledge
        </h2>
        <p className="mt-1 text-sm text-text-muted">
          Saved to your existing knowledge library. Existing documents cannot be
          edited here.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {mode === "file" ? "Import a document" : "Trading note"}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2" aria-label="Knowledge input method">
            <Button
              variant="outline"
              aria-pressed={mode === "paste"}
              disabled={busy}
              onClick={() => {
                setMode("paste");
                invalidatePreview();
              }}
            >
              Paste text
            </Button>
            <Button
              variant="outline"
              aria-pressed={mode === "file"}
              disabled={busy}
              onClick={() => {
                setMode("file");
                invalidatePreview();
              }}
            >
              Import a file
            </Button>
          </div>
          <div className="space-y-2">
            <Label htmlFor="knowledge-ingest-category">Category</Label>
            <Select
              id="knowledge-ingest-category"
              value={sourceType}
              disabled={busy}
              onChange={(event) => {
                if (["risk_policy", "trading_playbook", "general_note"].includes(event.target.value))
                  setSourceType(event.target.value as DocumentSourceType);
                invalidatePreview();
              }}
            >
              <option value="risk_policy">Trading Rules</option>
              <option value="trading_playbook">Playbook</option>
              <option value="general_note">Market Observations</option>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="knowledge-ingest-title">Title</Label>
            <Input
              id="knowledge-ingest-title"
              value={title}
              disabled={busy}
              onChange={(event) => {
                setTitle(event.target.value);
                invalidatePreview();
              }}
              data-testid="knowledge-ingest-title"
            />
          </div>
          {mode === "paste" ? (
            <div className="space-y-2">
              <Label htmlFor="knowledge-ingest-text">Document text</Label>
              <Textarea
                id="knowledge-ingest-text"
                value={text}
                disabled={busy}
                onChange={(event) => setText(event.target.value)}
                data-testid="knowledge-ingest-text"
              />
            </div>
          ) : (
            <div className="space-y-3">
              <div className="space-y-2">
                <Label htmlFor="knowledge-import-file">Document file</Label>
                <Input
                  key={fileInputKey}
                  id="knowledge-import-file"
                  type="file"
                  accept=".txt,.md,.docx,.pdf"
                  disabled={busy}
                  onChange={(event) => {
                    invalidatePreview();
                    const selected = event.target.files?.[0] ?? null;
                    if (selected && selected.size > 5 * 1024 * 1024) {
                      setFile(null);
                      event.target.value = "";
                      setError("Choose a file no larger than 5 MiB.");
                      return;
                    }
                    setFile(selected);
                    if (selected && !title.trim()) {
                      setTitle(selected.name.replace(/\.[^.]+$/, ""));
                    }
                  }}
                />
                <p className="text-sm text-text-muted">
                  TXT, Markdown, DOCX or selectable-text PDF, up to 5 MiB and
                  100,000 extracted characters. Scanned PDFs and image text
                  require OCR and are not supported.
                </p>
              </div>
              <Button
                variant="outline"
                disabled={busy || !file || !title.trim()}
                onClick={() => void previewFile()}
              >
                Preview file
              </Button>
              {preview ? (
                <div className="space-y-2" data-testid="knowledge-file-preview">
                  <p className="break-words text-sm">
                    {preview.filename} · {preview.extracted_characters} characters
                  </p>
                  <p role="status" className="text-sm text-text-muted">
                    Preview only. Nothing has been saved or indexed. Review the
                    extracted text before saving.
                  </p>
                  {preview.warnings.map((warning) => (
                    <p key={warning} className="text-sm text-warning">
                      {warning}
                    </p>
                  ))}
                  <Label htmlFor="knowledge-extracted-preview">
                    Extracted text preview
                  </Label>
                  <Textarea
                    id="knowledge-extracted-preview"
                    readOnly
                    rows={12}
                    value={preview.extracted_text}
                  />
                </div>
              ) : null}
            </div>
          )}
          <Button
            disabled={busy || !title.trim() || (mode === "file" ? !file || !preview : !text.trim())}
            onClick={() => void ingest()}
            data-testid="knowledge-ingest-submit"
          >
            {busy ? "Working…" : mode === "file" ? "Save previewed file" : "Save note"}
          </Button>
          {message ? (
            <p
              role="status"
              className="break-words text-sm text-success"
              data-testid="knowledge-ingest-success"
            >
              {message}
            </p>
          ) : null}
          {error ? (
            <p
              className="text-sm text-danger"
              role="alert"
              data-testid="knowledge-ingest-error"
            >
              {error}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </section>
  );
}
