import { describe, expect, it } from "vitest";

import {
  buildPostureAnnouncement,
  resolveAdviceDisplay,
  resolveExecutionDisplay,
  resolveRiskDisplay,
} from "@/components/layout/status-strip-state";
import type { KillSwitchStatus } from "@/lib/api/types";

function killSwitch(partial: Partial<KillSwitchStatus>): KillSwitchStatus {
  return {
    organization_id: "org",
    active: false,
    reason: null,
    activated_by: null,
    activated_at: null,
    deactivated_by: null,
    deactivated_at: null,
    version: 1,
    scope: "organization",
    global_active: false,
    execution_blocked: false,
    ...partial,
  };
}

describe("AT-040 StatusStrip safety truth", () => {
  it("paper + real disabled shows Demo with paper tone", () => {
    const display = resolveExecutionDisplay("paper", false, true);
    expect(display.paperConfirmed).toBe(true);
    expect(display.label).toBe("Demo");
    expect(display.tone).toBe("paper");
  });

  it("paper + real enabled is a safety conflict without paper styling", () => {
    const display = resolveExecutionDisplay("paper", true, true);
    expect(display.paperConfirmed).toBe(false);
    expect(display.label).toBe("Safety conflict");
    expect(display.tone).not.toBe("paper");
    expect(display.label.toLowerCase()).not.toContain("paper");
  });

  it("live + real disabled never uses paper tone", () => {
    const display = resolveExecutionDisplay("live", false, true);
    expect(display.paperConfirmed).toBe(false);
    expect(display.tone).not.toBe("paper");
    expect(display.label).toBe("LIVE");
  });

  it("missing health posture is unverified", () => {
    const display = resolveExecutionDisplay(null, null, false);
    expect(display.paperConfirmed).toBe(false);
    expect(display.label).toBe("Execution unverified");
    expect(display.tone).toBe("warn");
  });

  it("partial posture is unverified and never paper", () => {
    expect(resolveExecutionDisplay("paper", null, true).label).toBe("Execution unverified");
    expect(resolveExecutionDisplay(null, false, true).label).toBe("Execution unverified");
    expect(resolveExecutionDisplay("paper", null, true).tone).not.toBe("paper");
  });

  it("contradictory states never return a PAPER badge label", () => {
    const cases: Array<[string | null, boolean | null, boolean]> = [
      ["paper", true, true],
      ["live", false, true],
      ["live", true, true],
      [null, null, false],
      ["paper", null, true],
      [null, false, true],
    ];
    for (const [mode, real, known] of cases) {
      const display = resolveExecutionDisplay(mode, real, known);
      expect(display.label).not.toBe("Demo");
      expect(display.tone).not.toBe("paper");
    }
  });

  it("kill-switch loaded and inactive shows Risk low", () => {
    const risk = resolveRiskDisplay({
      killSwitchStatus: killSwitch({ execution_blocked: false, active: false }),
      killSwitchError: null,
      statusLoading: false,
    });
    expect(risk.level).toBe("low");
    expect(risk.label).toBe("Risk low");
    expect(risk.known).toBe(true);
  });

  it("kill-switch blocked shows critical risk", () => {
    const risk = resolveRiskDisplay({
      killSwitchStatus: killSwitch({ execution_blocked: true, active: true }),
      killSwitchError: null,
      statusLoading: false,
    });
    expect(risk.level).toBe("critical");
    expect(risk.label).toBe("Risk critical");
  });

  it("kill-switch status missing never becomes Risk low", () => {
    const risk = resolveRiskDisplay({
      killSwitchStatus: null,
      killSwitchError: null,
      statusLoading: false,
    });
    expect(risk.level).toBeNull();
    expect(risk.label).toBe("Risk unknown");
    expect(risk.known).toBe(false);
  });

  it("kill-switch request error shows Risk unknown", () => {
    const risk = resolveRiskDisplay({
      killSwitchStatus: null,
      killSwitchError: "Failed to load kill switch",
      statusLoading: false,
    });
    expect(risk.level).toBeNull();
    expect(risk.label).toBe("Risk unknown");
  });

  it("kill-switch still loading with null status shows Risk unknown", () => {
    const risk = resolveRiskDisplay({
      killSwitchStatus: null,
      killSwitchError: null,
      statusLoading: true,
    });
    expect(risk.level).toBeNull();
    expect(risk.label).toBe("Risk unknown");
  });
});

describe("AT-040 StatusStrip advice truth", () => {
  it("confirmed paper may say Paper-only research", () => {
    const advice = resolveAdviceDisplay("paper", false, true);
    expect(advice.text).toContain("Paper-only research");
    expect(advice.text).toContain("Not financial advice");
  });

  it("real trading enabled never claims paper-only research", () => {
    const advice = resolveAdviceDisplay("paper", true, true);
    expect(advice.text.toLowerCase()).not.toContain("paper-only research");
    expect(advice.text).toContain("Real trading appears enabled");
  });

  it("live execution mode never claims paper-only research", () => {
    const advice = resolveAdviceDisplay("live", false, true);
    expect(advice.text.toLowerCase()).not.toContain("paper-only research");
    expect(advice.text).toContain("LIVE");
    expect(advice.text).toContain("not paper-only");
  });

  it("unknown posture uses neutral unverified wording", () => {
    const advice = resolveAdviceDisplay(null, null, false);
    expect(advice.text).toBe("Trading environment not verified. Not financial advice.");
    expect(advice.text.toLowerCase()).not.toContain("paper-only");
  });

  it("partial posture uses neutral unverified wording", () => {
    expect(resolveAdviceDisplay("paper", null, true).text).toBe(
      "Trading environment not verified. Not financial advice.",
    );
    expect(resolveAdviceDisplay(null, false, true).text).toBe(
      "Trading environment not verified. Not financial advice.",
    );
  });

  it("never renders contradictory paper-only text outside confirmed paper", () => {
    const cases: Array<[string | null, boolean | null, boolean]> = [
      ["paper", true, true],
      ["live", false, true],
      ["live", true, true],
      [null, null, false],
      ["paper", null, true],
      [null, false, true],
    ];
    for (const [mode, real, known] of cases) {
      const advice = resolveAdviceDisplay(mode, real, known);
      expect(advice.text.toLowerCase()).not.toContain("paper-only research");
    }
  });
});

describe("buildPostureAnnouncement (FP2-114)", () => {
  function announce(
    executionMode: string | null,
    realTradingEnabled: boolean | null,
    postureKnown: boolean,
    status: KillSwitchStatus | null,
  ): string {
    return buildPostureAnnouncement({
      execution: resolveExecutionDisplay(executionMode, realTradingEnabled, postureKnown),
      realTradingEnabled,
      risk: resolveRiskDisplay({
        killSwitchStatus: status,
        killSwitchError: null,
        statusLoading: false,
      }),
    });
  }

  it("states verified paper posture, real-trading state and risk level", () => {
    expect(announce("paper", false, true, killSwitch({}))).toBe(
      "Trading posture: execution Demo, verified; real trading disabled; risk low.",
    );
  });

  it("reports a blocked kill switch as critical risk", () => {
    expect(announce("paper", false, true, killSwitch({ execution_blocked: true }))).toContain(
      "risk critical.",
    );
  });

  it("never claims verified paper while posture is unknown", () => {
    const text = announce(null, null, false, null);
    expect(text).toBe(
      "Trading posture: execution unverified; real trading unverified; risk unknown.",
    );
    expect(text).not.toContain("Demo");
  });

  it("surfaces a safety conflict rather than a paper claim", () => {
    const text = announce("paper", true, true, killSwitch({}));
    expect(text).toContain("safety conflict");
    expect(text).toContain("real trading enabled");
    expect(text).not.toContain("Demo");
  });
});
