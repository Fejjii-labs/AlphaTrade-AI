import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { BrainStrategy } from "@/lib/api/brain-types";
import type { WorkerTradingScope } from "@/lib/api/types";
import { expectNoIdentifiers } from "@/test/no-identifiers";

import { BotStrategyGrid, buildGrids, cellStatus, coinLabel } from "./BotStrategyGrid";

const KIND = {
  dline: "d_line/v2",
  nested: "operational_nested_continuation/v1",
  sfp: "swing_failure_pattern/v1",
} as const;

function strategy(kind: string, symbol: string, direction: string, timeframe: string, enabled = true): BrainStrategy {
  const id = `${kind}-${symbol}-${direction}-${timeframe}`;
  return {
    strategy_id: `s-${id}`, version_id: `v-${id}`, version: 1, name: `x ${id}`, status: "approved", enabled,
    execution_permission: "",
    spec: { kind, symbol, direction, trigger_timeframe: timeframe, parameters: {} } as BrainStrategy["spec"],
  };
}

/** Staging today: D-line and Nested trade 1h only; SFP is paused. */
const staging: WorkerTradingScope = {
  reported_at: "2026-11-27T12:00:00Z",
  families: [
    { family: "d_line", trading: true, timeframes: ["1h"] },
    { family: "nested", trading: true, timeframes: ["1h"] },
    { family: "sfp", trading: false, timeframes: [] },
  ],
};

const strategies = [
  strategy(KIND.dline, "BTCUSDT", "long", "1h"),
  strategy(KIND.dline, "BTCUSDT", "short", "1h"),
  strategy(KIND.dline, "BTCUSDT", "long", "15m", false),
  strategy(KIND.dline, "ETHUSDT", "long", "30m"),
  strategy(KIND.nested, "HYPEUSDT", "short", "1h"),
  strategy(KIND.sfp, "ZECUSDT", "long", "1h"),
];

beforeEach(() => {
  vi.spyOn(api.strategyBrain, "overview").mockResolvedValue({
    strategies, setups: [], watched_symbols: ["ETHUSDT", "BTCUSDT"], limitations: [], paper_only: true,
  } as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("cellStatus", () => {
  it("is honest about what the worker trades", () => {
    expect(cellStatus(strategy(KIND.dline, "BTCUSDT", "long", "1h"), staging)).toBe("trading");
    expect(cellStatus(strategy(KIND.dline, "BTCUSDT", "long", "30m"), staging)).toBe("watching");
    expect(cellStatus(strategy(KIND.sfp, "BTCUSDT", "long", "1h"), staging)).toBe("watching");
    expect(cellStatus(strategy(KIND.nested, "BTCUSDT", "long", "1h", false), staging)).toBe("off");
    expect(cellStatus(strategy(KIND.nested, "BTCUSDT", "long", "1h"), null)).toBe("unknown");
    expect(cellStatus(strategy("other/v1", "BTCUSDT", "long", "1h"), staging)).toBe("unknown");
    const anyTimeframe = { ...staging, families: [{ family: "sfp" as const, trading: true, timeframes: null }] };
    expect(cellStatus(strategy(KIND.sfp, "BTCUSDT", "long", "4h"), anyTimeframe)).toBe("trading");
  });
});

describe("buildGrids", () => {
  it("groups by family, orders coins by the watchlist, and adds a timeframe column when needed", () => {
    const { grids, timeframes } = buildGrids(
      [...strategies, strategy(KIND.nested, "BTCUSDT", "long", "4h")],
      staging,
      ["ETHUSDT", "BTCUSDT"],
    );
    expect(grids.map((grid) => grid.label)).toEqual(["D-line", "Nested", "SFP"]);
    expect(grids[0]?.coins.map((coin) => coin.symbol)).toEqual(["ETHUSDT", "BTCUSDT"]);
    expect(timeframes).toEqual(["15m", "30m", "1h", "4h"]);
  });

  it("keeps the best status when one cell holds two versions", () => {
    const { grids } = buildGrids(
      [strategy(KIND.dline, "BTCUSDT", "long", "1h", false), strategy(KIND.dline, "BTCUSDT", "long", "1h")],
      staging,
    );
    expect(grids[0]?.coins[0]?.cells.get("1h:long")).toEqual({ status: "trading", count: 2 });
  });

  it("shows coins without the quote", () => {
    expect(coinLabel("btcusdt")).toBe("BTC");
    expect(coinLabel("ETHUSDC")).toBe("ETHUSDC");
  });
});

describe("BotStrategyGrid", () => {
  it("renders one card per family with coins down, timeframes across, Long and Short per cell", async () => {
    render(<BotStrategyGrid scope={staging} />);
    const dline = await screen.findByTestId("strategy-family-d_line");
    expect(within(dline).getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["Coin", "15m", "30m", "1h"]);
    expect(within(dline).getAllByRole("rowheader").map((th) => th.textContent)).toEqual(["ETH", "BTC"]);
    expect(screen.getByTestId("strategy-cell-d_line-BTCUSDT-1h")).toHaveTextContent("LongTradingShortTrading");
    expect(screen.getByTestId("strategy-cell-d_line-BTCUSDT-15m")).toHaveTextContent("LongOff");
    expect(screen.getByTestId("strategy-cell-d_line-ETHUSDT-30m")).toHaveTextContent("LongWatching only");
    expect(screen.getByTestId("strategy-cell-sfp-ZECUSDT-1h")).toHaveTextContent("LongWatching only");
    expect(dline).toHaveTextContent("2 trading · 1 watching only · 1 off");
    expectNoIdentifiers();
  });

  it("filters by family, direction, timeframe and status, and each chip clears on a second tap", async () => {
    render(<BotStrategyGrid scope={staging} />);
    await screen.findByTestId("bot-strategies");
    const chip = (group: string, name: string) =>
      within(screen.getByRole("toolbar", { name: group })).getByRole("button", { name });

    fireEvent.click(chip("Family", "Nested"));
    expect(screen.queryByTestId("strategy-family-d_line")).not.toBeInTheDocument();
    expect(screen.getByTestId("strategy-family-nested")).toBeInTheDocument();
    fireEvent.click(chip("Family", "Nested"));
    expect(screen.getByTestId("strategy-family-d_line")).toBeInTheDocument();

    fireEvent.click(chip("Direction", "Short"));
    expect(screen.getByTestId("strategy-cell-d_line-BTCUSDT-1h")).toHaveTextContent("ShortTrading");
    expect(screen.getByTestId("strategy-cell-d_line-BTCUSDT-1h")).not.toHaveTextContent("Long");
    fireEvent.click(chip("Direction", "Short"));

    fireEvent.click(chip("Timeframe", "1h"));
    expect(within(screen.getByTestId("strategy-family-d_line")).getAllByRole("columnheader")).toHaveLength(2);
    fireEvent.click(chip("Timeframe", "1h"));

    fireEvent.click(chip("Status", "Watching only"));
    expect(screen.queryByTestId("strategy-family-nested")).not.toBeInTheDocument();
    expect(screen.getByTestId("strategy-cell-d_line-ETHUSDT-30m")).toHaveTextContent("Watching only");
    expect(screen.queryByTestId("strategy-row-d_line-BTCUSDT")).not.toBeInTheDocument();

    fireEvent.click(chip("Family", "Nested"));
    expect(screen.getByText("No strategies match these filters.")).toBeInTheDocument();
  });

  it("says Status unknown, never Trading, when the worker has not reported its scope", async () => {
    render(<BotStrategyGrid scope={null} />);
    expect(await screen.findByTestId("strategy-scope-unknown")).toBeInTheDocument();
    expect(screen.getByTestId("strategy-cell-d_line-BTCUSDT-1h")).toHaveTextContent("LongStatus unknown");
    expect(screen.getByTestId("bot-strategies").querySelectorAll('[data-status="trading"]')).toHaveLength(0);
  });
});
