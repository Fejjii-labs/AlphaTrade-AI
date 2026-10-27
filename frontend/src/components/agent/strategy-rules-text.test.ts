import { describe, expect, it } from "vitest";

import { strategyRuleLines } from "./strategy-rules-text";

describe("strategyRuleLines", () => {
  it("turns structured rules into readable grouped sentences", () => {
    const groups = strategyRuleLines({
      primary_timeframe: "15m",
      entry_rules: [{
        direction: "long",
        trigger_type: "liquidity_sweep",
        conditions: [{ indicator: "rsi", operator: "lt", value: "30", timeframe: "15m" }],
      }],
      exit_rules: [{ rule_type: "take_profit", r_multiple: 2, size_fraction: 0.5 }],
      no_trade_rules: [{ rule_type: "high_impact_news" }],
    } as never);

    expect(groups).toEqual([
      { group: "Timeframe", lines: ["15m"] },
      { group: "Entry", lines: ["Long on liquidity sweep when RSI < 30 on 15m"] },
      { group: "Exit", lines: ["Take profit 2R 50% of size"] },
      { group: "No trade", lines: ["High impact news"] },
    ]);
    expect(groups.flatMap((g) => g.lines).join(" ")).not.toMatch(/[{}]|_/);
  });

  it("returns nothing for missing or empty rules", () => {
    expect(strategyRuleLines(null)).toEqual([]);
    expect(strategyRuleLines({} as never)).toEqual([]);
  });
});
