import { describe, expect, it } from "vitest";

import { botReasonSentence } from "./bot-reasons";

describe("botReasonSentence", () => {
  it("maps known codes to one plain sentence", () => {
    expect(botReasonSentence("insufficient_daily_loss_allocation")).toBe("Daily loss budget used up.");
    expect(botReasonSentence("demo_leverage_exceeds_profile_max")).toBe("Leverage is above the risk profile maximum.");
    expect(botReasonSentence("kill_switch_active")).toBe("Paused by the kill switch.");
    expect(botReasonSentence("max_trades_per_day")).toBe("Daily trade limit reached.");
    expect(botReasonSentence("MAX_EXPOSURE")).toBe("Too much open exposure.");
  });

  it("never shows a raw code for unknown reasons", () => {
    expect(botReasonSentence("some_new_internal_gate_v2")).toBe("Skipped by a safety check.");
    expect(botReasonSentence(null)).toBeNull();
    expect(botReasonSentence("")).toBeNull();
  });
});
