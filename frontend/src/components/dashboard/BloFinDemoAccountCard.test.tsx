import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
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

beforeEach(() => {
  latest.mockReset().mockResolvedValue(account());
  refresh.mockReset().mockResolvedValue(account());
});

it("does not preserve another account's snapshot when a new account read fails", async () => {
  latest.mockResolvedValueOnce(account({ account_id: "first-account" }))
    .mockResolvedValueOnce(account({ account_id: "second-account", status: "unavailable", total_equity_usd: null }));
  const { rerender } = render(<BloFinDemoAccountCard refreshKey={0} />);
  expect(await screen.findByText("Native demo account snapshot.")).toBeInTheDocument();
  expect(screen.getByTestId("dashboard-equity")).toHaveTextContent("1,001.5");
  rerender(<BloFinDemoAccountCard refreshKey={1} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("refresh failed");
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
    expect(await screen.findByText("USDT balance")).toBeInTheDocument();
    expect(screen.getByText(/0.1 contracts/)).toBeInTheDocument();
    expect(screen.getByText("1,001.50 USD")).toBeInTheDocument();
    expect(screen.getByText("Equity: 1,002.25 USDT")).toBeInTheDocument();
    expect(screen.getByText("Base quantity: 0.0001 BTC")).toBeInTheDocument();
    expect(screen.getByText(/Unrealized PnL —/)).toBeInTheDocument();
    expect(screen.getByText("BLOFIN_DEMO")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Exchange settings" }),
    ).toHaveAttribute("href", "/settings/exchange");
    expect(refresh).not.toHaveBeenCalled();
  });

  it("deduplicates repeated explicit refresh clicks", async () => {
    let resolve!: (data: DashboardDemoAccount) => void;
    refresh.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    render(<BloFinDemoAccountCard />);
    const button = await screen.findByRole("button", {
      name: "Refresh demo account",
    });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    expect(
      screen.getByText(/Showing the previous saved snapshot/),
    ).toBeInTheDocument();
    await act(async () =>
      resolve(account({ positions: [], position_count: 0 })),
    );
    expect(
      await screen.findByText("No native open positions at this snapshot."),
    ).toBeInTheDocument();
    expect(latest).toHaveBeenCalledTimes(1);
  });

  it("Dashboard refresh reloads saved evidence without requesting native sync", async () => {
    const view = render(<BloFinDemoAccountCard refreshKey={0} />);
    await screen.findByText("USDT balance");
    latest.mockResolvedValue(account({ status: "stale" }));
    view.rerender(<BloFinDemoAccountCard refreshKey={1} />);
    expect(await screen.findByText("Stale snapshot")).toBeInTheDocument();
    expect(latest).toHaveBeenCalledTimes(2);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("does not let an older saved read overwrite newer saved evidence", async () => {
    let resolveOld!: (data: DashboardDemoAccount) => void;
    latest.mockReturnValue(
      new Promise((done) => {
        resolveOld = done;
      }),
    );
    const view = render(<BloFinDemoAccountCard />);
    latest.mockResolvedValue(account({ positions: [], position_count: 0 }));
    view.rerender(<BloFinDemoAccountCard refreshKey={1} />);
    expect(
      await screen.findByText("No native open positions at this snapshot."),
    ).toBeInTheDocument();
    await act(async () => resolveOld(account({ status: "stale" })));
    expect(screen.getByText("Fresh snapshot")).toBeInTheDocument();
    expect(screen.queryByText(/0.1 contracts/)).not.toBeInTheDocument();
  });

  it("Dashboard reload cannot overtake a pending native refresh", async () => {
    let resolve!: (data: DashboardDemoAccount) => void;
    refresh.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const view = render(<BloFinDemoAccountCard />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Refresh demo account" }),
    );
    view.rerender(<BloFinDemoAccountCard refreshKey={1} />);
    expect(latest).toHaveBeenCalledTimes(1);
    await act(async () =>
      resolve(account({ positions: [], position_count: 0 })),
    );
    expect(
      await screen.findByText("No native open positions at this snapshot."),
    ).toBeInTheDocument();
  });

  it("keeps native failure explicit and preserves successful values", async () => {
    render(<BloFinDemoAccountCard />);
    await screen.findByText("USDT balance");
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
    expect(await screen.findByRole("alert")).toHaveTextContent("refresh failed");
    expect(screen.getByText("USDT balance")).toBeInTheDocument();
    expect(screen.getByText("Stale snapshot")).toBeInTheDocument();
    expect(
      screen.queryByText(/No native open positions/),
    ).not.toBeInTheDocument();
  });

  it("preserves values on HTTP failure, uses safe error text and supports retry", async () => {
    render(<BloFinDemoAccountCard />);
    await screen.findByText("USDT balance");
    refresh.mockRejectedValue(new Error("opaque raw error"));
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh demo account" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "refresh failed",
    );
    expect(screen.getByText("USDT balance")).toBeInTheDocument();
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
      latest.mockResolvedValue(
        account({
          status,
          positions: [],
          balances: [],
          position_count: null,
          message: "Configure or refresh demo account.",
          can_refresh: status !== "inactive",
        }),
      );
      render(<BloFinDemoAccountCard />);
      await screen.findByText("Configure or refresh demo account.");
      expect(
        screen.queryByText(/No native open positions/),
      ).not.toBeInTheDocument();
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
    await screen.findByText("USDT balance");
    expect(
      screen.queryByRole("button", { name: "Refresh demo account" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/Native open positions · at least 1/),
    ).toBeInTheDocument();
    expect(screen.getByText(/owner can refresh/)).toBeInTheDocument();
  });

  it("marks the snapshot stale when its freshness window expires while open", async () => {
    vi.useFakeTimers();
    latest.mockResolvedValue(
      account({ expires_at: new Date(Date.now() + 1000).toISOString() }),
    );
    render(<BloFinDemoAccountCard />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText("Fresh snapshot")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(1001);
    });
    expect(screen.getByText("Stale snapshot")).toBeInTheDocument();
    expect(latest).toHaveBeenCalledTimes(1);
    expect(refresh).not.toHaveBeenCalled();
  });
  it("automatically refreshes native evidence while visible and pauses when hidden", async () => {
    vi.useFakeTimers();
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    render(<BloFinDemoAccountCard />);
    await act(async () => {});
    await act(async () => { vi.advanceTimersByTime(DEMO_REFRESH_INTERVAL_MS); });
    expect(refresh).toHaveBeenCalledTimes(1);
    visibility.mockReturnValue("hidden");
    fireEvent(document, new Event("visibilitychange"));
    await act(async () => { vi.advanceTimersByTime(DEMO_REFRESH_INTERVAL_MS * 4); });
    expect(refresh).toHaveBeenCalledTimes(1);
    visibility.mockReturnValue("visible");
    fireEvent(document, new Event("visibilitychange"));
    await act(async () => { vi.advanceTimersByTime(1); });
    expect(refresh).toHaveBeenCalledTimes(2);
    // Repeated visibility events do not create a burst of native requests.
    fireEvent(document, new Event("visibilitychange"));
    await act(async () => { vi.advanceTimersByTime(1); });
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("backs off failures up to fifteen minutes and recovers to normal cadence", async () => {
    vi.useFakeTimers();
    refresh.mockRejectedValue(new Error("raw upstream failure"));
    render(<BloFinDemoAccountCard />);
    await act(async () => {});
    await act(async () => { vi.advanceTimersByTime(DEMO_REFRESH_INTERVAL_MS); });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(screen.getByText("USDT balance")).toBeInTheDocument();
    expect(screen.getByText("Stale snapshot")).toBeInTheDocument();
    for (const [delay, count] of [[360_000, 2], [720_000, 3], [DEMO_MAX_BACKOFF_MS, 4]]) {
      await act(async () => { vi.advanceTimersByTime(delay - 1); });
      expect(refresh).toHaveBeenCalledTimes(count - 1);
      await act(async () => { vi.advanceTimersByTime(1); });
      expect(refresh).toHaveBeenCalledTimes(count);
    }
    refresh.mockImplementation(async () => account());
    await act(async () => { vi.advanceTimersByTime(DEMO_MAX_BACKOFF_MS); });
    expect(screen.getByText("Fresh snapshot")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(DEMO_REFRESH_INTERVAL_MS); });
    expect(refresh).toHaveBeenCalledTimes(6);
  });

  it("times out a hung request, aborts transport and ignores its late result", async () => {
    vi.useFakeTimers();
    let late!: (value: DashboardDemoAccount) => void;
    refresh.mockReturnValue(new Promise((done) => { late = done; }));
    render(<BloFinDemoAccountCard />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Refresh demo account" }));
    await act(async () => { vi.advanceTimersByTime(DEMO_REQUEST_TIMEOUT_MS); });
    expect(refresh.mock.calls[0][0]?.aborted).toBe(true);
    expect(screen.getByRole("button", { name: "Refresh demo account" })).toBeEnabled();
    expect(screen.getByText("Stale snapshot")).toBeInTheDocument();
    await act(async () => late(account({ positions: [], position_count: 0 })));
    expect(screen.getByText(/0.1 contracts/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("deduplicates automatic sync against manual and Dashboard refresh and cancels on unmount", async () => {
    vi.useFakeTimers();
    refresh.mockReturnValue(new Promise(() => {}));
    const view = render(<BloFinDemoAccountCard />);
    await act(async () => {});
    await act(async () => { vi.advanceTimersByTime(DEMO_REFRESH_INTERVAL_MS); });
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
    expect(await screen.findByRole("alert")).toHaveTextContent("refresh failed");
    expect(screen.getByText(/0.1 contracts/)).toBeInTheDocument();
    expect(screen.getByText("Stale snapshot")).toBeInTheDocument();
  });

  it("shows unknown equity and base quantity without inventing values", async () => {
    const data = account();
    latest.mockResolvedValue(account({
      total_equity_usd: null,
      balances: data.balances.map((b) => ({ ...b, equity: null })),
      positions: data.positions.map((p) => ({ ...p, base_asset: null, base_quantity: null })),
    }));
    render(<BloFinDemoAccountCard />);
    // Missing equity is also rendered before load; wait for the native balance row.
    expect(await screen.findByText("Equity: — USDT")).toBeInTheDocument();
    expect(screen.getByText("— USD")).toBeInTheDocument();
    expect(screen.getByText(/Base quantity: — \(unverified instrument metadata\)/)).toBeInTheDocument();
  });

});

it("uses native scope and verified partial performance without rounding tiny PnL to zero", async () => {
  latest.mockResolvedValue(account({ performance: {
    status: "partial", currency: "USDT", gross_pnl: "0.007656", fees: "0.0099", funding: null, net_pnl: null,
    verified_closed_trades: 1, unresolved_trades: 2, manual_test_trades: 1, strategy_closed_trades: 0,
    coverage: "Partial history: only exact linked AlphaTrade activity.",
  } }));
  render(<BloFinDemoAccountCard />);
  await screen.findByText("Partial history: only exact linked AlphaTrade activity.");
  expect(screen.getByTestId("dashboard-equity")).toHaveTextContent("1,001.50 USD");
  expect(screen.getByTestId("dashboard-available")).toHaveTextContent("900.13 USDT");
  expect(screen.getByTestId("dashboard-open-count")).toHaveTextContent("1");
  expect(screen.getByTestId("dashboard-pnl")).toHaveTextContent("— USDT");
  expect(screen.getByText("0.007656 USDT")).toBeInTheDocument();
  expect(screen.getByText(/Manual connectivity tests are excluded/)).toBeInTheDocument();
});

it("retains original successful timestamps after failed refresh", async () => {
  const synced = "2026-10-08T12:00:00Z";
  latest.mockResolvedValue(account({ synced_at: synced }));
  render(<BloFinDemoAccountCard />);
  const timestamp = await screen.findByText(/^As of /);
  const original = timestamp.textContent;
  refresh.mockRejectedValue(new Error("unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh demo account" }));
  await screen.findByRole("alert");
  expect(screen.getByText(/^As of /).textContent).toBe(original);
});
