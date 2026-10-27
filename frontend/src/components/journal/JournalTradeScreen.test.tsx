import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { JournalTradeScreen } from "./JournalTradeScreen";
import { journalTradeApi, type JournalTradeDetail } from "@/lib/api/journal-trade";
import { expectNoIdentifiers } from "@/test/no-identifiers";

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
  expect(screen.getByText("BloFin demo")).toBeVisible();
  expect(screen.getByText("BLOFIN_DEMO")).not.toBeVisible();
  expect(screen.getByText("Manual Demo Test")).toBeInTheDocument();
  expect(screen.getByText("0.00493406 USDT")).toBeInTheDocument();
  expect(screen.getAllByText("— USDT").length).toBeGreaterThan(0);
  expect(screen.getByText(/Account flatness alone/)).toBeInTheDocument();
  expect(screen.queryByTestId("journal-mirror-annotation")).not.toBeInTheDocument();
  expectNoIdentifiers();
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

it("does not offer strategy tags on the mirror cycle marker", async () => {
  vi.mocked(journalTradeApi.detail).mockResolvedValue({
    ...fixture,
    trade: {
      ...fixture.trade,
      source: "blofin_demo_manual",
      external_ref: "blofin-demo-mirror-cycle",
      symbol: "MIRROR",
    },
    manual_demo: null,
  });
  render(<JournalTradeScreen tradeId="exact-trade" />);
  await screen.findByTestId("journal-trade-detail");
  expect(screen.queryByTestId("journal-mirror-annotation")).not.toBeInTheDocument();
});

it("renders annotation notes as plain text", async () => {
  const notes = "<img src=x onerror=alert(1)> **bold** <script>alert(1)</script>";
  vi.mocked(journalTradeApi.detail).mockResolvedValue({
    ...fixture,
    trade: {
      ...fixture.trade,
      source: "blofin_demo_manual",
      external_ref: "blofin-demo-history:h-long",
      notes,
      grade: "B",
    },
    manual_demo: null,
  });
  const view = render(<JournalTradeScreen tradeId="exact-trade" />);
  const field = await screen.findByLabelText("Notes");
  expect(field.tagName).toBe("TEXTAREA");
  await waitFor(() => expect(field).toHaveValue(notes));
  expect(view.container.querySelector("img")).toBeNull();
  expect(view.container.querySelector("script")).toBeNull();
  expect(view.container.innerHTML).not.toContain("<img");
  expect(view.container.innerHTML).not.toContain("<script");
});

it("shows reconciled close details and strategy tags for a reconciled manual row", async () => {
  vi.mocked(journalTradeApi.detail).mockResolvedValue({
    ...fixture,
    trade: {
      ...fixture.trade,
      source: "manual",
      status: "closed",
      result: "win",
      external_ref: "blofin-demo-history:h-reconciled",
      exit_reason: "venue_close",
      exit_price: "105",
      exit_time: "2026-10-09T08:00:00Z",
      net_pnl: "0.186",
      tags: ["blofin-reconciled"],
    },
    manual_demo: null,
  });
  render(<JournalTradeScreen tradeId="exact-trade" />);
  await screen.findByTestId("journal-reconciled-badge");
  expect(screen.getByTestId("journal-reconciled-close")).toHaveTextContent("Manual");
  expect(screen.getByText("Venue Close")).toBeInTheDocument();
  expect(screen.getByText("h-reconciled")).toBeInTheDocument();
  expect(screen.getByText("105.00 USDT")).toBeInTheDocument();
  await screen.findByTestId("journal-mirror-annotation");
});

it("shows reconciled state for a stuck manual demo row closed on the venue", async () => {
  vi.mocked(journalTradeApi.detail).mockResolvedValue({
    ...fixture,
    trade: {
      ...fixture.trade,
      source: "manual_demo_test",
      status: "closed",
      external_ref: "blofin-demo-history:h-long",
      exit_reason: "venue_close",
      exit_price: "90",
      exit_time: "2026-10-09T08:00:00Z",
      net_pnl: "-0.2",
      tags: ["blofin-reconciled"],
    },
    manual_demo: null,
  });
  render(<JournalTradeScreen tradeId="exact-trade" />);
  const reconciled = await screen.findByTestId("journal-reconciled-close");
  expect(reconciled).toHaveTextContent("Manual Demo Test");
  expect(reconciled).not.toHaveTextContent("h-long");
  const technical = screen.getByText("Technical details").closest("details")!;
  expect(technical).not.toHaveAttribute("open");
  expect(within(technical).getByText("h-long")).not.toBeVisible();
  expect(screen.getByText("90.00 USDT")).toBeInTheDocument();
});
it("labels an entry order with a venue order id as recorded, not verified", async () => {
  const withVenueOrder: JournalTradeDetail = { ...fixture, manual_demo: { ...fixture.manual_demo!, evidence: { ...fixture.manual_demo!.evidence!, venue_order_id: "venue-order" } } };
  vi.mocked(journalTradeApi.detail).mockResolvedValue(withVenueOrder);
  render(<JournalTradeScreen tradeId="exact-trade" />);
  await screen.findByTestId("journal-trade-detail");
  expect(screen.getByText("Entry order: Recorded on venue")).toBeInTheDocument();
  expect(screen.queryByText(/Verified on venue/)).not.toBeInTheDocument();
});
it("keeps venue code and trade, command and order IDs in collapsed Technical details only", async () => {
  const tradeId = "0f0e0d0c-0b0a-4908-8706-050403020100";
  const commandId = "a1a2a3a4-b1b2-4c1c-8d1d-e1e2e3e4e5e6";
  const venueOrderId = "9876543210123456789";
  const detail: JournalTradeDetail = {
    ...fixture,
    trade: { ...fixture.trade, id: tradeId, execution_lifecycle_id: commandId },
    manual_demo: { ...fixture.manual_demo!, command_id: commandId, evidence: { ...fixture.manual_demo!.evidence!, command_id: commandId, journal_trade_id: tradeId, venue_order_id: venueOrderId } },
  };
  vi.mocked(journalTradeApi.detail).mockResolvedValue(detail);
  render(<JournalTradeScreen tradeId={tradeId} />);
  await screen.findByTestId("journal-trade-detail");
  const technical = screen.getByText("Technical details").closest("details")!;
  expect(technical).not.toHaveAttribute("open");
  for (const [label, value] of [["Venue code", "BLOFIN_DEMO"], ["Journal trade ID", tradeId], ["Manual demo command ID", commandId], ["Venue order ID", venueOrderId], ["Client order ID", "fixture-client"]]) {
    expect(within(technical).getByText(label).nextSibling).toHaveTextContent(value);
  }
  expect(within(technical).queryByText("Venue history ID")).not.toBeInTheDocument();
  expect(screen.getByText("Entry order: Recorded on venue")).toBeInTheDocument();
  expectNoIdentifiers();
});
