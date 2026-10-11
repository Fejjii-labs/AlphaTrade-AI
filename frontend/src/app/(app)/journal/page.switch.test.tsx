import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import JournalPage from "./page";

const search = new URLSearchParams();

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "fixture-user" },
    organization: { id: "fixture-org" },
  }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/journal",
  useSearchParams: () => search,
}));

vi.mock("@/components/journal/JournalHubScreen", () => ({
  JournalHubScreen: () => <div>Hub record</div>,
}));

vi.mock("@/components/journal/JournalKnowledgeWorkspace", () => ({
  JournalKnowledgeWorkspace: () => <div>Unified journal</div>,
}));

vi.mock("@/components/journal/JournalTradeScreen", () => ({
  JournalTradeScreen: ({ tradeId }: { tradeId: string }) => (
    <div>Exact trade {tradeId}</div>
  ),
}));

afterEach(() => {
  cleanup();
  for (const key of [...search.keys()]) search.delete(key);
});

describe("Journal page switch", () => {
  it("opens the trader journal by default", () => {
    render(<JournalPage />);
    expect(screen.getByText("Unified journal")).toBeInTheDocument();
  });

  it("keeps the record hub for view=record and deep links", () => {
    search.set("view", "record");
    const { rerender } = render(<JournalPage />);
    expect(screen.getByText("Hub record")).toBeInTheDocument();

    search.delete("view");
    search.set("proposal_id", "prop-1");
    rerender(<JournalPage />);
    expect(screen.getByText("Hub record")).toBeInTheDocument();
  });
});

it.each(["trade_id", "trade"])(
  "resolves %s directly to exact canonical trade detail",
  (query) => {
    search.set(query, "canonical-trade");
    render(<JournalPage />);
    expect(screen.getByText("Exact trade canonical-trade")).toBeInTheDocument();
    expect(screen.queryByText("Hub record")).not.toBeInTheDocument();
  },
);
