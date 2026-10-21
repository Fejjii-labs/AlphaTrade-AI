import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ExchangeDemoAccountSync } from "./ExchangeDemoAccountSync";
import { DEMO_REFRESH_INTERVAL_MS } from "@/components/dashboard/demoAccountRefresh";
import {
  demoAccountApi,
  type DashboardDemoAccount,
} from "@/lib/api/dashboard-demo-account";

vi.mock("@/lib/api/dashboard-demo-account", () => ({
  demoAccountApi: { latest: vi.fn(), refresh: vi.fn() },
}));

const latest = vi.mocked(demoAccountApi.latest);
const refresh = vi.mocked(demoAccountApi.refresh);

function account(overrides: Partial<DashboardDemoAccount> = {}): DashboardDemoAccount {
  return {
    venue: "BLOFIN_DEMO",
    read_only: true,
    status: "ok",
    can_refresh: true,
    snapshot_id: "exchange-snapshot",
    synced_at: "2026-10-18T12:00:00Z",
    expires_at: new Date(Date.now() + 300_000).toISOString(),
    total_equity_usd: "10",
    refresh_error: null,
    last_attempt_at: null,
    balances: [],
    positions: [],
    balances_truncated: false,
    positions_truncated: false,
    position_count: 0,
    message: "Native demo account snapshot.",
    ...overrides,
  };
}

beforeEach(() => {
  latest.mockReset().mockResolvedValue(account());
  refresh.mockReset().mockResolvedValue(account());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("exchange demo sync", () => {
  it("refreshes as soon as an owner opens the page", async () => {
    render(<ExchangeDemoAccountSync />);
    expect(await screen.findByTestId("exchange-last-synced")).toHaveTextContent(
      "Last synced",
    );
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("exchange-last-synced")).not.toHaveTextContent("stale");
  });

  it("does not refresh for a reader and shows a stale snapshot", async () => {
    latest.mockResolvedValue(
      account({
        can_refresh: false,
        status: "stale",
        expires_at: new Date(Date.now() - 1000).toISOString(),
      }),
    );
    render(<ExchangeDemoAccountSync />);
    expect(await screen.findByTestId("exchange-last-synced")).toHaveTextContent("stale");
    expect(refresh).not.toHaveBeenCalled();
  });

  it("polls after the configured interval", async () => {
    vi.useFakeTimers();
    render(<ExchangeDemoAccountSync />);
    await act(async () => {});
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(DEMO_REFRESH_INTERVAL_MS);
    });
    expect(refresh).toHaveBeenCalledTimes(2);
  });
});
