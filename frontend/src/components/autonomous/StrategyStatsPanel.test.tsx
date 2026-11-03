import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StrategyStatsPanel } from "./StrategyStatsPanel";

const useAsyncDataMock = vi.fn();
vi.mock("@/hooks/useAsyncData", () => ({ useAsyncData: (...args: unknown[]) => useAsyncDataMock(...args) }));
vi.mock("@/lib/api", () => ({ api: { autonomous: { strategyStats: vi.fn() } } }));

const stats = (overrides: Record<string, unknown> = {}) => ({
  closed_trades: 0,
  wins: 0,
  losses: 0,
  win_rate: null,
  net_pnl: "0",
  open_positions: 0,
  last_trade_at: null,
  ...overrides,
});

afterEach(cleanup);

describe("StrategyStatsPanel", () => {
  it("shows status, trades, win rate, P&L and open positions per strategy", () => {
    useAsyncDataMock.mockReturnValue({
      loading: false,
      error: null,
      reload: vi.fn(),
      data: {
        strategies: [
          {
            strategy_id: "s1",
            name: "Nested BTC",
            setup_type: "nested_continuation",
            enabled: true,
            paper_eligible: true,
            bot_selected: true,
            last_setup_at: "2026-10-27T09:30:00Z",
            stats: stats({ closed_trades: 3, wins: 2, losses: 1, win_rate: 0.6667, net_pnl: "90.25", open_positions: 1 }),
          },
          {
            strategy_id: "s2",
            name: "Paused pullback",
            setup_type: "htf_trend_pullback",
            enabled: false,
            paper_eligible: false,
            bot_selected: false,
            last_setup_at: null,
            stats: stats(),
          },
        ],
        unattributed: stats({ closed_trades: 1, net_pnl: "-5" }),
        excluded_sources: ["backtest"],
      },
    });
    render(<StrategyStatsPanel />);
    const nested = screen.getByTestId("data-table-row-s1");
    expect(within(nested).getByText("Enabled")).toBeInTheDocument();
    expect(within(nested).getByText("Bot target")).toBeInTheDocument();
    expect(nested).toHaveTextContent("66.7%");
    expect(nested).toHaveTextContent("+90.25");
    const paused = screen.getByTestId("data-table-row-s2");
    expect(within(paused).getByText("Paused")).toBeInTheDocument();
    expect(paused).toHaveTextContent("None yet");
    expect(screen.getByTestId("strategy-stats-note")).toHaveTextContent("Trades without a strategy: 1 closed, 0 open, P&L −5.00.");
  });

  it("says so when stats cannot load", () => {
    useAsyncDataMock.mockReturnValue({ loading: false, error: "boom", reload: vi.fn(), data: null });
    render(<StrategyStatsPanel />);
    expect(screen.getByText("Strategy stats unavailable.")).toBeInTheDocument();
  });
});
