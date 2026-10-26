import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_WATCHLIST_SLOTS,
  WatcherWatchlistEditor,
  WatcherWatchlistSection,
} from "@/components/WatcherWatchlistSection";
import type { WatcherSymbolRuntimeStatus } from "@/lib/api/types";

const statuses: WatcherSymbolRuntimeStatus[] = DEFAULT_WATCHLIST_SLOTS.map((slot) => ({
  configuration_revision: 0,
  observed_at: new Date().toISOString(),
  position: slot.position,
  symbol: slot.symbol,
  enabled: slot.enabled,
  market_source: slot.symbol === "BTCUSDT" ? "replay" : "unavailable",
  freshness: slot.symbol === "BTCUSDT" ? "unknown" : "unavailable",
  last_successful_scan: null,
  last_failed_scan: null,
  setup_state: slot.symbol === "BTCUSDT" ? "not_scanned" : "unavailable",
  strategy_matches: [],
  alert_state: "none",
  error_state: slot.symbol === "BTCUSDT" ? null : "contract_unverified",
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("WatcherWatchlistEditor", () => {
  it("shows five slots with live status beside each symbol", () => {
    render(
      <WatcherWatchlistEditor
        slots={DEFAULT_WATCHLIST_SLOTS}
        statuses={statuses}
        onChange={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getAllByTestId(/watchlist-slot-/)).toHaveLength(5);
    expect(screen.getByLabelText("Symbol for slot 1")).toHaveValue("BTCUSDT");
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("replay");
    expect(screen.getByTestId("watchlist-status-2")).toHaveTextContent("contract unverified");
  });

  it("lays each slot on the Row baseline with the source badge on that baseline", () => {
    render(
      <WatcherWatchlistEditor
        slots={DEFAULT_WATCHLIST_SLOTS}
        statuses={statuses}
        onChange={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByTestId("watchlist-slot-1")).toHaveClass("grid", "items-baseline", "min-h-10");
    const badge = screen.getByTestId("watchlist-status-1").firstElementChild;
    expect(badge).toHaveClass("self-baseline", "leading-none");
  });

  it("toggles enable, edits a symbol, and reorders", () => {
    const onChange = vi.fn();
    render(
      <WatcherWatchlistEditor
        slots={DEFAULT_WATCHLIST_SLOTS}
        statuses={statuses}
        onChange={onChange}
        onSave={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText("Enable slot 2"));
    expect(onChange).toHaveBeenCalled();
    const disabled = onChange.mock.calls[0]?.[0] as typeof DEFAULT_WATCHLIST_SLOTS;
    expect(disabled[1]?.enabled).toBe(false);

    fireEvent.change(screen.getByLabelText("Symbol for slot 3"), {
      target: { value: "solusdt" },
    });
    const replaced = onChange.mock.calls.at(-1)?.[0] as typeof DEFAULT_WATCHLIST_SLOTS;
    expect(replaced[2]?.symbol).toBe("SOLUSDT");

    fireEvent.click(screen.getByLabelText("Move slot 1 down"));
    const reordered = onChange.mock.calls.at(-1)?.[0] as typeof DEFAULT_WATCHLIST_SLOTS;
    expect(reordered[0]?.symbol).toBe("ZECUSDT");
    expect(reordered[1]?.symbol).toBe("BTCUSDT");
  });
});


describe("symbol and revision status boundaries", () => {
  it("status follows the symbol through reorder and is never inherited by replacement", () => {
    const props = { statuses, onChange: vi.fn(), onSave: vi.fn() };
    const view = render(<WatcherWatchlistEditor {...props} slots={DEFAULT_WATCHLIST_SLOTS} />);
    const reordered = [DEFAULT_WATCHLIST_SLOTS[1]!, DEFAULT_WATCHLIST_SLOTS[0]!, ...DEFAULT_WATCHLIST_SLOTS.slice(2)]
      .map((slot, index) => ({...slot, position: index + 1}));
    view.rerender(<WatcherWatchlistEditor {...props} slots={reordered} />);
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("contract unverified");
    expect(screen.getByTestId("watchlist-status-2")).toHaveTextContent("replay");
    const replaced = reordered.map((slot, index) => index === 1 ? {...slot, symbol: "SOLUSDT"} : slot);
    view.rerender(<WatcherWatchlistEditor {...props} slots={replaced} />);
    expect(screen.getByTestId("watchlist-status-2")).toHaveTextContent("Pending / unscanned");
    expect(screen.getByTestId("watchlist-status-2")).not.toHaveTextContent("replay");
  });

  it("rejects old revisions, missing timestamps and stale observations", () => {
    const rows = statuses.map((row) => ({...row, observed_at: new Date(Date.now() - 120000).toISOString()}));
    const props = { slots: DEFAULT_WATCHLIST_SLOTS, onChange: vi.fn(), onSave: vi.fn() };
    const view = render(<WatcherWatchlistEditor {...props} statuses={rows} />);
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("Pending / unscanned");
    view.rerender(<WatcherWatchlistEditor {...props} statuses={statuses} configurationRevision={1} />);
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("Pending / unscanned");
    view.rerender(<WatcherWatchlistEditor {...props} statuses={statuses.map(row => ({...row, observed_at:null}))} />);
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("Pending / unscanned");
  });

  it("refreshes configuration and status after a revisioned save", async () => {
    const { api } = await import("@/lib/api");
    const config = { revision: 0, updated_at: new Date().toISOString(), max_enabled: 5, paper_only: true, slots: DEFAULT_WATCHLIST_SLOTS };
    const configuration = vi.spyOn(api.watcherWatchlist, "configuration").mockResolvedValue(config);
    const status = vi.spyOn(api.watcherWatchlist, "status").mockResolvedValue({
      configuration_revision:0, observed_at:new Date().toISOString(), stale_after_seconds:90,
      paper_only:true, real_trading_enabled:false, symbols:statuses,
    });
    const replace = vi.spyOn(api.watcherWatchlist, "replace").mockResolvedValue({...config, revision:1});
    render(<WatcherWatchlistSection />);
    await waitFor(() => expect(screen.getByTestId("watchlist-save")).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText("Symbol for slot 1"), {target:{value:"SOLUSDT"}});
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("Pending / unscanned");
    configuration.mockResolvedValue({...config, revision:1, slots:config.slots.map((slot, i) => i ? slot : {...slot, symbol:"SOLUSDT"})});
    fireEvent.click(screen.getByTestId("watchlist-save"));
    await waitFor(() => expect(configuration).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText("Watchlist saved. Paper only.")).toBeInTheDocument());
    expect(status).toHaveBeenCalledTimes(2);
    expect(replace.mock.calls[0]?.[1]).toBe(0);
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("Pending / unscanned");
  });
});


describe("watchlist request coordination", () => {
  function responses() {
    return {
      config: { revision: 0, updated_at: new Date().toISOString(), max_enabled: 5, paper_only: true, slots: DEFAULT_WATCHLIST_SLOTS },
      status: { configuration_revision: 0, observed_at: new Date().toISOString(), stale_after_seconds: 90, paper_only: true, real_trading_enabled: false, symbols: [] as WatcherSymbolRuntimeStatus[] },
    };
  }

  it("keeps a slow initial configuration load and starts polling after it completes", async () => {
    vi.useFakeTimers();
    const { api } = await import("@/lib/api");
    const { config, status } = responses();
    let resolveConfig!: (value: typeof config) => void;
    const configuration = vi.spyOn(api.watcherWatchlist, "configuration").mockImplementation(() => new Promise(resolve => { resolveConfig = resolve; }));
    const readStatus = vi.spyOn(api.watcherWatchlist, "status").mockResolvedValue(status);
    await act(async () => { render(<WatcherWatchlistSection />); });
    await act(async () => { await vi.advanceTimersByTimeAsync(65000); });
    expect(readStatus).toHaveBeenCalledTimes(1);
    await act(async () => { resolveConfig(config); });
    expect(configuration).toHaveBeenCalledTimes(1);
    expect(screen.getAllByTestId(/watchlist-slot-/)).toHaveLength(5);
    expect(screen.getByTestId("watchlist-save")).not.toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(readStatus).toHaveBeenCalledTimes(2);
  });

  it("expires a displayed observation while the next status poll remains unresolved", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-01T00:00:00Z"));
    const { api } = await import("@/lib/api");
    const { config, status } = responses();
    const freshStatus = { ...status, symbols: statuses.map(row => ({
      ...row, observed_at: new Date().toISOString(), freshness: "fresh_closed_candle",
    })) };
    vi.spyOn(api.watcherWatchlist, "configuration").mockResolvedValue(config);
    const readStatus = vi.spyOn(api.watcherWatchlist, "status").mockResolvedValueOnce(freshStatus)
      .mockImplementation(() => new Promise(() => {}));
    let view!: ReturnType<typeof render>;
    await act(async () => { view = render(<WatcherWatchlistSection />); });
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("fresh closed candle");
    await act(async () => { await vi.advanceTimersByTimeAsync(89999); });
    expect(readStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("fresh closed candle");
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("Pending / unscanned");
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("Pending / unscanned");
    expect(readStatus).toHaveBeenCalledTimes(2);
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not overlap or discard a slow status poll", async () => {
    vi.useFakeTimers();
    const { api } = await import("@/lib/api");
    const { config, status } = responses();
    vi.spyOn(api.watcherWatchlist, "configuration").mockResolvedValue(config);
    let resolvePoll!: (value: typeof status) => void;
    const readStatus = vi.spyOn(api.watcherWatchlist, "status").mockResolvedValueOnce(status)
      .mockImplementation(() => new Promise(resolve => { resolvePoll = resolve; }));
    await act(async () => { render(<WatcherWatchlistSection />); });
    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });
    expect(readStatus).toHaveBeenCalledTimes(2);
    await act(async () => { resolvePoll({ ...status, symbols: statuses.map(row => ({ ...row, observed_at: new Date().toISOString(), market_source: "completed-poll", error_state: null })) }); });
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("completed-poll");
  });

  it("keeps an hour of normal polling within the unchanged shared read budget", async () => {
    vi.useFakeTimers();
    const { api } = await import("@/lib/api");
    const { config, status } = responses();
    let revision = 0;
    const configuration = vi.spyOn(api.watcherWatchlist, "configuration").mockImplementation(async () => ({ ...config, revision }));
    const readStatus = vi.spyOn(api.watcherWatchlist, "status").mockImplementation(async () => ({ ...status, configuration_revision: revision }));
    const replace = vi.spyOn(api.watcherWatchlist, "replace").mockImplementation(async () => ({ ...config, revision: ++revision }));
    await act(async () => { render(<WatcherWatchlistSection />); });
    for (let save = 0; save < 6; save += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(600000); });
      await act(async () => { fireEvent.click(screen.getByTestId("watchlist-save")); });
    }
    expect(replace.mock.calls.map(call => call[1])).toEqual([0, 1, 2, 3, 4, 5]);
    expect(readStatus.mock.calls.length).toBeGreaterThan(1);
    expect(configuration.mock.calls.length + readStatus.mock.calls.length).toBeLessThan(120);
  });

  it("ignores a pre-save poll after loading the new revision", async () => {
    vi.useFakeTimers();
    const { api } = await import("@/lib/api");
    const { config, status } = responses();
    const saved = { ...config, revision: 1, slots: config.slots.map((slot, index) => index ? slot : { ...slot, symbol: "SOLUSDT" }) };
    const savedStatus = { ...status, configuration_revision: 1, symbols: statuses.map((row, index) => ({ ...row, configuration_revision: 1, symbol: index ? row.symbol : "SOLUSDT", observed_at: new Date().toISOString(), market_source: "fresh-saved", error_state: null })) };
    vi.spyOn(api.watcherWatchlist, "configuration").mockResolvedValueOnce(config).mockResolvedValue(saved);
    let resolveOldPoll!: (value: typeof status) => void;
    vi.spyOn(api.watcherWatchlist, "status").mockResolvedValueOnce(status)
      .mockImplementationOnce(() => new Promise(resolve => { resolveOldPoll = resolve; }))
      .mockResolvedValue(savedStatus);
    vi.spyOn(api.watcherWatchlist, "replace").mockResolvedValue(saved);
    await act(async () => { render(<WatcherWatchlistSection />); });
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Symbol for slot 1"), { target: { value: "SOLUSDT" } });
    });
    await act(async () => { fireEvent.click(screen.getByTestId("watchlist-save")); });
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("fresh-saved");
    await act(async () => { resolveOldPoll(status); });
    expect(screen.getByLabelText("Symbol for slot 1")).toHaveValue("SOLUSDT");
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("fresh-saved");
    expect(screen.getByTestId("watchlist-save")).not.toBeDisabled();
  });
});


describe("market configuration availability", () => {
  it("shows saved markets even when provider status is unavailable", async () => {
    const { api } = await import("@/lib/api");
    vi.spyOn(api.watcherWatchlist, "configuration").mockResolvedValue({
      revision: 4, slots: DEFAULT_WATCHLIST_SLOTS, max_enabled: 5, paper_only: true, updated_at: new Date().toISOString(),
    });
    vi.spyOn(api.watcherWatchlist, "status").mockRejectedValue(new Error("Unavailable"));
    render(<WatcherWatchlistSection />);
    expect(await screen.findByLabelText("Symbol for slot 1")).toHaveValue("BTCUSDT");
    expect(screen.getByTestId("watchlist-save")).not.toBeDisabled();
    expect(screen.getByText("Market availability unavailable. Your saved watchlist is shown.")).toBeInTheDocument();
    expect(screen.getByTestId("watchlist-status-1")).toHaveTextContent("Pending / unscanned");
  });

  it("labels failed configuration without inventing default market slots", async () => {
    const { api } = await import("@/lib/api");
    vi.spyOn(api.watcherWatchlist, "configuration").mockRejectedValue(new Error("Unavailable"));
    vi.spyOn(api.watcherWatchlist, "status").mockRejectedValue(new Error("Unavailable"));
    render(<WatcherWatchlistSection />);
    await screen.findByText("Watchlist configuration unavailable. Reload to try again.");
    expect(screen.queryByLabelText("Symbol for slot 1")).not.toBeInTheDocument();
    expect(screen.getByTestId("watchlist-save")).toBeDisabled();
  });
});
