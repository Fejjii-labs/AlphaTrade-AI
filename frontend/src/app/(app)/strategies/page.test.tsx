import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import StrategiesPage from "./page";
import { api } from "@/lib/api";
import { savedEntries, type SavedEntry } from "@/lib/api/saved-entries";
import { sessionCleared } from "@/lib/auth/session-events";

const state = vi.hoisted(() => ({
  params: new URLSearchParams(),
  identity: { user: { id: "user-a" }, organization: { id: "org-a" } } as {
    user: { id: string } | null; organization: { id: string } | null;
  },
}));
vi.mock("next/navigation", () => ({ useSearchParams: () => state.params }));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => state.identity }));
vi.mock("@/components/strategies/ExperimentsPanel", () => ({
  ExperimentsPanel: () => <section aria-label="Bounded experiments" />,
}));
vi.mock("@/components/settings/RiskSettingsSummary", () => ({
  RiskSettingsSummary: () => <p>Saved account limits</p>,
}));

const note: SavedEntry = {
  id: "persisted-strategy-note", title: "Wait for a close", summary: "Confirm a closed reclaim before entry.",
  category: "strategies", revision: 2, conversation_id: "source-conversation", source_message_ids: ["source-message"],
  source_document_id: null, trade_id: null, tags: [], original_text: "Strategy idea", created_at: "2026-10-11T10:00:00Z",
  updated_at: "2026-10-11T10:00:00Z", draft: null, undone: false,
};

beforeEach(() => {
  state.params = new URLSearchParams();
  state.identity = { user: { id: "user-a" }, organization: { id: "org-a" } };
  window.history.replaceState(null, "", "/strategies");
  vi.spyOn(api.strategyBrain, "overview").mockResolvedValue({ strategies: [], setups: [], watched_symbols: [], limitations: [], paper_only: true });
  vi.spyOn(api.strategies, "list").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  vi.spyOn(savedEntries, "list").mockResolvedValue({ items: [note], total: 1 });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("separates persisted captures from authored configurations with compact expandable details", async () => {
  render(<StrategiesPage />);
  const notes = await screen.findByTestId("captured-strategy-note");
  expect(savedEntries.list).toHaveBeenCalledWith({ category: "strategies", limit: 6 });
  expect(screen.getByRole("heading", { name: "Captured strategy notes" })).toBeInTheDocument();
  expect(notes).not.toHaveAttribute("open");
  expect(within(notes).getByText(note.title, { selector: "summary" })).toBeVisible();
  expect(within(notes).getByText(note.summary)).not.toBeVisible();
  fireEvent.click(within(notes).getByText(note.title, { selector: "summary" }));
  expect(within(notes).getByText(note.summary)).toBeVisible();
  expect(within(notes).getByRole("link", { name: "Open saved note" })).toHaveAttribute("href", "/journal?tab=knowledge&category=strategies&saved=persisted-strategy-note");
  expect(screen.getByRole("link", { name: "All strategy notes" })).toHaveAttribute("href", "/journal?tab=knowledge&category=strategies");
  expect(screen.getByRole("link", { name: "Discuss a strategy" })).toHaveAttribute("href", "/agent?discuss=strategy");
  expect(screen.queryByText(note.id)).not.toBeInTheDocument();
  expect(screen.queryByText(/Draft ·/)).not.toBeInTheDocument();
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
});

it("opens risk configuration when returning to its Strategies anchor", async () => {
  window.history.replaceState(null, "", "/strategies#risk");
  render(<StrategiesPage />);
  const policy = screen.getByText("Risk policy", { selector: "summary" }).closest("details");
  expect(policy).toHaveAttribute("open");
  expect(screen.getByRole("link", { name: "Configure account limits" })).toHaveAttribute("href", "/risk");
  await screen.findByTestId("captured-strategy-note");
});

it("keeps family details and their parent navigation separate from captured notes", async () => {
  state.params.set("family", "nested");
  render(<StrategiesPage />);
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Nested Continuation");
  expect(screen.getByRole("link", { name: /Back to Strategies/ })).toHaveAttribute("href", "/strategies");
  expect(screen.queryByTestId("captured-strategy-notes")).not.toBeInTheDocument();
  await waitFor(() => expect(api.strategyBrain.overview).toHaveBeenCalled());
});

it("labels unavailable saved notes independently without claiming an empty history", async () => {
  vi.mocked(savedEntries.list).mockRejectedValue(new Error("Unavailable"));
  render(<StrategiesPage />);
  expect(await screen.findByText("Captured notes unavailable.")).toBeInTheDocument();
  expect(screen.queryByText("No captured strategy notes yet.")).not.toBeInTheDocument();
  expect(screen.getAllByTestId("strategy-family")).toHaveLength(2);
});

it("cancels the rendered account scope before an old account read can complete", async () => {
  let finish!: (value: { items: SavedEntry[]; total: number }) => void;
  vi.mocked(savedEntries.list).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const { rerender } = render(<StrategiesPage />);
  state.identity = { user: { id: "user-b" }, organization: { id: "org-b" } };
  vi.mocked(savedEntries.list).mockResolvedValue({ items: [], total: 0 });
  rerender(<StrategiesPage />);
  await screen.findByText("No captured strategy notes yet.");
  await act(async () => finish({ items: [note], total: 1 }));
  expect(screen.queryByText(note.title)).not.toBeInTheDocument();
  expect(savedEntries.list).toHaveBeenCalledTimes(2);
});

it("clears local notes on a same-account session invalidation and skips unauthenticated reads", async () => {
  const { rerender } = render(<StrategiesPage />);
  await screen.findByTestId("captured-strategy-note");
  vi.mocked(savedEntries.list).mockImplementation(() => new Promise(() => {}));
  act(() => sessionCleared());
  expect(screen.queryByTestId("captured-strategy-note")).not.toBeInTheDocument();
  state.identity = { user: null, organization: null };
  rerender(<StrategiesPage />);
  expect(screen.queryByTestId("trader-strategies")).not.toBeInTheDocument();
  expect(savedEntries.list).toHaveBeenCalledTimes(2);
});
