import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import JournalPage from "@/app/(app)/journal/page";
import { api } from "@/lib/api";
import { readNativeActivity } from "@/lib/api/blofin-activity";
import { savedEntries } from "@/lib/api/saved-entries";
import { activityOrganization, activityPage } from "@/test/native-activity-fixtures";

let identity = { user: { id: "reader-1" }, organization: { id: activityOrganization } };
const params = new URLSearchParams();
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => identity }));
vi.mock("@/lib/api/blofin-activity", () => ({ readNativeActivity: vi.fn() }));
vi.mock("next/navigation", () => ({ useSearchParams: () => params, usePathname: () => "/journal", useRouter: () => ({ replace: vi.fn() }) }));

beforeEach(() => {
  identity = { user: { id: "reader-1" }, organization: { id: activityOrganization } };
  for (const key of [...params.keys()]) params.delete(key);
  vi.mocked(readNativeActivity).mockReset().mockResolvedValue(activityPage());
  vi.spyOn(savedEntries, "list").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(api.journal, "listTrades").mockResolvedValue({ items: [{ id: "local-simulator", symbol: "SIMULATED", timeframe: "1h", direction: "long", status: "closed", result: "win", source: "paper_execution", exchange: "INTERNAL" }], total: 1, limit: 50, offset: 0 });
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("mounts compact native history on the actual Journal page, without simulator rows or statistics", async () => {
  render(<JournalPage />);
  await screen.findByTestId("native-activity-row");
  expect(screen.getByRole("heading", { level: 1, name: "Trades" })).toBeInTheDocument();
  expect(screen.queryByTestId("native-activity-statistics")).not.toBeInTheDocument();
  expect(screen.queryByText(/SIMULATED/)).not.toBeInTheDocument();
});

it("clears Journal history when authenticated user changes", async () => {
  const { rerender } = render(<JournalPage />);
  await screen.findByTestId("native-activity-row");
  identity = { ...identity, user: { id: "reader-2" } };
  vi.mocked(readNativeActivity).mockResolvedValue(activityPage({ items: [] }));
  rerender(<JournalPage />);
  expect(screen.queryByTestId("native-activity-row")).not.toBeInTheDocument();
  await screen.findByText(/No stored fills/);
});

it("does not mount or fetch native activity in the Knowledge view", async () => {
  params.set("tab", "knowledge");
  vi.spyOn(api.knowledge, "listDocuments").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  render(<JournalPage />);
  await screen.findByText("No matching entries.");
  expect(readNativeActivity).not.toHaveBeenCalled();
});

it("shows Trades and Stats as URL tabs, and Stats skips the trade list reads", async () => {
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
      : [{ key: "nested", label: "Nested BTC", metrics: metrics() }],
  }) as never);
  render(<JournalPage />);
  const tabs = screen.getByRole("navigation", { name: "Trades views" });
  expect([...tabs.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href"), a.getAttribute("aria-current")])).toEqual([
    ["Trades", "/journal", "page"],
    ["Stats", "/journal?tab=stats", null],
  ]);
  // Knowledge has its own sidebar item now; no interim header link.
  expect(screen.queryByRole("link", { name: "Knowledge" })).not.toBeInTheDocument();
  cleanup();
  vi.mocked(api.journal.listTrades).mockClear();
  params.set("tab", "stats");
  render(<JournalPage />);
  const stats = await screen.findByTestId("trade-stats");
  expect(screen.getByRole("link", { name: "Stats" })).toHaveAttribute("aria-current", "page");
  expect(stats).toHaveTextContent("Win rate75.0%");
  expect(stats).toHaveTextContent("Net P&L (paper/demo)+42.50");
  expect(stats).toHaveTextContent("Profit factor2.50");
  expect(stats).toHaveTextContent("Avg R0.80");
  expect(within(screen.getByRole("region", { name: "By strategy" })).getByText("Nested BTC")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "By symbol" })).getByText("-3.00")).toHaveClass("text-negative");
  expect(api.journal.statistics).toHaveBeenCalledWith({ group_by: "strategy" });
  expect(api.journal.statistics).toHaveBeenCalledWith({ group_by: "symbol" });
  expect(api.journal.listTrades).not.toHaveBeenCalled();
  expect(screen.queryByTestId("native-activity-row")).not.toBeInTheDocument();
});

it("says Stats are unavailable instead of showing zeros", async () => {
  vi.spyOn(api.journal, "statistics").mockRejectedValue(new Error("down"));
  params.set("tab", "stats");
  render(<JournalPage />);
  expect(await screen.findByText("Stats unavailable.")).toBeInTheDocument();
});

it("offers New demo order in the Trades header on both tabs, not on Knowledge", async () => {
  render(<JournalPage />);
  expect(screen.getByRole("button", { name: "New demo order" })).toBeInTheDocument();
  cleanup();
  vi.spyOn(api.journal, "statistics").mockRejectedValue(new Error("down"));
  params.set("tab", "stats");
  render(<JournalPage />);
  expect(screen.getByRole("button", { name: "New demo order" })).toBeInTheDocument();
});
