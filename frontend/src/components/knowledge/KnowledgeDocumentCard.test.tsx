import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { KnowledgeDocumentCard } from "./KnowledgeDocumentCard";
import { api } from "@/lib/api";
import type { RagDocument } from "@/lib/api/types";

vi.mock("@/lib/api", () => ({ api: { knowledge: { retryIndexing: vi.fn() } } }));

const document: RagDocument = {
  id: "document-private-reference", title: "Wait for the close", source_type: "review_note", source_uri: "local://synthetic-lesson",
  version: 1, created_at: "2026-10-11T10:00:00Z", updated_at: "2026-10-11T10:00:00Z",
};
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("shows only the added date and a compact open control", () => {
  const open = vi.fn();
  render(<KnowledgeDocumentCard document={document} onToggleExpand={open} />);
  expect(screen.getByTestId("knowledge-category-badge")).toHaveTextContent("Accepted lesson");
  expect(screen.getByTestId("knowledge-source-context")).toHaveTextContent(/^Added \S.*2026$/);
  expect(screen.queryByText(/document-private-reference|local:\/\/|Provenance details/)).not.toBeInTheDocument();
  expect(screen.getByTestId("knowledge-indexing-status")).toHaveTextContent("Stored · Search unverified");
  fireEvent.click(screen.getByRole("button", { name: "Open" }));
  expect(open).toHaveBeenCalledOnce();
});

it("queues an explicit indexing retry without claiming current search readiness", async () => {
  vi.mocked(api.knowledge.retryIndexing).mockResolvedValue({} as Awaited<ReturnType<typeof api.knowledge.retryIndexing>>);
  render(<KnowledgeDocumentCard document={{ ...document, ingestion_metadata: { indexing: { vector_index_status: "failed", observed_at: "2026-10-11T10:00:00Z", fallback_used: false, sql_chunk_count: 1 } } }} />);
  fireEvent.click(screen.getByRole("button", { name: "Retry indexing" }));
  await waitFor(() => expect(api.knowledge.retryIndexing).toHaveBeenCalledExactlyOnceWith(document.id));
  expect(await screen.findByText("Retry queued. Refresh the library to check readiness.")).toBeInTheDocument();
  expect(screen.getByTestId("knowledge-indexing-status")).toHaveTextContent("Search failed");
});

it("shows dates only, without file hashes, parser or index internals", () => {
  render(
    <KnowledgeDocumentCard
      document={{
        id: "doc",
        title: "Rules",
        source_type: "risk_policy",
        version: 1,
        created_at: "2026-10-05T10:00:00Z",
        updated_at: "2026-10-07T10:00:00Z",
        ingestion_metadata: {
          file: {
            filename: "rules.txt",
            media_type: "text/plain",
            raw_content_hash: "a".repeat(64),
            byte_size: 50,
            extracted_text_hash: "b".repeat(64),
            extracted_characters: 50,
            parser_version: "knowledge-file/v1",
            confirmed_at: "2026-10-05T10:00:00Z",
          },
          indexing: {
            sql_chunk_count: 2,
            vector_backend: "qdrant",
            vector_index_status: "upsert_acknowledged",
            fallback_used: false,
            observed_at: "2026-10-05T10:00:00Z",
          },
        },
      }}
    />,
  );
  expect(screen.getByTestId("knowledge-source-context")).toHaveTextContent(
    /^Added .*5, 2026 · Updated .*7, 2026$/,
  );
  expect(
    screen.queryByText(/SHA-256|knowledge-file\/v1|qdrant|rules\.txt|a{64}/),
  ).not.toBeInTheDocument();
});
