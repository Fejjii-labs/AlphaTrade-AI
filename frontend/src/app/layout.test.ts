import { describe, expect, it, vi } from "vitest";

vi.mock("next/font/google", () => ({
  Figtree: () => ({ variable: "--font-figtree" }),
  JetBrains_Mono: () => ({ variable: "--font-mono" }),
}));

const { viewport } = await import("@/app/layout");

describe("root viewport metadata", () => {
  it("leaves browser pinch zoom unrestricted (FP2-114, WCAG 1.4.4)", () => {
    expect(viewport.width).toBe("device-width");
    expect(viewport.initialScale).toBe(1);
    expect(viewport.maximumScale).toBeUndefined();
    expect(viewport.userScalable).toBeUndefined();
  });

  it("enables iOS safe-area insets via viewport-fit=cover", () => {
    expect(viewport.viewportFit).toBe("cover");
  });
});
