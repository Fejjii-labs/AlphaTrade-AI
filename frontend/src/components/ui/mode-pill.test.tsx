import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ModePill, resolveModePill } from "@/components/ui/mode-pill";

const blocked = "Real-money trading: permanently blocked";

describe("resolveModePill fail-closed", () => {
  it("is unverified when posture is missing", () => {
    expect(
      resolveModePill({ executionMode: null, realTradingEnabled: null, postureKnown: false }).kind,
    ).toBe("unverified");
    expect(
      resolveModePill({ executionMode: "paper", realTradingEnabled: null, postureKnown: true }).kind,
    ).toBe("unverified");
    expect(
      resolveModePill({ executionMode: null, realTradingEnabled: false, postureKnown: true }).kind,
    ).toBe("unverified");
  });

  it("shows PAPER only for confirmed paper on the simulator", () => {
    expect(
      resolveModePill({
        executionMode: "paper",
        realTradingEnabled: false,
        postureKnown: true,
      }),
    ).toMatchObject({ kind: "paper", label: "PAPER · Simulator" });
  });

  it("shows DEMO only when paper is confirmed and the context is BloFin demo", () => {
    expect(
      resolveModePill({
        executionMode: "paper",
        realTradingEnabled: false,
        postureKnown: true,
        context: "demo",
      }),
    ).toMatchObject({ kind: "demo", label: "DEMO · BloFin" });
  });

  it("does not show DEMO or PAPER for an unconfirmed demo context", () => {
    const result = resolveModePill({
      executionMode: "paper",
      realTradingEnabled: null,
      postureKnown: true,
      context: "demo",
    });
    expect(result.kind).toBe("unverified");
    expect(result.label).not.toMatch(/PAPER|DEMO/);
  });

  it("treats paper with real trading enabled as a safety conflict", () => {
    const result = resolveModePill({
      executionMode: "paper",
      realTradingEnabled: true,
      postureKnown: true,
    });
    expect(result.kind).toBe("conflict");
    expect(result.label).toBe("SAFETY CONFLICT");
  });

  it("has no live label", () => {
    const result = resolveModePill({
      executionMode: "live",
      realTradingEnabled: false,
      postureKnown: true,
    });
    expect(result.kind).toBe("conflict");
    expect(result.label).not.toMatch(/LIVE|PAPER|DEMO/);
  });

  it("does not treat an uppercase mode string as confirmed paper", () => {
    expect(
      resolveModePill({ executionMode: "PAPER", realTradingEnabled: false, postureKnown: true }).kind,
    ).toBe("conflict");
  });
});

describe("ModePill", () => {
  it("defaults to UNVERIFIED and never PAPER", () => {
    render(<ModePill />);
    const pill = screen.getByTestId("mode-pill");
    expect(pill).toHaveAttribute("data-kind", "unverified");
    expect(pill).toHaveTextContent("UNVERIFIED");
    expect(pill).not.toHaveTextContent("PAPER");
    expect(pill).toHaveAttribute("aria-label", expect.stringContaining(blocked));
  });

  it("opens posture facts and closes on Escape", () => {
    render(
      <ModePill
        executionMode="paper"
        realTradingEnabled={false}
        postureKnown
        paused={false}
        riskLabel="Risk low"
      />,
    );
    fireEvent.click(screen.getByTestId("mode-pill"));
    const popover = screen.getByTestId("mode-pill-popover");
    expect(popover).toHaveTextContent(blocked);
    expect(popover).toHaveTextContent("Risk low");
    expect(popover).toHaveTextContent("Running");
    expect(popover).toHaveTextContent("Not financial advice");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("mode-pill-popover")).not.toBeInTheDocument();
    expect(screen.getByTestId("mode-pill")).toHaveFocus();
  });

  it("marks pause without using a running dot while unknown", () => {
    const { rerender } = render(<ModePill paused={null} />);
    expect(screen.getByTestId("mode-pill")).toHaveAttribute("data-pause", "unknown");
    rerender(<ModePill paused />);
    expect(screen.getByTestId("mode-pill")).toHaveAttribute("data-pause", "paused");
    rerender(
      <ModePill
        executionMode="paper"
        realTradingEnabled={false}
        postureKnown
        context="demo"
        paused={false}
      />,
    );
    const pill = screen.getByTestId("mode-pill");
    expect(pill).toHaveAttribute("data-kind", "demo");
    expect(pill).toHaveAttribute("data-pause", "running");
    expect(pill).toHaveTextContent("DEMO · BloFin");
    expect(pill.className).toContain("text-mode-demo");
  });
});
