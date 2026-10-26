import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgeStorePanel } from "@/components/knowledge/KnowledgeStorePanel";
import { KnowledgeDocumentCard } from "@/components/knowledge/KnowledgeDocumentCard";
import { api } from "@/lib/api";
import type { FileImportPreview } from "@/lib/api/types";

vi.mock("@/lib/api", () => ({
  api: {
    knowledge: { ingest: vi.fn(), previewFile: vi.fn(), importFile: vi.fn() },
  },
}));

const preview: FileImportPreview = {
  filename: "rules.txt",
  title: "rules",
  source_type: "trading_playbook",
  media_type: "text/plain",
  byte_size: 50,
  raw_content_hash: "a".repeat(64),
  extracted_text_hash: "b".repeat(64),
  extracted_text: "Synthetic rules. Risk BLOCK remains authoritative.",
  extracted_characters: 50,
  warnings: [],
  preview_receipt: "synthetic-preview-receipt",
  expires_at: "2026-10-05T20:00:00Z",
  saved: false,
  vector_index_status: "not_started",
};
const stored = {
  document_id: "synthetic-doc",
  source_hash: "source-hash",
  chunk_count: 2,
  duplicate: false,
  version: 1,
  vector_backend: "qdrant",
  fallback_used: false,
  vector_index_status: "upsert_acknowledged" as const,
  sql_chunks_stored: true,
};

function chooseFile() {
  fireEvent.click(screen.getByRole("button", { name: "Import a file" }));
  const file = new File([preview.extracted_text], "rules.txt", {
    type: "text/plain",
  });
  fireEvent.change(screen.getByLabelText("Document file"), {
    target: { files: [file] },
  });
  return file;
}

describe("Knowledge file preview and explicit save", () => {
  afterEach(() => {
    cleanup();
    vi.resetAllMocks();
  });

  it("previews without ingestion and saves only after the user reviews the extracted text", async () => {
    vi.mocked(api.knowledge.previewFile).mockResolvedValue(preview);
    vi.mocked(api.knowledge.importFile).mockResolvedValue(stored);
    const refreshed = vi.fn();
    render(<KnowledgeStorePanel onStored={refreshed} />);
    const file = chooseFile();
    expect(
      screen.getByRole("button", { name: "Save previewed file" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Preview file" }));
    expect(await screen.findByLabelText("Extracted text preview")).toHaveValue(
      preview.extracted_text,
    );
    expect(
      screen.getByText(/Nothing has been saved or indexed/),
    ).toBeInTheDocument();
    expect(api.knowledge.previewFile).toHaveBeenCalledWith(
      file,
      "rules",
      "trading_playbook",
    );
    expect(api.knowledge.importFile).not.toHaveBeenCalled();
    expect(api.knowledge.ingest).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Save previewed file" }),
    );
    expect(
      await screen.findByTestId("knowledge-ingest-success"),
    ).toHaveTextContent("Search index acknowledged by qdrant");
    expect(api.knowledge.importFile).toHaveBeenCalledWith(
      file,
      "rules",
      "trading_playbook",
      "synthetic-preview-receipt",
    );
    expect(refreshed).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByLabelText("Extracted text preview"),
    ).not.toBeInTheDocument();
  });

  it.each(["Title", "Category"])(
    "requires another preview when %s changes",
    async (label) => {
      vi.mocked(api.knowledge.previewFile).mockResolvedValue(preview);
      render(<KnowledgeStorePanel />);
      chooseFile();
      fireEvent.click(screen.getByRole("button", { name: "Preview file" }));
      await screen.findByLabelText("Extracted text preview");
      fireEvent.change(screen.getByLabelText(label), {
        target: { value: label === "Title" ? "Changed" : "general_note" },
      });
      expect(
        screen.queryByLabelText("Extracted text preview"),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Save previewed file" }),
      ).toBeDisabled();
      expect(api.knowledge.importFile).not.toHaveBeenCalled();
    },
  );

  it("shows scanned/malformed-file errors and never enables saving", async () => {
    vi.mocked(api.knowledge.previewFile).mockRejectedValue(
      new Error("PDF has no selectable text. OCR is unsupported."),
    );
    render(<KnowledgeStorePanel />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Preview file" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "OCR is unsupported",
    );
    expect(
      screen.getByRole("button", { name: "Save previewed file" }),
    ).toBeDisabled();
    expect(api.knowledge.importFile).not.toHaveBeenCalled();
  });

  it("reports duplicate local indexing without claiming hosted search readiness", async () => {
    vi.mocked(api.knowledge.previewFile).mockResolvedValue(preview);
    vi.mocked(api.knowledge.importFile).mockResolvedValue({
      ...stored,
      duplicate: true,
      vector_backend: "in-memory-vector",
      fallback_used: true,
    });
    render(<KnowledgeStorePanel />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Preview file" }));
    await screen.findByLabelText("Extracted text preview");
    fireEvent.click(
      screen.getByRole("button", { name: "Save previewed file" }),
    );
    const result = await screen.findByTestId("knowledge-ingest-success");
    expect(result).toHaveTextContent("Already stored document synthetic-doc");
    expect(result).toHaveTextContent(
      "hosted search readiness is not established",
    );
  });

  it("invalidates expired preview on save failure and lets the user review again", async () => {
    vi.mocked(api.knowledge.previewFile).mockResolvedValue(preview);
    vi.mocked(api.knowledge.importFile).mockRejectedValue(
      new Error("Preview expired. Preview again."),
    );
    render(<KnowledgeStorePanel />);
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Preview file" }));
    await screen.findByLabelText("Extracted text preview");
    fireEvent.click(
      screen.getByRole("button", { name: "Save previewed file" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Preview expired",
    );
    expect(
      screen.queryByLabelText("Extracted text preview"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save previewed file" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Preview file" })).toBeEnabled();
  });

  it("rejects oversized selections before preview", () => {
    render(<KnowledgeStorePanel />);
    fireEvent.click(screen.getByRole("button", { name: "Import a file" }));
    fireEvent.change(screen.getByLabelText("Document file"), {
      target: {
        files: [
          new File([new Uint8Array(5 * 1024 * 1024 + 1)], "large.txt", {
            type: "text/plain",
          }),
        ],
      },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("5 MiB");
    expect(api.knowledge.previewFile).not.toHaveBeenCalled();
  });

  it("preserves pasted ingestion and labels legacy indexing as unverified", async () => {
    vi.mocked(api.knowledge.ingest).mockResolvedValue({
      document_id: "pasted-doc",
      source_hash: "hash",
      chunk_count: 1,
      duplicate: false,
      version: 1,
    });
    render(<KnowledgeStorePanel />);
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Pasted note" },
    });
    fireEvent.change(screen.getByLabelText("Document text"), {
      target: { value: "Recorded text" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));
    expect(
      await screen.findByTestId("knowledge-ingest-success"),
    ).toHaveTextContent("indexing status is unverified");
    expect(api.knowledge.ingest).toHaveBeenCalledWith({
      title: "Pasted note",
      text: "Recorded text",
      source_type: "trading_playbook",
    });
    expect(api.knowledge.previewFile).not.toHaveBeenCalled();
    expect(api.knowledge.importFile).not.toHaveBeenCalled();
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
});
