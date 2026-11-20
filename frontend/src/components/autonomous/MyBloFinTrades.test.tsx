import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { CanonicalJournalTradeListItem } from "@/lib/api/types";

import { MyBloFinTrades } from "./MyBloFinTrades";

vi.mock("@/components/journal/NewDemoOrder", () => ({ NewDemoOrder: () => <button type="button">New demo order</button> }));

const trade = (id: string, over: Partial<CanonicalJournalTradeListItem> = {}): CanonicalJournalTradeListItem => ({
  id, symbol: "BTCUSDT", timeframe: "manual", direction: "long", status: "closed", result: "win",
  source: "blofin_demo_manual", entry_time: "2026-11-01T10:00:00Z", exit_time: "2026-11-01T12:00:00Z", net_pnl: "4.5", ...over,
});

beforeEach(() => {
  vi.spyOn(api.exchange, "blofinSyncLatest").mockResolvedValue({ synced_at: new Date().toISOString(), health_status: "ok" } as never);
  vi.spyOn(api.journal, "listTrades").mockImplementation(async (query) => {
    const items = query?.source === "blofin_demo_manual"
      ? [trade("m1", { symbol: "SOLUSDT", exit_time: "2026-11-03T12:00:00Z" }), trade("planned", { status: "planned" })]
      : [trade("a1", { source: "manual_demo_test", direction: "short", status: "open", exit_time: null, net_pnl: null })];
    return { items, total: items.length, limit: 5, offset: 0 };
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows my latest BloFin demo trades with links to their journal, a sync line and a new order button", async () => {
  render(<MyBloFinTrades />);
  const first = await screen.findByRole("link", { name: /SOLUSDT/ });
  expect(first).toHaveAttribute("href", "/journal?trade_id=m1");
  expect(first).toHaveTextContent("+4.50");
  expect(screen.getByRole("link", { name: /BTCUSDT · Short/ })).toHaveAttribute("href", "/journal?trade_id=a1");
  expect(screen.getByRole("link", { name: /BTCUSDT · Short/ })).toHaveTextContent("Open");
  expect(screen.queryByText(/planned/)).not.toBeInTheDocument();
  expect(screen.getByTestId("blofin-sync-line")).toHaveTextContent("BloFin · Synced just now");
  expect(screen.getByRole("button", { name: "New demo order" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "All trades" })).toHaveAttribute("href", "/journal");
  expect(api.journal.listTrades).toHaveBeenCalledWith({ source: "blofin_demo_manual", limit: 5 });
  expect(api.journal.listTrades).toHaveBeenCalledWith({ source: "manual_demo_test", limit: 5 });
});
