import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SavedReceipt } from "./SavedReceipt";
import { ENTRY_CATEGORIES, ENTRY_LABELS, savedEntries, type SavedEntry } from "@/lib/api/saved-entries";

vi.mock("@/lib/api/saved-entries", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api/saved-entries")>(),
  savedEntries: { update: vi.fn() },
}));

const entry = (category: SavedEntry["category"]): SavedEntry => ({
  id: "private-record", title: "Wait for confirmation", summary: "I wait for a closed confirmation before entry.",
  category, revision: 3, conversation_id: "private-conversation", source_message_ids: ["private-source"],
  source_document_id: null, trade_id: null, tags: [], original_text: "My rule: wait for confirmation.",
  created_at: "2026-10-11", updated_at: "2026-10-11", draft: null, undone: false,
});

describe("saved conversation receipts", () => {
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it.each(ENTRY_CATEGORIES)("links the durable %s summary to its owned category", category => {
    const record = entry(category);
    render(<SavedReceipt conversationId="private-conversation" capture={{ saved_entries: [record], status: "saved" }} />);
    expect(screen.getByText(`Saved to ${ENTRY_LABELS[category]}`)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: `Open ${record.title} in ${ENTRY_LABELS[category]}` });
    expect(link).toHaveAttribute("href", category === "journal"
      ? "/journal?saved=private-record"
      : `/journal?tab=knowledge&category=${category}&saved=private-record`);
    expect(screen.getAllByText(record.summary)[0]).toHaveClass("line-clamp-2");
    expect(screen.getByText("Saved note details").closest("details")).not.toHaveAttribute("open");
    expect(screen.queryByText(record.id)).not.toBeInTheDocument();
    expect(screen.queryByText(record.conversation_id)).not.toBeInTheDocument();
    expect(savedEntries.update).not.toHaveBeenCalled();
  });

  it("keeps revision checks when deliberately moving a note into Knowledge lessons", async () => {
    const record = entry("rules");
    vi.mocked(savedEntries.update).mockResolvedValue({ ...record, category: "lessons", revision: 4 });
    render(<SavedReceipt conversationId="private-conversation" capture={{ saved_entries: [record], status: "saved" }} />);
    fireEvent.click(screen.getByText("Saved note details"));
    fireEvent.click(screen.getByRole("button", { name: "Reclassify" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Reclassify saved entry" }), { target: { value: "lessons" } });
    await waitFor(() => expect(savedEntries.update).toHaveBeenCalledWith(record.id, { expected_revision: 3, category: "lessons" }));
    expect(await screen.findByRole("link", { name: `Open ${record.title} in Lessons` })).toHaveAttribute("href", "/journal?tab=knowledge&category=lessons&saved=private-record");
  });
});
