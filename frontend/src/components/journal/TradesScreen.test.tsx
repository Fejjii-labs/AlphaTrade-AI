import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import JournalPage from "@/app/(app)/journal/page";
import { api } from "@/lib/api";
import { readNativeActivity } from "@/lib/api/blofin-activity";
import { savedEntries } from "@/lib/api/saved-entries";
import type { BloFinSyncSnapshotItem, CanonicalJournalTradeListItem } from "@/lib/api/types";
import { activityOrganization, activityPage } from "@/test/native-activity-fixtures";
import { ageText, syncStatusText } from "./BloFinSyncLine";

let identity = { user: { id: "reader-1" }, organization: { id: activityOrganization } };
const params = new URLSearchParams();
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => identity }));
vi.mock("@/lib/api/blofin-activity", () => ({ readNativeActivity: vi.fn() }));
vi.mock("next/navigation", () => ({ useSearchParams: () => params, usePathname: () => "/journal", useRouter: () => ({ replace: vi.fn() }) }));

const trade = (over: Partial<CanonicalJournalTradeListItem>): CanonicalJournalTradeListItem => ({
  id: "t", symbol: "BTCUSDT", timeframe: "15m", direction: "long", status: "closed", result: "win",
  source: "paper_execution", exchange: "BLOFIN_DEMO", execution_lifecycle_id: "life",
  entry_time: "2026-11-01T10:00:00Z", exit_time: "2026-11-01T12:00:00Z", net_pnl: "5", fees: "0.2", ...over,
});

const snapshot = (over: Partial<BloFinSyncSnapshotItem> = {}) => ({
  synced_at: new Date(Date.now() - 2 * 60_000).toISOString(), health_status: "ok", ...over,
}) as BloFinSyncSnapshotItem;

beforeEach(() => {
  identity = { user: { id: "reader-1" }, organization: { id: activityOrganization } };
  for (const key of [...params.keys()]) params.delete(key);
  vi.mocked(readNativeActivity).mockReset().mockResolvedValue(activityPage());
  vi.spyOn(savedEntries, "list").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(api.exchange, "blofinSyncLatest").mockResolvedValue(snapshot());
  vi.spyOn(api.journal, "listTrades").mockImplementation(async (query) => {
    const bySource: Record<string, CanonicalJournalTradeListItem[]> = {
      paper_execution: [
        trade({ id: "bot-1", symbol: "ETHUSDT", exit_time: "2026-11-02T12:00:00Z" }),
        trade({ id: "simulator", symbol: "SIMULATED", exchange: "INTERNAL" }),
      ],
      blofin_demo_manual: [trade({ id: "mine-1", symbol: "SOLUSDT", source: "blofin_demo_manual", exchange: "BLOFIN_DEMO", execution_lifecycle_id: null, exit_time: "2026-11-03T12:00:00Z" })],
      manual_demo_test: [],
    };
    const items = bySource[query?.source ?? ""] ?? [];
    return { items, total: items.length, limit: 200, offset: 0 };
  });
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("lists bot and my BloFin demo trades, newest first, without simulator rows or the activity wall", async () => {
  render(<JournalPage />);
  const table = await screen.findByTestId("journal-trades-table");
  expect(screen.getByRole("heading", { level: 1, name: "Trades" })).toBeInTheDocument();
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows.map((r) => within(r).getAllByRole("cell")[1]!.textContent)).toEqual([
    expect.stringContaining("SOLUSDT"),
    expect.stringContaining("ETHUSDT"),
  ]);
  expect(screen.queryByText(/SIMULATED/)).not.toBeInTheDocument();
  expect(api.journal.listTrades).toHaveBeenCalledWith({ source: "paper_execution", limit: 200 });
  expect(api.journal.listTrades).toHaveBeenCalledWith({ source: "blofin_demo_manual", limit: 200 });
  expect(screen.queryByText(/History coverage/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Historical records/)).not.toBeInTheDocument();
  expect(screen.getByTestId("blofin-sync-line")).toHaveTextContent("BloFin · Synced 2 min ago");
  // Fills load only when expanded.
  expect(readNativeActivity).not.toHaveBeenCalled();
});

it("shows fills with plain contract numbers when expanded", async () => {
  render(<JournalPage />);
  const fills = await screen.findByTestId("blofin-fills");
  fills.setAttribute("open", "");
  fireEvent(fills, new Event("toggle"));
  const row = await screen.findByTestId("blofin-fill-row");
  expect(row).toHaveTextContent("BTC-USDT · Buy");
  expect(row).toHaveTextContent("0.1235 @ 67,000.1235");
  expect(row).not.toHaveTextContent("123456789");
});

it("syncs BloFin and reloads the list", async () => {
  vi.spyOn(api.exchange, "blofinSync").mockResolvedValue({ snapshot: snapshot(), note: "" });
  render(<JournalPage />);
  await screen.findByTestId("journal-trades-table");
  vi.mocked(api.journal.listTrades).mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Sync" }));
  await waitFor(() => expect(api.exchange.blofinSync).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(api.journal.listTrades).toHaveBeenCalled());
});

it("says Sync failed in one line when the sync call fails", async () => {
  vi.spyOn(api.exchange, "blofinSync").mockRejectedValue(new Error("down"));
  render(<JournalPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Sync" }));
  await waitFor(() => expect(screen.getByTestId("blofin-sync-line")).toHaveTextContent("BloFin · Sync failed"));
});

it("formats sync age and state", () => {
  const now = Date.parse("2026-11-19T12:00:00Z");
  expect(ageText("2026-11-19T11:59:40Z", now)).toBe("just now");
  expect(ageText("2026-11-19T11:58:00Z", now)).toBe("2 min ago");
  expect(ageText("2026-11-19T09:00:00Z", now)).toBe("3 h ago");
  expect(ageText("2026-11-17T12:00:00Z", now)).toBe("2 d ago");
  expect(syncStatusText(null, false)).toBe("Not synced yet");
  expect(syncStatusText(snapshot({ health_status: "unavailable" }), false)).toBe("Sync failed");
});

it("clears the list when the authenticated user changes", async () => {
  const { rerender } = render(<JournalPage />);
  await screen.findByText("ETHUSDT");
  identity = { ...identity, user: { id: "reader-2" } };
  vi.mocked(api.journal.listTrades).mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
  rerender(<JournalPage />);
  await screen.findByText("No trades yet.");
  expect(screen.queryByText("ETHUSDT")).not.toBeInTheDocument();
});

it("shows Trades and Stats as URL tabs; Stats offers only Bot and My BloFin trades", async () => {
  const metrics = (over: Record<string, unknown> = {}) => ({
    trade_count: 4, wins: 3, losses: 1, breakeven: 0, win_rate: 0.75, pnl_sample_count: 4,
    net_pnl_total: "42.5", gross_pnl_total: "45", expectancy: null, average_winner: null, average_loser: null,
    profit_factor: 2.5, r_sample_count: 4, average_r: 0.8, cost_sample_count: 0, fees_total: null, funding_total: null,
    slippage_total: null, total_costs: null, mfe_sample_count: 0, average_mfe_amount: null, mae_sample_count: 0,
    average_mae_amount: null, capture_sample_count: 0, available_profit_total: null, realized_on_available_total: null,
    average_realized_vs_available_pct: null, confidence: "low", warnings: [], ...over,
  });
  vi.spyOn(api.journal, "statistics").mockImplementation(async (query) => ({
    group_by: query?.group_by ?? "overall", filters: {}, overall: metrics(), total_buckets: 1, limit: 50, offset: 0,
    truncated: false, max_rows: 1000, generated_at: "2026-10-27T10:00:00Z",
    buckets: query?.group_by === "symbol"
      ? [{ key: "BTCUSDT", label: "BTCUSDT", metrics: metrics({ trade_count: 4, net_pnl_total: "-3" }) }]
      : query?.group_by === "source"
        ? [
            { key: "backtest", label: "backtest", metrics: metrics({ trade_count: 2 }) },
            { key: "paper_execution", label: "paper_execution", metrics: metrics({ trade_count: 3 }) },
          ]
        : [{ key: "nested", label: "Nested BTC", metrics: metrics() }],
  }) as never);
  render(<JournalPage />);
  const tabs = screen.getByRole("navigation", { name: "Trades views" });
  expect([...tabs.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href"), a.getAttribute("aria-current")])).toEqual([
    ["Trades", "/journal", "page"],
    ["Stats", "/journal?tab=stats", null],
  ]);
  cleanup();
  vi.mocked(api.journal.listTrades).mockClear();
  params.set("tab", "stats");
  render(<JournalPage />);
  const stats = await screen.findByTestId("trade-stats");
  expect(screen.getByRole("link", { name: "Stats" })).toHaveAttribute("aria-current", "page");
  const picker = screen.getByRole("combobox", { name: "Stats source" });
  expect([...picker.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["Bot", "My BloFin trades"]);
  expect(stats).toHaveTextContent("Win rate75.0%");
  expect(stats).toHaveTextContent("Net P&L+42.50");
  const bySource = within(screen.getByRole("region", { name: "By source" }));
  expect(bySource.getByText("Bot")).toBeInTheDocument();
  expect(bySource.queryByText(/Backtest/i)).not.toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "By symbol" })).getByText("-3.00")).toHaveClass("text-negative");
  expect(api.journal.statistics).toHaveBeenCalledWith({ group_by: "strategy", source: "paper_execution" });
  vi.mocked(api.journal.statistics).mockClear();
  fireEvent.change(picker, { target: { value: "blofin_demo_manual" } });
  await waitFor(() => expect(api.journal.statistics).toHaveBeenCalledWith({ group_by: "strategy", source: "blofin_demo_manual" }));
  expect(api.journal.statistics).toHaveBeenCalledWith({ group_by: "symbol", source: "blofin_demo_manual" });
  expect(api.journal.listTrades).not.toHaveBeenCalled();
});

it("says Stats are unavailable instead of showing zeros", async () => {
  vi.spyOn(api.journal, "statistics").mockRejectedValue(new Error("down"));
  params.set("tab", "stats");
  render(<JournalPage />);
  expect(await screen.findByText("Stats unavailable.")).toBeInTheDocument();
});

it("offers New demo order in the Trades header on both tabs", async () => {
  render(<JournalPage />);
  expect(screen.getByRole("button", { name: "New demo order" })).toBeInTheDocument();
  cleanup();
  vi.spyOn(api.journal, "statistics").mockRejectedValue(new Error("down"));
  params.set("tab", "stats");
  render(<JournalPage />);
  expect(screen.getByRole("button", { name: "New demo order" })).toBeInTheDocument();
});

it("opens a saved note from ?saved= with a way back", async () => {
  vi.spyOn(savedEntries, "get").mockResolvedValue({
    id: "note-1", title: "Waited for the close", summary: "Good patience.", category: "journal", revision: 1,
    conversation_id: "c", source_message_ids: [], source_document_id: null, trade_id: null, tags: [],
    original_text: "x", created_at: "2026-11-01T10:00:00Z", updated_at: "2026-11-01T10:00:00Z", draft: null, undone: false,
  });
  params.set("saved", "note-1");
  render(<JournalPage />);
  expect(await screen.findByRole("heading", { name: "Waited for the close" })).toBeInTheDocument();
  expect(api.journal.listTrades).not.toHaveBeenCalled();
});

it("renders trades even if saved notes are still loading", async () => {
  vi.mocked(savedEntries.list).mockImplementation(() => new Promise(() => {}));
  render(<JournalPage />);
  expect(await screen.findByTestId("journal-trades-table")).toBeInTheDocument();
  expect(savedEntries.list).toHaveBeenCalledTimes(1);
});
