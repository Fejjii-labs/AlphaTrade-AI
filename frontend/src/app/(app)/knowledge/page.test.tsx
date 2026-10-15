import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Page from "./page";
import { api } from "@/lib/api";
import { savedEntries, type SavedEntry } from "@/lib/api/saved-entries";
const state = vi.hoisted(() => ({
  params: new URLSearchParams(),
  pathname: "/knowledge",
  replace: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => state.pathname,
  useSearchParams: () => state.params,
  useRouter: () => ({ replace: state.replace }),
}));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u" }, organization: { id: "o" } }),
}));
const entry: SavedEntry = {
  id: "note",
  category: "rules",
  title: "Confirmation rule",
  summary: "I wait for a closed confirmation.",
  original_text: "My rule: wait for a closed confirmation.",
  conversation_id: "conversation",
  source_message_ids: ["message"],
  source_document_id: "document",
  trade_id: null,
  tags: [],
  draft: null,
  revision: 1,
  undone: false,
  created_at: "2026-10-09",
  updated_at: "2026-10-09",
};
beforeEach(() => {
  state.params = new URLSearchParams();
  state.pathname = "/knowledge";
  state.replace.mockReset();
  sessionStorage.clear();
  vi.spyOn(savedEntries, "list").mockResolvedValue({
    items: [entry],
    total: 1,
  });
  vi.spyOn(savedEntries, "get").mockResolvedValue(entry);
  vi.spyOn(savedEntries, "update").mockImplementation(async (_id, body) => ({
    ...entry,
    ...body,
    revision: 2,
  }));
  vi.spyOn(api.knowledge, "listDocuments").mockResolvedValue({
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
  });
  vi.spyOn(api.knowledge, "listChunks").mockResolvedValue({
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
  });
  vi.spyOn(api.journal, "listTrades").mockResolvedValue({
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
  });
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
it("consolidates Knowledge categories and keeps original evidence on demand", async () => {
  render(<Page />);
  expect(await screen.findByText("Confirmation rule")).toBeInTheDocument();
  expect(
    screen.getByRole("navigation", { name: "Journal and Knowledge" }),
  ).toHaveTextContent("JournalKnowledge");
  expect(screen.getByLabelText("Knowledge category")).toHaveTextContent(
    "RulesStrategiesNews & AnalysisLessons",
  );
  expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute(
    "href",
    "/journal?tab=knowledge&category=rules&saved=note",
  );
  expect(screen.queryByText(entry.original_text)).not.toBeInTheDocument();
  expect(api.knowledge.listChunks).not.toHaveBeenCalled();
  expect(savedEntries.list).toHaveBeenCalledWith({
    view: "knowledge",
    category: undefined,
    q: "",
    limit: 50,
    offset: 0,
  });
});
it("keeps category and search in the return URL", async () => {
  state.params = new URLSearchParams(
    "tab=knowledge&category=rules&q=confirmation",
  );
  render(<Page />);
  await screen.findByText("Confirmation rule");
  fireEvent.click(screen.getByRole("link", { name: "Open" }));
  expect(
    JSON.parse(sessionStorage.getItem("alphatrade.journal.return:o:u")!),
  ).toMatchObject({
    href: "/knowledge?tab=knowledge&category=rules&q=confirmation",
  });
  fireEvent.change(screen.getByLabelText("Search entries"), {
    target: { value: "closed" },
  });
  expect(state.replace).toHaveBeenCalledWith(
    "/knowledge?tab=knowledge&category=rules&q=closed",
    { scroll: false },
  );
});
it("loads an exact saved record, corrects it with a revision check and retains source links", async () => {
  state.params.set("saved", "note");
  render(<Page />);
  expect(
    await screen.findByRole("heading", { name: "Confirmation rule" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Original conversation" }),
  ).toHaveAttribute("href", "/agent?conversation=conversation");
  expect(
    screen.getByRole("link", { name: "Original document" }),
  ).toHaveAttribute("href", "/journal?tab=knowledge&document_id=document");
  fireEvent.click(screen.getByRole("button", { name: "Correct summary" }));
  fireEvent.change(screen.getByLabelText("Corrected summary"), {
    target: { value: "Wait for confirmation on close." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await waitFor(() =>
    expect(savedEntries.update).toHaveBeenCalledWith("note", {
      expected_revision: 1,
      summary: "Wait for confirmation on close.",
    }),
  );
});
it("retains a failed correction and does not claim it was saved", async () => {
  state.params.set("saved", "note");
  vi.mocked(savedEntries.update).mockRejectedValue(new Error("Conflict"));
  render(<Page />);
  await screen.findByRole("heading", { name: "Confirmation rule" });
  fireEvent.click(screen.getByRole("button", { name: "Correct summary" }));
  fireEvent.change(screen.getByLabelText("Corrected summary"), {
    target: { value: "Retained correction" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Save failed");
  expect(screen.getByLabelText("Corrected summary")).toHaveValue(
    "Retained correction",
  );
});
it("shows unavailable sources independently while preserving readable notes", async () => {
  vi.mocked(api.knowledge.listDocuments).mockRejectedValue(
    new Error("unavailable"),
  );
  render(<Page />);
  expect(await screen.findByText("Confirmation rule")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Documents unavailable");
});
it("retrieves an exact linked document without substituting another source", async () => {
  state.params.set("document_id", "exact-document");
  vi.mocked(api.knowledge.listChunks).mockResolvedValue({
    items: [],
    total: 80,
    limit: 50,
    offset: 0,
  });
  render(<Page />);
  await waitFor(() =>
    expect(api.knowledge.listChunks).toHaveBeenCalledWith({
      document_id: "exact-document",
      limit: 50,
      offset: 0,
    }),
  );
  expect(
    await screen.findByRole("link", { name: /Back to Knowledge/ }),
  ).toBeInTheDocument();
  expect(screen.queryByText("Confirmation rule")).not.toBeInTheDocument();
  fireEvent.click(
    within(
      screen.getByRole("navigation", { name: "Original document pages" }),
    ).getByRole("button", { name: "Next" }),
  );
  expect(state.replace).toHaveBeenCalledWith(
    "/knowledge?document_id=exact-document&chunk_page=1",
    { scroll: false },
  );
});
it("keeps a Back path to Knowledge when an exact saved entry is unavailable", async () => {
  state.params.set("saved", "missing");
  vi.mocked(savedEntries.get).mockRejectedValue(new Error("Missing"));
  render(<Page />);
  await screen.findByText("Saved entry unavailable.");
  expect(screen.getByRole("link", { name: /Back to Knowledge/ })).toHaveAttribute("href", "/journal?tab=knowledge");
});
it("restores the stored filtered list from an unavailable saved entry", async () => {
  state.params.set("saved", "missing");
  sessionStorage.setItem("alphatrade.journal.return:o:u", JSON.stringify({ href: "/journal?tab=knowledge&category=rules&q=close", scroll: 480 }));
  vi.mocked(savedEntries.get).mockRejectedValue(new Error("Missing"));
  render(<Page />);
  await screen.findByText("Saved entry unavailable.");
  expect(screen.getByRole("link", { name: /Back to Knowledge/ })).toHaveAttribute("href", "/journal?tab=knowledge&category=rules&q=close");
});
it("resolves an unavailable exact document independently of stalled library lists", async () => {
  state.params.set("document", "missing-for-readiness");
  vi.mocked(savedEntries.list).mockImplementation(() => new Promise(() => {}));
  vi.mocked(api.knowledge.listDocuments).mockImplementation(() => new Promise(() => {}));
  vi.mocked(api.knowledge.listChunks).mockRejectedValue(new Error("Document unavailable"));
  render(<Page />);
  expect(await screen.findByTestId("knowledge-document-stale")).toBeVisible();
  expect(savedEntries.list).not.toHaveBeenCalled();
  expect(api.knowledge.listDocuments).not.toHaveBeenCalled();
  expect(api.knowledge.listChunks).toHaveBeenCalledWith({
    document_id: "missing-for-readiness", limit: 50, offset: 0,
  });
});
it("paginates preserved notes and sources without silently truncating history", async () => {
  vi.mocked(savedEntries.list).mockResolvedValue({ items: [entry], total: 80 });
  render(<Page />);
  await screen.findByText("Confirmation rule");
  fireEvent.click(
    within(
      screen.getByRole("navigation", { name: "Saved notes pages" }),
    ).getByRole("button", { name: "Next" }),
  );
  expect(state.replace).toHaveBeenCalledWith("/knowledge?page=1", {
    scroll: false,
  });
});
it("shows personal reflections without requiring a trade and excludes simulation claims", async () => {
  state.pathname = "/journal";
  vi.mocked(savedEntries.list).mockResolvedValue({
    items: [{ ...entry, category: "journal" }],
    total: 1,
  });
  vi.mocked(api.journal.listTrades).mockResolvedValue({
    items: [
      {
        id: "simulation",
        symbol: "BTCUSDT",
        exchange: "internal",
        source: "manual",
        status: "closed",
        entry_time: "2026-10-09",
      },
    ] as never,
    total: 1,
    limit: 50,
    offset: 0,
  });
  render(<Page />);
  expect(await screen.findByText("Confirmation rule")).toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: "Open exact trade" }),
  ).not.toBeInTheDocument();
});

it("requires execution lifecycle provenance for default exchange activity", async () => {
  state.pathname = "/journal";
  const base = {
    symbol: "BTCUSDT",
    exchange: "BLOFIN_DEMO",
    source: "paper_execution",
    status: "closed",
    entry_time: "2026-10-09",
  };
  vi.mocked(api.journal.listTrades).mockResolvedValue({
    items: [
      { ...base, id: "unsupported-claim" },
      {
        ...base,
        id: "executed",
        execution_lifecycle_id: "projected-lifecycle",
      },
      {
        ...base,
        id: "direct-venue",
        source: "manual",
        execution_lifecycle_id: "irrelevant",
      },
      {
        ...base,
        id: "planned",
        status: "planned",
        execution_lifecycle_id: "projected-plan",
      },
    ] as never,
    total: 4,
    limit: 50,
    offset: 0,
  });
  render(<Page />);
  expect(
    await screen.findByRole("link", { name: "Open exact trade" }),
  ).toHaveAttribute("href", "/journal?trade_id=executed");
  expect(
    screen.getAllByRole("link", { name: "Open exact trade" }),
  ).toHaveLength(1);
});

it("hides pagination for empty or single-page libraries", async () => {
  render(<Page />);
  await screen.findByText("Confirmation rule");
  expect(screen.queryByRole("navigation", { name: "Saved notes pages" })).not.toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "Documents pages" })).not.toBeInTheDocument();
  expect(screen.getByTestId("knowledge-filters")).toContainElement(screen.getByLabelText("Knowledge category"));
});
it("retains independent document paging and a way back from a stale empty page", async () => {
  state.params.set("page", "1");
  vi.mocked(savedEntries.list).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.knowledge.listDocuments).mockResolvedValue({ items: [], total: 80, limit: 50, offset: 0 });
  render(<Page />);
  const documents = await screen.findByRole("navigation", { name: "Documents pages" });
  fireEvent.click(within(documents).getByRole("button", { name: "Next" }));
  expect(state.replace).toHaveBeenCalledWith("/knowledge?page=1&source_page=1", { scroll: false });
  const notes = screen.getByRole("navigation", { name: "Saved notes pages" });
  expect(within(notes).getByRole("button", { name: "Next" })).toBeDisabled();
  fireEvent.click(within(notes).getByRole("button", { name: "Previous" }));
  expect(state.replace).toHaveBeenCalledWith("/knowledge?page=0", { scroll: false });
});
