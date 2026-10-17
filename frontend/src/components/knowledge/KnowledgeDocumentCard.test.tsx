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

it("keeps lesson provenance inside collapsed details with a compact open control", () => {
  const open = vi.fn();
  render(<KnowledgeDocumentCard document={document} onToggleExpand={open} />);
  expect(screen.getByTestId("knowledge-category-badge")).toHaveTextContent("Accepted lesson");
  expect(screen.getByTestId("knowledge-document-id")).not.toBeVisible();
  expect(screen.getByTestId("knowledge-indexing-status")).toHaveTextContent("Stored · Search unverified");
  fireEvent.click(screen.getByRole("button", { name: "Open" }));
  expect(open).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByText("Provenance details", { selector: "summary" }));
  expect(screen.getByTestId("knowledge-document-id")).toBeVisible();
});

it("queues an explicit indexing retry without claiming current search readiness", async () => {
  vi.mocked(api.knowledge.retryIndexing).mockResolvedValue({} as Awaited<ReturnType<typeof api.knowledge.retryIndexing>>);
  render(<KnowledgeDocumentCard document={{ ...document, ingestion_metadata: { indexing: { vector_index_status: "failed", observed_at: "2026-10-11T10:00:00Z", fallback_used: false, sql_chunk_count: 1 } } }} />);
  fireEvent.click(screen.getByRole("button", { name: "Retry indexing" }));
  await waitFor(() => expect(api.knowledge.retryIndexing).toHaveBeenCalledExactlyOnceWith(document.id));
  expect(await screen.findByText("Retry queued. Refresh the library to check readiness.")).toBeInTheDocument();
  expect(screen.getByTestId("knowledge-indexing-status")).toHaveTextContent("Search failed");
});
