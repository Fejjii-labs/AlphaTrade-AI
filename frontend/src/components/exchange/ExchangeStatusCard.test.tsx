import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api";
import type { BloFinSyncSnapshotItem } from "@/lib/api/types";

import { ExchangeStatusCard, exchangeStatus } from "./ExchangeStatusCard";

const snapshot: BloFinSyncSnapshotItem = {
  id: "s", organization_id: "o", user_id: "u",
  synced_at: "2026-11-19T18:16:53Z",
  health_status: "ok", provider: "blofin_demo", exchange_mode: "paper_exchange_demo",
  account_snapshot: {}, positions_snapshot: {}, market_context: {}, provenance: { read_only: true },
  is_stale: false, stale_reason: null, error_summary: null,
  position_count: 2, balance_count: 1, note: "BloFin demo read-only snapshot.",
};

beforeEach(() => {
  vi.spyOn(api.exchange, "blofinSyncLatest").mockResolvedValue(snapshot);
  vi.spyOn(api.exchange, "blofinSync").mockResolvedValue({ snapshot: { ...snapshot, position_count: 3 }, note: "" });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("exchangeStatus", () => {
  it("maps the snapshot to one word and a reason only when something is wrong", () => {
    expect(exchangeStatus({ kind: "ok", snapshot })).toEqual({ word: "Connected", tone: "ok", reason: null });
    expect(exchangeStatus({ kind: "none" })).toEqual({ word: "Not connected", tone: "unknown", reason: null });
    expect(exchangeStatus({ kind: "ok", snapshot: { ...snapshot, is_stale: true } })).toEqual({
      word: "Connected", tone: "warn", reason: "Last sync is old.",
    });
    expect(exchangeStatus({ kind: "ok", snapshot: { ...snapshot, health_status: "unavailable" } })).toEqual({
      word: "Error", tone: "down", reason: "BloFin did not answer the last sync.",
    });
    expect(exchangeStatus({ kind: "ok", snapshot: { ...snapshot, health_status: "degraded" } }).word).toBe("Error");
  });
});

describe("ExchangeStatusCard", () => {
  it("renders the card shell at once with a skeleton, then status, time and counts", async () => {
    let resolve: (value: BloFinSyncSnapshotItem) => void = () => {};
    vi.mocked(api.exchange.blofinSyncLatest).mockReturnValue(new Promise((r) => { resolve = r; }));
    render(<ExchangeStatusCard />);
    expect(screen.getByRole("heading", { name: "BloFin demo" })).toBeInTheDocument();
    expect(screen.getByTestId("exchange-status-skeleton")).toBeInTheDocument();
    resolve(snapshot);
    const card = await screen.findByTestId("exchange-status");
    expect(card).toHaveTextContent("Connected");
    expect(card).toHaveTextContent("Balances1");
    expect(card).toHaveTextContent("Positions2");
    expect(screen.getByTestId("exchange-last-sync")).not.toHaveTextContent("—");
    for (const gone of [/read-only/i, /Order mutations/, /paper_exchange_demo/, /blofin_demo/, /never/i, /\bok\b/]) {
      expect(card).not.toHaveTextContent(gone);
    }
  });

  it("Sync now refreshes the card in place", async () => {
    render(<ExchangeStatusCard />);
    await screen.findByTestId("exchange-status");
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(screen.getByTestId("exchange-status")).toHaveTextContent("Positions3"));
    expect(api.exchange.blofinSync).toHaveBeenCalledOnce();
  });

  it("shows Not connected without a snapshot and a short line when sync fails", async () => {
    vi.mocked(api.exchange.blofinSyncLatest).mockRejectedValue(new ApiError("Not found", 404, null));
    vi.mocked(api.exchange.blofinSync).mockRejectedValue(new TypeError("Failed to fetch"));
    render(<ExchangeStatusCard />);
    expect(await screen.findByTestId("exchange-status")).toHaveTextContent("Not connected");
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sync failed. Try again.");
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();
  });

  it("offers Retry instead of raw error text when the status cannot load", async () => {
    vi.mocked(api.exchange.blofinSyncLatest).mockRejectedValue(new TypeError("Failed to fetch"));
    render(<ExchangeStatusCard />);
    expect(await screen.findByText("Couldn't load.")).toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();
    vi.mocked(api.exchange.blofinSyncLatest).mockResolvedValue(snapshot);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("exchange-status")).toHaveTextContent("Connected");
  });
});
