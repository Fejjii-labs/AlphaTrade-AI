import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import JournalPage from "./page";

const search = new URLSearchParams();
const router = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "fixture-user" },
    organization: { id: "fixture-org" },
  }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/journal",
  useSearchParams: () => search,
  useRouter: () => router,
}));

vi.mock("@/components/journal/TradesScreen", () => ({
  TradesScreen: () => <div>Trades list</div>,
}));

vi.mock("@/components/journal/JournalTradeScreen", () => ({
  JournalTradeScreen: ({ tradeId }: { tradeId: string }) => (
    <div>Exact trade {tradeId}</div>
  ),
}));

afterEach(() => {
  cleanup();
  router.replace.mockReset();
  for (const key of [...search.keys()]) search.delete(key);
});

describe("Journal page switch", () => {
  it("opens the trades list by default and for retired record links", () => {
    const { rerender } = render(<JournalPage />);
    expect(screen.getByText("Trades list")).toBeInTheDocument();
    search.set("view", "record");
    search.set("proposal_id", "prop-1");
    rerender(<JournalPage />);
    expect(screen.getByText("Trades list")).toBeInTheDocument();
  });

  it("sends old Knowledge links to Agent → Documents with their query", () => {
    search.set("tab", "knowledge");
    search.set("document_id", "doc-1");
    render(<JournalPage />);
    expect(router.replace).toHaveBeenCalledWith("/agent?tab=documents&document_id=doc-1");
    expect(screen.queryByText("Trades list")).not.toBeInTheDocument();
  });
});

it.each(["trade_id", "trade"])(
  "resolves %s directly to exact canonical trade detail",
  (query) => {
    search.set(query, "canonical-trade");
    render(<JournalPage />);
    expect(screen.getByText("Exact trade canonical-trade")).toBeInTheDocument();
    expect(screen.queryByText("Trades list")).not.toBeInTheDocument();
  },
);
