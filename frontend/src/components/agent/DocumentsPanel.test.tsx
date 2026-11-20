import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { RagDocument } from "@/lib/api/types";
import { expectNoIdentifiers } from "@/test/no-identifiers";

import { DocumentsPanel } from "./DocumentsPanel";

const doc = (id: string, title: string, meta: Record<string, unknown> | null = null) =>
  ({ id, title, source_type: "general_note", created_at: "2026-11-17T10:00:00Z", updated_at: "2026-11-17T10:00:00Z", ingestion_metadata: meta }) as unknown as RagDocument;

const docs = [
  doc("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "AlphaTrade_Upload_Test"),
  doc("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "Captured by agent", { agent_capture: true }),
];

beforeEach(() => {
  vi.spyOn(api.knowledge, "listDocuments").mockResolvedValue({ items: docs, total: 2, limit: 50, offset: 0 } as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("lists my documents with open and a two-step delete, without IDs or index jargon", async () => {
  const remove = vi.spyOn(api.knowledge, "deleteDocument").mockResolvedValue(undefined);
  render(<DocumentsPanel documentId={null} savedId={null} />);
  const list = await screen.findByTestId("documents-list");
  expect(within(list).getAllByTestId("document-row")).toHaveLength(1);
  expect(within(list).getByRole("link", { name: "AlphaTrade_Upload_Test" })).toHaveAttribute(
    "href", "/agent?tab=documents&document_id=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  expect(list).not.toHaveTextContent(/index|Stored|Chunk/i);
  expectNoIdentifiers();
  vi.mocked(api.knowledge.listDocuments).mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 } as never);
  fireEvent.click(within(list).getByRole("button", { name: /Delete/ }));
  expect(remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"));
  expect(await screen.findByText("No documents yet. Attach one in the chat.")).toBeInTheDocument();
});

it("keeps the row and says so when delete fails", async () => {
  vi.spyOn(api.knowledge, "deleteDocument").mockRejectedValue(new Error("boom"));
  render(<DocumentsPanel documentId={null} savedId={null} />);
  fireEvent.click(await screen.findByRole("button", { name: /Delete/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Delete failed.");
  expect(screen.getByRole("link", { name: "AlphaTrade_Upload_Test" })).toBeInTheDocument();
});

it("opens one document's text with a way back", async () => {
  vi.spyOn(api.knowledge, "listChunks").mockResolvedValue({
    items: [{ id: "c1", document_id: "d", chunk_ordinal: 0, title: "AlphaTrade_Upload_Test", content: "Wait for the 15m close." }],
    total: 1, limit: 50, offset: 0,
  } as never);
  render(<DocumentsPanel documentId="d" savedId={null} />);
  expect(await screen.findByText("Wait for the 15m close.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "← Documents" })).toHaveAttribute("href", "/agent?tab=documents");
});
