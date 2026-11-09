import { describe, expect, it } from "vitest";
import { voiceBudget, voiceReason } from "./voice-reasons";

describe("voice reason codes", () => {
  it("says holds, not a monthly limit, when pending sessions hold the budget", () => {
    const held = voiceReason("voice_budget_held_by_pending_sessions");
    expect(held).toEqual({ limit: false, text: "Voice budget temporarily held by pending sessions." });
    expect(held?.text).not.toMatch(/monthly/i);
  });

  it("keeps consumed monthly quota as a monthly limit", () => {
    for (const code of ["voice_monthly_cost_quota_exhausted", "voice_monthly_token_quota_exhausted"])
      expect(voiceReason(code)).toEqual({ limit: true, text: "Monthly voice limit reached." });
  });
});

describe("voice budget breakdown", () => {
  it("prefers minutes, falls back to tokens, and is null without both sides", () => {
    expect(voiceBudget({ used_minutes: 12.5, held_minutes: 3.4, used_tokens: 1, held_tokens: 2 }))
      .toBe("Used 12.5 min · Held 3.4 min");
    expect(voiceBudget({ used_minutes: null, held_minutes: null, used_tokens: 1200, held_tokens: 34000 }))
      .toBe("Used 1,200 tokens · Held 34,000 tokens");
    expect(voiceBudget({ used_minutes: 1 })).toBeNull();
    expect(voiceBudget({})).toBeNull();
  });
});
