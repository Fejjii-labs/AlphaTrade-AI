import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VoiceOrb } from "./VoiceOrb";

function reducedMotion(matches: boolean) {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({
    matches, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }));
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("VoiceOrb (synthetic meter, no microphone)", () => {
  it("is a full-screen dialog with a state label, no identifiers, and End", () => {
    const onEnd = vi.fn();
    render(<VoiceOrb state="thinking" detail="2:41 left" onEnd={onEnd} />);
    const dialog = screen.getByRole("dialog", { name: "Voice conversation" });
    expect(dialog.className).toContain("fixed inset-0");
    expect(screen.getByRole("status")).toHaveTextContent("Thinking…");
    expect(dialog.textContent).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}/);
    expect(screen.getByRole("button", { name: "End" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "End" }));
    expect(onEnd).toHaveBeenCalledOnce();
  });

  it("Escape ends the conversation", () => {
    const onEnd = vi.fn();
    render(<VoiceOrb state="listening" onEnd={onEnd} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onEnd).toHaveBeenCalledOnce();
  });

  it("reacts to the live level and shows speaking while reply audio plays", async () => {
    vi.useFakeTimers();
    let speaking = false;
    const meter = vi.fn(() => ({ speaking, level: 0.2 }));
    render(<VoiceOrb state="listening" meter={meter} onEnd={vi.fn()} />);
    const orb = screen.getByTestId("voice-orb");
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(meter).toHaveBeenCalled();
    expect(Number(orb.style.getPropertyValue("--orb-level"))).toBeGreaterThan(0);
    expect(orb.dataset.state).toBe("listening");
    speaking = true;
    await act(async () => { vi.advanceTimersByTime(100); });
    expect(orb.dataset.state).toBe("speaking");
    expect(screen.getByRole("status")).toHaveTextContent("Speaking");
  });

  it("respects prefers-reduced-motion: no level-driven scaling", async () => {
    vi.useFakeTimers();
    reducedMotion(true);
    const meter = vi.fn(() => ({ speaking: false, level: 0.9 }));
    render(<VoiceOrb state="listening" meter={meter} onEnd={vi.fn()} />);
    const orb = screen.getByTestId("voice-orb");
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(orb.dataset.reducedMotion).toBe("true");
    expect(orb.style.getPropertyValue("--orb-level")).toBe("0");
  });
});
