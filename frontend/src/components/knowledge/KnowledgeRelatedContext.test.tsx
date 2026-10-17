import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgeRelatedContext } from "./KnowledgeRelatedContext";
import type { LessonCandidate, RagDocument } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({ candidate: vi.fn(), list: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: {
    lessons: { getCandidate: mocks.candidate, listCandidates: mocks.list },
  },
}));
const document = (
  source_uri: string,
  source_type = "review_note",
): RagDocument => ({
  id: "doc",
  title: "Stored knowledge",
  source_type: source_type as RagDocument["source_type"],
  source_uri,
  version: 1,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
});
const lesson = (changes: Partial<LessonCandidate> = {}): LessonCandidate => ({
  id: "lesson-1",
  organization_id: "org",
  user_id: "user",
  source_type: "coaching",
  lesson_text: "Wait for the close",
  mistake_type: "early_entry",
  severity: "low",
  status: "accepted",
  created_at: "2026-10-01T10:00:00Z",
  related_strategy_id: "strategy-1",
  related_journal_entry_id: "entry-1",
  ...changes,
});
beforeEach(() => {
  mocks.candidate.mockReset().mockResolvedValue(lesson());
  mocks.list
    .mockReset()
    .mockResolvedValue({
      items: [
        lesson(),
        lesson({
          id: "unrelated",
          lesson_text: "Unrelated lesson",
          related_strategy_id: "other",
          related_journal_entry_id: "other",
        }),
      ],
      total: 3,
    });
});
afterEach(cleanup);

describe("canonical Knowledge relationships", () => {
  it("resolves lesson strategy and journal links from stored fields", async () => {
    render(
      <KnowledgeRelatedContext document={document("lesson://lesson-1")} />,
    );
    expect(await screen.findByText("Wait for the close")).toBeInTheDocument();
    expect(mocks.candidate).toHaveBeenCalledWith("lesson-1");
    expect(screen.getByRole("link", { name: "Strategy" })).toHaveAttribute(
      "href",
      "/strategy-lab/strategy-1",
    );
    expect(screen.getByRole("link", { name: "Journal entry" })).toHaveAttribute(
      "href",
      "/journal?entry=entry-1",
    );
  });
  it.each([
    ["strategy://strategy-1/v2", "strategy_template"],
    ["journal://entry-1", "trade_journal"],
  ])(
    "matches lessons using typed stored relationships: %s",
    async (uri, source) => {
      render(<KnowledgeRelatedContext document={document(uri, source)} />);
      expect(
        await screen.findByRole("link", { name: "Wait for the close" }),
      ).toHaveAttribute("href", "/lessons?candidate=lesson-1");
      expect(screen.queryByText("Unrelated lesson")).not.toBeInTheDocument();
      expect(
        screen.getByRole("link", { name: "Related journal entry" }),
      ).toHaveAttribute("href", "/journal?entry=entry-1");
      expect(
        screen.getByText(/Checked 2 of 3 accepted lessons/),
      ).toBeInTheDocument();
    },
  );
  it("does not infer journal entry IDs from canonical trade IDs or free text", () => {
    const { rerender } = render(
      <KnowledgeRelatedContext
        document={document("journal-trade://trade-1", "trade_journal")}
      />,
    );
    rerender(
      <KnowledgeRelatedContext
        document={document("strategy-1", "strategy_template")}
      />,
    );
    expect(mocks.candidate).not.toHaveBeenCalled();
    expect(mocks.list).not.toHaveBeenCalled();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
  it("retries failed lesson reads and keeps source failures distinct from no matches", async () => {
    mocks.candidate.mockRejectedValueOnce(new Error("offline"));
    render(
      <KnowledgeRelatedContext document={document("lesson://lesson-1")} />,
    );
    await screen.findByText("Related lesson unavailable.");
    fireEvent.click(screen.getByRole("button", { name: "Retry lesson" }));
    await screen.findByText("Wait for the close");
  });
});
