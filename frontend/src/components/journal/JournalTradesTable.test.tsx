import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CanonicalJournalTradeListItem } from "@/lib/api/types";
import { expectNoIdentifiers } from "@/test/no-identifiers";

import { JournalTradesTable } from "./JournalTradesTable";

const base: CanonicalJournalTradeListItem = {
  id: "t-win",
  symbol: "BTCUSDT",
  timeframe: "15m",
  direction: "long",
  status: "closed",
  result: "win",
  source: "paper_execution",
  exchange: "BLOFIN_DEMO",
  execution_lifecycle_id: "life",
  strategy_label: "Nested",
  entry_time: "2026-11-01T10:00:00Z",
  exit_time: "2026-11-01T12:00:00Z",
  entry_price: "67000.12345678",
  exit_price: "67500.5",
  fees: "0.12345678",
  net_pnl: "12.5",
};

const trades: CanonicalJournalTradeListItem[] = [
  base,
  { ...base, id: "t-loss", symbol: "ETHUSDT", direction: "short", source: "blofin_demo_manual", net_pnl: "-3.25" },
  {
    ...base,
    id: "t-open",
    symbol: "SOLUSDT",
    status: "open",
    source: "manual_demo_test",
    strategy_label: null,
    exit_time: null,
    exit_price: null,
    fees: null,
    net_pnl: null,
  },
];

afterEach(cleanup);

describe("JournalTradesTable", () => {
  it("renders one row per trade with entry, exit, fees and net P&L", () => {
    render(<JournalTradesTable trades={trades} onOpen={() => {}} />);
    const table = screen.getByTestId("data-table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Date", "Coin", "Side", "By", "Entry", "Exit", "Fees", "Net P&L", "Journal",
    ]);
    const win = screen.getByTestId("data-table-row-t-win");
    expect(within(win).getByText("BTCUSDT")).toBeInTheDocument();
    expect(within(win).getByText("Nested")).toBeInTheDocument();
    expect(within(win).getByText("Long")).toBeInTheDocument();
    expect(within(win).getByText("Bot")).toBeInTheDocument();
    expect(within(win).getByTestId("trade-fees")).toHaveTextContent("0.12");
    expect(win).not.toHaveTextContent("12345678");
    expect(within(screen.getByTestId("data-table-row-t-loss")).getByText("Mine")).toBeInTheDocument();
    expect(within(screen.getByTestId("data-table-row-t-open")).getByText("Open")).toBeInTheDocument();
    expectNoIdentifiers();
  });

  it("colours only signed P&L and shows a dash when there is none", () => {
    render(<JournalTradesTable trades={trades} onOpen={() => {}} />);
    const win = within(screen.getByTestId("data-table-row-t-win")).getByTestId("trade-net-pnl");
    const loss = within(screen.getByTestId("data-table-row-t-loss")).getByTestId("trade-net-pnl");
    expect(win).toHaveTextContent("+12.50");
    expect(win).toHaveClass("text-positive");
    expect(loss).toHaveTextContent("−3.25");
    expect(loss).toHaveClass("text-negative");
    const open = screen.getByTestId("data-table-row-t-open");
    expect(within(open).queryByTestId("trade-net-pnl")).not.toBeInTheDocument();
    expect(within(open).getByText("No strategy")).toBeInTheDocument();
  });

  it("links every row to its journal page with a 44px target and remembers before navigation", () => {
    const onOpen = vi.fn();
    render(<JournalTradesTable trades={trades} onOpen={onOpen} />);
    const links = screen.getAllByRole("link", { name: /^Journal/ });
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/journal?trade_id=t-win",
      "/journal?trade_id=t-loss",
      "/journal?trade_id=t-open",
    ]);
    expect(links[0]).toHaveClass("min-h-11", "min-w-11");
    fireEvent.click(links[1]!);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("scrolls horizontally inside its container on narrow screens", () => {
    render(<JournalTradesTable trades={trades} onOpen={() => {}} />);
    expect(screen.getByTestId("data-table").parentElement).toHaveClass("overflow-x-auto");
  });
});
