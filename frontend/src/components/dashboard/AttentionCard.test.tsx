import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AttentionCard, AttentionContent } from "./AttentionCard";
import type { AttentionItem, AttentionQueue } from "@/lib/api/attention-types";

const attention = vi.fn();
vi.mock("@/lib/api", () => ({ api: { dashboard: { attention: () => attention() } } }));
const now = Date.parse("2026-10-02T12:00:00Z");
const item: AttentionItem = {
  item_id: "risk-1", category: "risk_block", severity: "critical", title: "Risk block to review",
  reason: "Recorded daily loss restriction.", recommended_next_action: "Review the risk event; preserve restrictions.",
  symbol: "BTCUSDT", strategy_id: "strategy-1", strategy_version_id: "version-1",
  expires_at: null, acknowledgement_state: "unsupported",
  sources: [{ record_type: "risk_events", record_id: "source-1", occurred_at: "2026-10-02T11:00:00Z",
    version: null, content_hash: null, upstream_system: null, upstream_event_id: null }],
};
const queue: AttentionQueue = {
  schema_version: "AttentionQueue/v1", organization_id: "org-1", user_id: "user-1",
  generated_at: "2026-10-02T12:00:00Z", items: [item], recommended_next_action: item.recommended_next_action ?? null,
  limitations: ["Only stored records are projected."], execution_mode: "paper",
  executes_trades: false, approves_strategies: false, bypasses_risk: false, telegram_delivery: false,
};

describe("Dashboard attention", () => {
  beforeEach(() => attention.mockReset());
  afterEach(() => { cleanup(); vi.useRealTimers(); });
  it("shows provenance, recommendation and review state without mutation controls", () => {
    render(<AttentionContent queue={queue} now={now} />);
    expect(screen.getByText(/Risk block to review/)).toBeInTheDocument();
    expect(screen.getByText(/source-1/)).toBeInTheDocument();
    expect(screen.getByText(/Strategy version: version-1/)).toBeInTheDocument();
    expect(screen.getByText(/Acknowledgement: Unsupported/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /execute|approve|send|acknowledge/i })).not.toBeInTheDocument();
  });
  it("distinguishes no action from unavailable", async () => {
    attention.mockResolvedValue({ ...queue, items: [], recommended_next_action: null });
    render(<AttentionCard />);
    expect(await screen.findByText(/No action available/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it.each([null, {}, undefined])("treats missing queue %j as unavailable", async (data) => {
    attention.mockResolvedValue(data);
    render(<AttentionCard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Attention unavailable");
    expect(screen.queryByText(/No action available/)).not.toBeInTheDocument();
  });
  it("reports failed reads and retries using only the attention GET", async () => {
    attention.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(queue);
    render(<AttentionCard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Attention unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Refresh attention" }));
    expect(await screen.findByTestId("attention-content")).toBeInTheDocument();
    expect(attention).toHaveBeenCalledTimes(2);
  });
  it("hides old market recommendations while refreshing", async () => {
    attention.mockResolvedValueOnce(queue).mockImplementationOnce(() => new Promise(() => {}));
    render(<AttentionCard />);
    await screen.findByTestId("attention-content");
    fireEvent.click(screen.getByRole("button", { name: "Refresh attention" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Loading attention");
    expect(screen.queryByTestId("attention-content")).not.toBeInTheDocument();
  });
  it("removes an item at its expiration boundary without another request", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    attention.mockResolvedValue({ ...queue, items: [{ ...item, expires_at: "2026-10-02T12:00:01Z" }] });
    render(<AttentionCard />);
    await act(async () => {});
    expect(screen.getByText(/Risk block to review/)).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(screen.queryByText(/Risk block to review/)).not.toBeInTheDocument();
    expect(screen.getByText(/No action available/)).toBeInTheDocument();
    expect(attention).toHaveBeenCalledTimes(1);
  });
  it("keeps the ordered compact list and shows the full count", () => {
    render(<AttentionContent queue={{ ...queue, items: Array.from({ length: 7 }, (_, i) => ({
      ...item, item_id: `risk-${i}`, title: `Review item ${i}`,
    })) }} now={now} />);
    expect(screen.getByText(/Showing 5 of 7/)).toBeInTheDocument();
    expect(screen.getByText(/Review item 0/)).toBeInTheDocument();
    expect(screen.queryByText(/Review item 5/)).not.toBeInTheDocument();
  });
});
