"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";

import {
  formatKnowledgeTimestamp,
  formatSourceType,
  knowledgeCategory,
  resolveKnowledgeRelationships,
  type DeepLinkExclusionNotice,
} from "@/components/knowledge/knowledgeDisplay";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { RagDocument } from "@/lib/api/types";

type KnowledgeDocumentCardProps = {
  document: RagDocument;
  highlighted?: boolean;
  deepLinkNotices?: DeepLinkExclusionNotice[];
  expanded?: boolean;
  onToggleExpand?: () => void;
  detailSlot?: ReactNode;
};

export function KnowledgeDocumentCard({
  document,
  highlighted = false,
  deepLinkNotices = [],
  expanded = false,
  onToggleExpand,
  detailSlot,
}: KnowledgeDocumentCardProps) {
  const [retryError, setRetryError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const status = document.ingestion_metadata?.indexing?.vector_index_status ?? "unknown";
  const category = knowledgeCategory(document);
  const relationships = resolveKnowledgeRelationships(document);
  const createdLabel = formatKnowledgeTimestamp(document.created_at);
  const updatedLabel = formatKnowledgeTimestamp(document.updated_at);

  return (
    <Card
      id={`knowledge-document-${document.id}`}
      data-testid={`knowledge-document-card-${document.id}`}
      className={
        highlighted
          ? "min-w-0 border-info-border ring-2 ring-focus"
          : "min-w-0 border-border-subtle"
      }
    >
      <CardHeader className="min-w-0 space-y-2 pb-2">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
          <CardTitle className="min-w-0 break-words text-base text-text-primary">
            {document.title}
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="info" data-testid="knowledge-category-badge">
              {category.label}
            </Badge>
            <Badge variant="muted" data-testid="knowledge-source-type-badge">
              {formatSourceType(document.source_type)}
            </Badge>
          </div>
        </div>
        <p role="status" data-testid="knowledge-indexing-status">
          Stored · Search {status === "upsert_acknowledged" ? "previously acknowledged" : status === "unknown" ? "unverified" : status}
        </p>
        {status === "failed" ? <Button size="sm" variant="outline" type="button" disabled={retrying} onClick={async () => {
          setRetrying(true); setRetryError(null);
          try { await api.knowledge.retryIndexing(document.id); setRetryError("Retry queued. Refresh the library to check readiness."); }
          catch (error) { setRetryError(error instanceof Error ? error.message : "Indexing retry failed"); }
          finally { setRetrying(false); }
        }}>Retry indexing</Button> : null}
        {retryError ? <p role="status">{retryError}</p> : null}
        {deepLinkNotices.map((notice) => (
          <p
            key={notice.kind}
            role="status"
            data-testid={notice.testId}
            className="break-words text-sm text-warning"
          >
            {notice.message}
          </p>
        ))}
      </CardHeader>
      <CardContent className="min-w-0 space-y-3 text-sm text-text-secondary">
        <div
          data-testid="knowledge-source-context"
          className="min-w-0 space-y-1"
        >
          <details className="min-w-0 text-xs text-text-muted">
            <summary className="inline-flex min-h-11 cursor-pointer items-center">
              Provenance details
            </summary>
            <div className="space-y-1 break-all">
              <p data-testid="knowledge-document-id">Document: {document.id}</p>
              <p>Source URI: {document.source_uri?.trim() || "unavailable"}</p>
              <p>Version: {document.version}</p>
              <p>Created: {createdLabel ?? "unavailable"}</p>
              <p>Updated: {updatedLabel ?? "unavailable"}</p>
              {document.source_hash ? (
                <p>Source hash: {document.source_hash}</p>
              ) : null}
              {document.ingestion_metadata?.file ? (
                <>
                  <p>File: {document.ingestion_metadata.file.filename}</p>
                  <p>
                    Raw file SHA-256:{" "}
                    {document.ingestion_metadata.file.raw_content_hash}
                  </p>
                  <p>
                    Extracted characters:{" "}
                    {document.ingestion_metadata.file.extracted_characters}
                  </p>
                  <p>
                    Extraction:{" "}
                    {document.ingestion_metadata.file.parser_version}
                  </p>
                </>
              ) : null}
              {document.ingestion_metadata?.indexing ? (
                <p>
                  Recorded search index:{" "}
                  {document.ingestion_metadata.indexing.vector_backend ?? "unverified"}, {status} at{" "}
                  {document.ingestion_metadata.indexing.observed_at}
                  {document.ingestion_metadata.indexing.fallback_used
                    ? " (fallback/local)"
                    : ""}
                  . Current search availability is checked separately.
                </p>
              ) : (
                <p>Search indexing status: unverified.</p>
              )}
            </div>
          </details>
        </div>

        <ul className="min-w-0 space-y-1" data-testid="knowledge-relationships">
          {relationships.length === 0 && expanded ? (
            <li
              className="text-text-muted"
              data-testid="knowledge-relationship-none"
            >
              No stored relationship identifiers on this document.
            </li>
          ) : (
            relationships.map((link) => (
              <li
                key={`${link.kind}-${link.id ?? "missing"}`}
                className="min-w-0 break-words"
                data-testid={`knowledge-relationship-${link.kind}`}
              >
                {link.href && link.id ? (
                  <Link
                    href={link.href}
                    className="inline-flex min-h-11 items-center break-all underline text-text-primary"
                  >
                    {link.label}: open
                  </Link>
                ) : expanded ? (
                  <span
                    data-testid={`knowledge-relationship-unavailable-${link.kind}`}
                    className="text-text-muted"
                  >
                    {link.label}: {link.unavailableReason ?? "unavailable"}
                  </span>
                ) : null}
              </li>
            ))
          )}
        </ul>

        {onToggleExpand ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid={`knowledge-expand-${document.id}`}
            aria-expanded={expanded}
            onClick={onToggleExpand}
          >
            {expanded ? "Close" : "Open"}
          </Button>
        ) : null}

        {expanded ? detailSlot : null}
      </CardContent>
    </Card>
  );
}
