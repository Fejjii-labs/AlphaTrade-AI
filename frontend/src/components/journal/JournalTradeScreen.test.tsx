import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { JournalTradeScreen } from "./JournalTradeScreen";
import { journalTradeApi, type JournalTradeDetail } from "@/lib/api/journal-trade";

vi.mock("@/lib/api/journal-trade", () => ({
  journalTradeApi: { detail: vi.fn(), reflect: vi.fn(), annotate: vi.fn() },
}));
const fixture: JournalTradeDetail = {
  trade: { id: "exact-trade", symbol: "BTCUSDT", timeframe: "manual", direction: "long", status: "open", result: "open", source: "manual_demo_test", exchange: "BLOFIN_DEMO", size: "0.0001", entry_price: "82234.40", exit_price: null, fees: "0.00493406", gross_pnl: null, net_pnl: null, funding: null, exit_time: null, execution_lifecycle_id: "exact-command" },
  manual_demo: {
    command_id: "exact-command", account_id: "fixture-account", account_name: "Fixture BloFin demo", venue: "BLOFIN_DEMO", origin: "manual_demo_test", attempted_at: "2026-10-08T12:56:23Z", submitted_at: "2026-10-08T12:56:23Z", symbol: "BTCUSDT", side: "BUY", requested_contracts: "0.1", base_quantity: "0.0001", stop: "82000", target: "83000", content_hash: "fixture-hash", submission_outcome: "ALLOW", blocked_reason: null, detail_url: "/execution/manual-demo/exact-command",
    evidence: { origin: "manual demo test", revision_id: "fixture-plan", command_id: "exact-command", client_order_id: "fixture-client", status: "filled", filled_quantity: "0.1", remaining_quantity: "0", average_fill_price: "82234.40", fees: "0.00493406", protection: "unverified", journal_trade_id: "exact-trade", missing_evidence: [], position_status: "unknown", exit_price: null, gross_pnl: null, funding: null, net_pnl: null },
  },
  evidence: [], rule_checks: [], observations: [],
};
beforeEach(() => { vi.mocked(journalTradeApi.detail).mockResolvedValue(fixture); vi.mocked(journalTradeApi.reflect).mockResolvedValue({ id: "reflection", category: "behavioral", observation: "Keep the stop", created_at: "2026-10-09T10:00:00Z" }); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });
it("loads only the exact linked trade with unknown outcomes and precise tiny fees", async () => {
  render(<JournalTradeScreen tradeId="exact-trade" />);
  await screen.findByTestId("journal-trade-detail");
  expect(journalTradeApi.detail).toHaveBeenCalledExactlyOnceWith("exact-trade");
  expect(screen.getByText("BLOFIN_DEMO")).toBeInTheDocument();
  expect(screen.getByText("Manual Demo Test")).toBeInTheDocument();
  expect(screen.getByText("0.00493406 USDT")).toBeInTheDocument();
  expect(screen.getAllByText("— USDT").length).toBeGreaterThan(0);
  expect(screen.getByText(/Account flatness alone/)).toBeInTheDocument();
  expect(screen.queryByTestId("journal-mirror-annotation")).not.toBeInTheDocument();
});
it("attaches a personal reflection through observations without editing execution evidence", async () => {
  let finish!: () => void;
  vi.mocked(journalTradeApi.reflect).mockImplementation(() => new Promise((resolve) => { finish = () => resolve({ id: "reflection", category: "behavioral", observation: "Keep the stop", created_at: "2026-10-09T10:00:00Z" }); }));
  render(<JournalTradeScreen tradeId="exact-trade" />);
  await screen.findByTestId("journal-trade-detail");
  fireEvent.change(screen.getByLabelText("What would you repeat or change?"), { target: { value: " Keep the stop " } });
  const button = screen.getByRole("button", { name: "Attach reflection" });
  fireEvent.click(button); fireEvent.click(button);
  expect(journalTradeApi.reflect).toHaveBeenCalledExactlyOnceWith("exact-trade", "Keep the stop");
  expect(button).toBeDisabled();
  finish();
  await screen.findByText("Reflection attached to this trade.");
  expect(journalTradeApi.detail).toHaveBeenCalledTimes(2);
});
it("shows denial instead of other records or a replacement entry", async () => {
  vi.mocked(journalTradeApi.detail).mockRejectedValue(new Error("Trade not found in this organization"));
  render(<JournalTradeScreen tradeId="foreign-trade" />);
  await screen.findByText("Trade not found in this organization");
  expect(screen.queryByTestId("journal-trade-detail")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Attach reflection" })).not.toBeInTheDocument();
  expect(journalTradeApi.reflect).not.toHaveBeenCalled();
});
it("rejects a mismatched trade response", async () => {
  render(<JournalTradeScreen tradeId="different-trade" />);
  await screen.findByText("The linked Journal trade could not be verified.");
  expect(screen.queryByText("BLOFIN_DEMO")).not.toBeInTheDocument();
});
it("retains a failed reflection draft for retry", async () => {
  vi.mocked(journalTradeApi.reflect).mockRejectedValue(new Error("Reflection unavailable"));
  render(<JournalTradeScreen tradeId="exact-trade" />);
  const input = await screen.findByLabelText("What would you repeat or change?");
  fireEvent.change(input, { target: { value: "Keep the stop" } });
  fireEvent.click(screen.getByRole("button", { name: "Attach reflection" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Reflection unavailable"));
  expect(input).toHaveValue("Keep the stop");
});

it("uses audited manual economics instead of legacy mutable Journal amounts", async () => {
  vi.mocked(journalTradeApi.detail).mockResolvedValue({ ...fixture, trade: { ...fixture.trade, entry_price: "100", exit_price: "200", net_pnl: "999", fees: "0" } });
  render(<JournalTradeScreen tradeId="exact-trade" />);
  await screen.findByTestId("journal-trade-detail");
  expect(screen.getByText("82,234.40 USDT")).toBeInTheDocument();
  expect(screen.getByText("0.00493406 USDT")).toBeInTheDocument();
  expect(screen.queryByText("999.00 USDT")).not.toBeInTheDocument();
  expect(screen.queryByText("200.00 USDT")).not.toBeInTheDocument();
});

it("tags a mirrored BloFin demo trade without editing venue amounts", async () => {
  vi.mocked(journalTradeApi.detail).mockResolvedValue({
    ...fixture,
    trade: {
      ...fixture.trade,
      source: "blofin_demo_manual",
      external_ref: "blofin-demo-history:h-long",
      leverage: "5",
      planned_stop_price: "90",
      strategy_label: null,
      grade: null,
      notes: null,
    },
    manual_demo: null,
  });
  vi.mocked(journalTradeApi.annotate).mockResolvedValue({
    ...fixture.trade,
    source: "blofin_demo_manual",
  });
  render(<JournalTradeScreen tradeId="exact-trade" />);
  await screen.findByTestId("journal-mirror-annotation");
  expect(screen.getByText("5")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Strategy"), { target: { value: " Breakout " } });
  fireEvent.change(screen.getByLabelText("Grade"), { target: { value: "A-" } });
  fireEvent.change(screen.getByLabelText("Notes"), { target: { value: " Held the plan " } });
  fireEvent.click(screen.getByRole("button", { name: "Save strategy tag" }));
  await screen.findByText("Strategy tag saved.");
  expect(journalTradeApi.annotate).toHaveBeenCalledExactlyOnceWith("exact-trade", {
    strategy_label: "Breakout",
    grade: "A-",
    notes: "Held the plan",
  });
  expect(screen.getByText("82,234.40 USDT")).toBeInTheDocument();
});
