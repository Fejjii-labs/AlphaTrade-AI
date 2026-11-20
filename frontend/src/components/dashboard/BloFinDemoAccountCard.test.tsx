import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BloFinDemoAccountCard } from "./BloFinDemoAccountCard";
import { DEMO_REFRESH_INTERVAL_MS, DEMO_REQUEST_TIMEOUT_MS, DEMO_MAX_BACKOFF_MS } from "./useDemoAccountSnapshot";
import {
  demoAccountApi,
  type DashboardDemoAccount,
} from "@/lib/api/dashboard-demo-account";

vi.mock("@/lib/api/dashboard-demo-account", () => ({
  demoAccountApi: { latest: vi.fn(), refresh: vi.fn() },
}));

const latest = vi.mocked(demoAccountApi.latest);
const refresh = vi.mocked(demoAccountApi.refresh);

function account(
  overrides: Partial<DashboardDemoAccount> = {},
): DashboardDemoAccount {
  return {
    venue: "BLOFIN_DEMO",
    read_only: true,
    status: "ok",
    can_refresh: true,
    snapshot_id: "fixture-snapshot",
    synced_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 300_000).toISOString(),
    total_equity_usd: "1001.50",
    refresh_error: null,
    last_attempt_at: null,
    balances: [{ asset: "USDT", total: "1000.25", available: "900.125", equity: "1002.25" }],
    positions: [
      {
        symbol: "BTCUSDT",
        side: "long",
        contracts: "0.1",
        base_asset: "BTC",
        base_quantity: "0.0001",
        entry_price: "82894",
        mark_price: null,
        unrealized_pnl: null,
        leverage: "1",
      },
    ],
    balances_truncated: false,
    positions_truncated: false,
    position_count: 1,
    message: "Native demo account snapshot.",
    ...overrides,
  };
}

/** Waits until the saved snapshot's balances are on screen. */
async function loaded() {
  await waitFor(() =>
    expect(screen.getByTestId("dashboard-available")).toHaveTextContent("900.13 USDT"),
  );
}

function openCount(text: string) {
  expect(screen.getByTestId("dashboard-open-count")).toHaveTextContent(
    new RegExp(`^Open positions${text}$`),
  );
}

beforeEach(() => {
  latest.mockReset().mockResolvedValue(account());
  refresh.mockReset().mockResolvedValue(account());
});

it("does not preserve another account's snapshot when a new account read fails", async () => {
  latest.mockResolvedValueOnce(account({ account_id: "first-account" }))
    .mockResolvedValueOnce(account({ account_id: "second-account", status: "unavailable", total_equity_usd: null }));
  const { rerender } = render(<BloFinDemoAccountCard refreshKey={0} />);
  await loaded();
  expect(screen.getByTestId("dashboard-equity")).toHaveTextContent("1,001.5");
  rerender(<BloFinDemoAccountCard refreshKey={1} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Refresh failed.");
  expect(screen.getByTestId("dashboard-equity")).not.toHaveTextContent("1,001.5");
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("BloFin demo Dashboard", () => {
  it("loads saved balances and native contracts with explicit venue and unknown metrics", async () => {
    render(<BloFinDemoAccountCard />);
    await loaded();
    openCount("1");
    expect(screen.getByText("1,001.50 USD")).toBeInTheDocument();
    expect(screen.queryByText("BLOFIN_DEMO")).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Exchange" }),
    ).toHaveAttribute("href", "/settings/exchange");
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("demo-last-synced")).toHaveTextContent("Last synced");
    expect(screen.getByTestId("dashboard-win-rate")).toHaveTextContent("Not available yet");
    expect(screen.getByTestId("dashboard-pnl")).toHaveTextContent("Not available yet");
  });

  it("deduplicates repeated explicit refresh clicks", async () => {
    render(<BloFinDemoAccountCard />);
    const button = await screen.findByRole("button", {
      name: "Refresh demo account",
    });
    expect(refresh).toHaveBeenCalledTimes(1);
    let resolve!: (data: DashboardDemoAccount) => void;
    refresh.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    fireEvent.click(button);
    fireEvent.click(button);
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Refreshing…");
    openCount("1");
    await act(async () =>
      resolve(account({ positions: [], position_count: 0 })),
    );
    await waitFor(() => openCount("0"));
    expect(latest).toHaveBeenCalledTimes(1);
  });

  it("Dashboard refresh reloads saved evidence without requesting native sync", async () => {
    const view = render(<BloFinDemoAccountCard refreshKey={0} />);
    await loaded();
    latest.mockResolvedValue(account({ status: "stale" }));
    view.rerender(<BloFinDemoAccountCard refreshKey={1} />);
    expect(await screen.findByText("Stale snapshot")).toBeInTheDocument();
    expect(latest).toHaveBeenCalledTimes(2);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("does not let an older saved read overwrite newer saved evidence", async () => {
    let resolveOld!: (data: DashboardDemoAccount) => void;
    latest.mockReturnValue(
      new Promise((done) => {
        resolveOld = done;
      }),
    );
    const view = render(<BloFinDemoAccountCard />);
    latest.mockResolvedValue(
      account({ can_refresh: false, positions: [], position_count: 0 }),
    );
    view.rerender(<BloFinDemoAccountCard refreshKey={1} />);
    await waitFor(() => openCount("0"));
    await act(async () => resolveOld(account({ status: "stale" })));
    expect(screen.getByText("Fresh snapshot")).toBeInTheDocument();
    openCount("0");
  });

  it("Dashboard reload cannot overtake a pending native refresh", async () => {
    let resolve!: (data: DashboardDemoAccount) => void;
    refresh.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const view = render(<BloFinDemoAccountCard />);
    expect(await screen.findByRole("button", { name: "Refreshing…" })).toBeDisabled();
    view.rerender(<BloFinDemoAccountCard refreshKey={1} />);
    expect(latest).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(async () =>
      resolve(account({ positions: [], position_count: 0 })),
    );
    await waitFor(() => openCount("0"));
  });

  it("keeps native failure explicit and preserves successful values", async () => {
    render(<BloFinDemoAccountCard />);
    await loaded();
    refresh.mockResolvedValue(
      account({
        status: "unavailable",
        balances: [],
        positions: [],
        position_count: null,
        message: "The latest demo account sync failed.",
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh demo account" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Refresh failed.");
    expect(screen.getByTestId("dashboard-available")).toHaveTextContent("900.13 USDT");
    expect(screen.getByText("Stale snapshot")).toBeInTheDocument();
    openCount("1");
  });

  it("preserves values on HTTP failure, uses safe error text and supports retry", async () => {
    render(<BloFinDemoAccountCard />);
    await loaded();
    refresh.mockRejectedValue(new Error("opaque raw error"));
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh demo account" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Refresh failed.",
    );
    expect(screen.getByTestId("dashboard-available")).toHaveTextContent("900.13 USDT");
    expect(screen.getByText("Stale snapshot")).toBeInTheDocument();
    expect(screen.queryByText("opaque raw error")).not.toBeInTheDocument();
    refresh.mockResolvedValue(account());
    fireEvent.click(screen.getByRole("button", { name: "Refresh demo account" }));
    expect(await screen.findByText("Fresh snapshot")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each(["inactive", "not_synced"] as const)(
    "shows %s without empty account claims",
    async (status) => {
      const payload = account({
        status,
        positions: [],
        balances: [],
        position_count: null,
        message: "Configure or refresh demo account.",
        can_refresh: status !== "inactive",
      });
      latest.mockResolvedValue(payload);
      refresh.mockResolvedValue(payload);
      render(<BloFinDemoAccountCard />);
      expect(
        (await screen.findAllByText(status === "inactive" ? "Demo account not connected" : "No snapshot yet")).length,
      ).toBeGreaterThan(0);
      expect(screen.queryByText("Configure or refresh demo account.")).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("dashboard-open-count"),
      ).not.toBeInTheDocument();
      if (status === "inactive") {
        expect(refresh).not.toHaveBeenCalled();
      } else {
        expect(refresh).toHaveBeenCalled();
      }
    },
  );

  it("hides native refresh for readers and labels an incomplete position list", async () => {
    latest.mockResolvedValue(
      account({
        can_refresh: false,
        positions_truncated: true,
        position_count: null,
      }),
    );
    render(<BloFinDemoAccountCard />);
    await loaded();
    expect(
      screen.queryByRole("button", { name: "Refresh demo account" }),
    ).not.toBeInTheDocument();
    openCount("—Incomplete list");
  });

  it("marks the snapshot stale when its freshness window expires while open", async () => {
    vi.useFakeTimers();
    latest.mockResolvedValue(
      account({
        can_refresh: false,
        expires_at: new Date(Date.now() + 1000).toISOString(),
      }),
    );
    render(<BloFinDemoAccountCard />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText("Fresh snapshot")).toBeInTheDocument();
    expect(screen.getByTestId("demo-last-synced")).not.toHaveClass("text-warning");
    await act(async () => {
      vi.advanceTimersByTime(1001);
    });
    expect(screen.getByText("Stale snapshot")).toBeInTheDocument();
    expect(screen.getByTestId("demo-last-synced")).toHaveClass("text-warning");
    expect(latest).toHaveBeenCalledTimes(1);
    expect(refresh).not.toHaveBeenCalled();
  });
  it("automatically refreshes native evidence while visible and pauses when hidden", async () => {
    vi.useFakeTimers();
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    render(<BloFinDemoAccountCard />);
    await act(async () => {});
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(DEMO_REFRESH_INTERVAL_MS); });
    expect(refresh).toHaveBeenCalledTimes(2);
    visibility.mockReturnValue("hidden");
    fireEvent(document, new Event("visibilitychange"));
    await act(async () => { vi.advanceTimersByTime(DEMO_REFRESH_INTERVAL_MS * 4); });
    expect(refresh).toHaveBeenCalledTimes(2);
    visibility.mockReturnValue("visible");
    fireEvent(document, new Event("visibilitychange"));
    await act(async () => { vi.advanceTimersByTime(1); });
    expect(refresh).toHaveBeenCalledTimes(3);
    // Repeated visibility events do not create a burst of native requests.
    fireEvent(document, new Event("visibilitychange"));
    await act(async () => { vi.advanceTimersByTime(1); });
    expect(refresh).toHaveBeenCalledTimes(3);
  });

  it("backs off failures up to fifteen minutes and recovers to normal cadence", async () => {
    vi.useFakeTimers();
    refresh.mockRejectedValue(new Error("raw upstream failure"));
    render(<BloFinDemoAccountCard />);
    await act(async () => {});
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("dashboard-available")).toHaveTextContent("900.13 USDT");
    expect(screen.getByText("Stale snapshot")).toBeInTheDocument();
    const delayFor = (failures: number) =>
      Math.min(
        DEMO_MAX_BACKOFF_MS,
        DEMO_REFRESH_INTERVAL_MS * 2 ** Math.min(failures, 3),
      );
    let count = 1;
    for (const failures of [1, 2, 3]) {
      const delay = delayFor(failures);
      await act(async () => { vi.advanceTimersByTime(delay - 1); });
      expect(refresh).toHaveBeenCalledTimes(count);
      await act(async () => { vi.advanceTimersByTime(1); });
      count += 1;
      expect(refresh).toHaveBeenCalledTimes(count);
    }
    refresh.mockImplementation(async () => account());
    await act(async () => { vi.advanceTimersByTime(delayFor(4)); });
    expect(screen.getByText("Fresh snapshot")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(DEMO_REFRESH_INTERVAL_MS); });
    expect(refresh).toHaveBeenCalledTimes(count + 2);
  });

  it("times out a hung request, aborts transport and ignores its late result", async () => {
    vi.useFakeTimers();
    let late!: (value: DashboardDemoAccount) => void;
    refresh.mockReturnValue(new Promise((done) => { late = done; }));
    render(<BloFinDemoAccountCard />);
    await act(async () => {});
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(DEMO_REQUEST_TIMEOUT_MS); });
    expect(refresh.mock.calls[0][0]?.aborted).toBe(true);
    expect(screen.getByRole("button", { name: "Refresh demo account" })).toBeEnabled();
    expect(screen.getByText("Stale snapshot")).toBeInTheDocument();
    await act(async () => late(account({ positions: [], position_count: 0 })));
    openCount("1");
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("deduplicates automatic sync against manual and Dashboard refresh and cancels on unmount", async () => {
    vi.useFakeTimers();
    refresh.mockReturnValue(new Promise(() => {}));
    const view = render(<BloFinDemoAccountCard />);
    await act(async () => {});
    expect(refresh).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Refreshing…" }));
    view.rerender(<BloFinDemoAccountCard refreshKey={1} />);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(latest).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(refresh.mock.calls[0][0]?.aborted).toBe(true);
    await act(async () => { vi.advanceTimersByTime(DEMO_MAX_BACKOFF_MS); });
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("polls saved snapshots for readers without requesting owner-only native sync", async () => {
    vi.useFakeTimers();
    latest.mockResolvedValue(account({ can_refresh: false }));
    render(<BloFinDemoAccountCard />);
    await act(async () => {});
    await act(async () => { vi.advanceTimersByTime(DEMO_REFRESH_INTERVAL_MS * 2); });
    expect(latest).toHaveBeenCalledTimes(2);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("retains server-preserved evidence on reload after a failed native attempt", async () => {
    latest.mockResolvedValue(account({ status: "stale", refresh_error: "Latest sync failed." }));
    render(<BloFinDemoAccountCard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Refresh failed.");
    openCount("1");
    expect(screen.getByText("Stale snapshot")).toBeInTheDocument();
  });

  it("shows unknown equity without inventing values", async () => {
    const payload = account({ total_equity_usd: null });
    latest.mockResolvedValue(payload);
    refresh.mockResolvedValue(payload);
    render(<BloFinDemoAccountCard />);
    // Missing equity is also rendered before load; wait for the balance tile.
    await loaded();
    expect(screen.getByTestId("dashboard-equity")).toHaveTextContent(/^Account equity— USD$/);
  });

});

it("shows activity win rate and withholds it when activity sync is disabled", async () => {
  latest.mockResolvedValue(account({
    can_refresh: false,
    performance: {
      status: "unavailable",
      currency: "USDT",
      gross_pnl: null,
      fees: null,
      funding: null,
      net_pnl: null,
      verified_closed_trades: 0,
      unresolved_trades: 0,
      manual_test_trades: 0,
      strategy_closed_trades: null,
      coverage: "Manual coverage is separate.",
      activity_sync_status: "ready",
      win_rate: "0.5000",
      trade_count: 2,
      wins: 1,
      losses: 1,
      realized_pnl: "2.87",
      window_start: "2026-10-01T00:00:00Z",
      window_end: "2026-10-18T00:00:00Z",
      activity_coverage: "Closed round-trips from stored BloFin demo fills.",
    },
  }));
  render(<BloFinDemoAccountCard />);
  await waitFor(() => {
    expect(screen.getByTestId("dashboard-win-rate")).toHaveTextContent("50.0%");
  });
  expect(screen.getByTestId("dashboard-pnl")).toHaveTextContent("2.87");
  expect(screen.getByTestId("dashboard-pnl")).toHaveTextContent("Fees not included");
  expect(screen.getByTestId("dashboard-win-rate")).toHaveTextContent(/^Win rate50\.0%$/);
});

it("hides a numeric win rate until activity sync is ready", async () => {
  latest.mockResolvedValue(account({
    can_refresh: false,
    performance: {
      status: "unavailable",
      currency: "USDT",
      gross_pnl: null,
      fees: null,
      funding: null,
      net_pnl: null,
      verified_closed_trades: 0,
      unresolved_trades: 0,
      manual_test_trades: 0,
      strategy_closed_trades: null,
      coverage: "Fee basis is unverified.",
      activity_sync_status: "partial",
      win_rate: "0.5000",
      trade_count: 2,
      wins: 1,
      losses: 1,
      realized_pnl: "2.87",
      window_start: "2026-10-01T00:00:00Z",
      window_end: "2026-10-18T00:00:00Z",
      activity_coverage: "Withheld until a flat start is proven.",
    },
  }));
  render(<BloFinDemoAccountCard />);
  await loaded();
  const winRate = screen.getByTestId("dashboard-win-rate");
  expect(winRate).toHaveTextContent(/^Win rate—Withheld$/);
  expect(winRate).not.toHaveTextContent("50.0%");
});

it("shows mirrored win rate while activity sync stays disabled", async () => {
  latest.mockResolvedValue(account({
    can_refresh: false,
    performance: {
      status: "unavailable",
      currency: "USDT",
      gross_pnl: null,
      fees: null,
      funding: null,
      net_pnl: null,
      verified_closed_trades: 0,
      unresolved_trades: 0,
      manual_test_trades: 0,
      strategy_closed_trades: null,
      coverage: "Manual coverage is separate.",
      activity_sync_status: "disabled",
      activity_coverage: "Activity sync is disabled. Win rate and realized P&L for the whole demo account are withheld. Manual AlphaTrade trades are not a substitute.",
      manual_mirror_status: "ready",
      manual_mirror_reason: "Closed BloFin demo manual journal entries.",
      win_rate: "1.0000",
      trade_count: 1,
      unresolved_round_trips: 2,
      wins: 1,
      losses: 0,
      realized_pnl: "0.186",
      average_r: "0.9300",
    },
  }));
  render(<BloFinDemoAccountCard />);
  await waitFor(() => {
    expect(screen.getByTestId("dashboard-win-rate")).toHaveTextContent("100.0%");
  });
  expect(screen.getByTestId("dashboard-pnl")).toHaveTextContent(/^Realized PnL0\.186 USDT$/);
  expect(screen.queryByText(/Not available yet/)).not.toBeInTheDocument();
  expect(screen.queryByText(/are withheld/)).not.toBeInTheDocument();
  expect(screen.queryByText(/wins/)).not.toBeInTheDocument();
});

it("does not pair a ready mirror with the activity fillPnl withhold sentence", async () => {
  latest.mockResolvedValue(account({
    can_refresh: false,
    performance: {
      status: "unavailable",
      currency: "USDT",
      gross_pnl: null,
      fees: null,
      funding: null,
      net_pnl: null,
      verified_closed_trades: 0,
      unresolved_trades: 0,
      manual_test_trades: 0,
      strategy_closed_trades: null,
      coverage: "Manual coverage is separate.",
      activity_sync_status: "partial",
      activity_coverage: "Win rate and realized P&L are withheld. fillPnl is not established as gross or net of fees or funding.",
      manual_mirror_status: "ready",
      manual_mirror_reason: "Closed BloFin demo manual journal entries.",
      win_rate: "1.0000",
      trade_count: 1,
      unresolved_round_trips: 2,
      wins: 1,
      losses: 0,
      realized_pnl: "0.186",
      average_r: "0.9300",
      window_start: "2026-10-17T12:00:00Z",
      window_end: "2026-10-17T12:00:00Z",
    },
  }));
  render(<BloFinDemoAccountCard />);
  await waitFor(() => {
    expect(screen.getByTestId("dashboard-win-rate")).toHaveTextContent("100.0%");
  });
  expect(screen.getByTestId("dashboard-pnl")).toHaveTextContent(/^Realized PnL0\.186 USDT$/);
  expect(screen.queryByText(/fillPnl is not established/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Window /)).not.toBeInTheDocument();
});

it("keeps win rate hidden when a mirrored trade is unverified", async () => {
  latest.mockResolvedValue(account({
    can_refresh: false,
    performance: {
      status: "unavailable",
      currency: "USDT",
      gross_pnl: null,
      fees: null,
      funding: null,
      net_pnl: null,
      verified_closed_trades: 0,
      unresolved_trades: 0,
      manual_test_trades: 0,
      strategy_closed_trades: null,
      coverage: "Manual coverage is separate.",
      activity_sync_status: "ready",
      win_rate: "0.5000",
      realized_pnl: "2.87",
      manual_mirror_status: "withheld",
      manual_mirror_reason: "Win rate stays withheld. fillPnl remains undocumented.",
    },
  }));
  render(<BloFinDemoAccountCard />);
  await waitFor(() =>
    expect(screen.getByTestId("dashboard-win-rate")).toHaveTextContent(/^Win rate—Withheld$/),
  );
  expect(screen.getByTestId("dashboard-pnl")).toHaveTextContent(/^Realized PnL— USDTWithheld$/);
  expect(screen.queryByText(/fillPnl/)).not.toBeInTheDocument();
});

it("keeps verified partial performance detail off the Dashboard tiles", async () => {
  latest.mockResolvedValue(account({ can_refresh: false, performance: {
    status: "partial", currency: "USDT", gross_pnl: "0.007656", fees: "0.0099", funding: null, net_pnl: null,
    verified_closed_trades: 1, unresolved_trades: 2, manual_test_trades: 1, strategy_closed_trades: 0,
    coverage: "Partial history: only exact linked AlphaTrade activity.",
  } }));
  render(<BloFinDemoAccountCard />);
  await loaded();
  expect(screen.getByTestId("dashboard-equity")).toHaveTextContent("1,001.50 USD");
  expect(screen.getByTestId("dashboard-available")).toHaveTextContent("900.13 USDT");
  expect(screen.getByTestId("dashboard-open-count")).toHaveTextContent("1");
  expect(screen.getByTestId("dashboard-pnl")).toHaveTextContent("— USDT");
  expect(screen.queryByText(/Partial history/)).not.toBeInTheDocument();
  expect(screen.queryByText("0.007656 USDT")).not.toBeInTheDocument();
  expect(screen.queryByText(/Manual connectivity tests are excluded/)).not.toBeInTheDocument();
});

it("retains original successful timestamps after failed refresh", async () => {
  const synced = "2026-10-08T12:00:00Z";
  const payload = account({ synced_at: synced });
  latest.mockResolvedValue(payload);
  refresh.mockResolvedValue(payload);
  render(<BloFinDemoAccountCard />);
  const timestamp = await screen.findByText(/^Last synced /);
  const original = timestamp.textContent ?? "";
  refresh.mockRejectedValue(new Error("unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh demo account" }));
  await screen.findByRole("alert");
  expect(screen.getByTestId("demo-last-synced").textContent).toBe(
    original,
  );
});

it("renders only the simplified account summary", async () => {
  latest.mockResolvedValue(account({
    performance: {
      status: "partial", currency: "USDT", gross_pnl: null, fees: null, funding: null, net_pnl: null,
      verified_closed_trades: 1, unresolved_trades: 0, manual_test_trades: 1, strategy_closed_trades: null,
      coverage: "Partial coverage: verified AlphaTrade manual demo lifecycles only.",
      activity_sync_status: "partial",
      activity_coverage: "Win rate and realized P&L are withheld. fillPnl is not established as gross or net of fees or funding.",
      win_rate: null, realized_pnl: null,
      window_start: "2026-10-21T19:44:00Z", window_end: "2026-11-11T00:14:00Z",
    },
  }));
  render(<BloFinDemoAccountCard />);
  await loaded();
  const card = screen.getByTestId("dashboard-demo-account");
  expect(screen.getByText("BloFin demo account")).toBeInTheDocument();
  expect(screen.queryByText("BLOFIN_DEMO")).not.toBeInTheDocument();
  expect(screen.getByText("Fresh snapshot")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Exchange" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Refresh demo account" })).toBeInTheDocument();
  for (const id of ["dashboard-equity", "dashboard-available", "dashboard-open-count", "dashboard-win-rate", "dashboard-pnl"]) {
    expect(screen.getByTestId(id)).toBeInTheDocument();
  }
  expect(screen.getByTestId("demo-last-synced")).toHaveTextContent(/^Last synced /);
  for (const removed of [
    /Snapshot scope/, /Performance coverage/, /Partial coverage/, /Native demo account snapshot/,
    /fillPnl/, /Window /, /Balances by currency/, /USDT balance/, /Native open positions/,
    /Native account valuation/, /settlement asset/, /fee basis/, /flat start/, /Last refresh attempt/,
  ]) {
    expect(card).not.toHaveTextContent(removed);
  }
  expect(card.querySelector("details")).toBeNull();
});
