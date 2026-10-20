import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

type Theme = "dark" | "light";
type Vars = Record<string, string>;

const AA = 4.5;

function parseTheme(css: string, theme: Theme): Vars {
  const lightAt = css.indexOf('[data-theme="light"]');
  const slice = theme === "dark" ? css.slice(0, lightAt) : css.slice(lightAt);
  const vars: Vars = {};
  for (const match of slice.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    vars[match[1]] = match[2].trim();
  }
  return vars;
}

function color(vars: Vars, name: string, seen = new Set<string>()): string {
  const raw = vars[name];
  if (!raw) throw new Error(`missing ${name}`);
  const ref = raw.match(/^var\((--[\w-]+)\)$/);
  if (!ref) return raw;
  if (seen.has(name)) throw new Error(`cycle at ${name}`);
  seen.add(name);
  return color(vars, ref[1], seen);
}

function channel(value: number): number {
  const s = value / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function rgb(hex: string): [number, number, number] {
  const n = hex.replace("#", "");
  return [
    Number.parseInt(n.slice(0, 2), 16),
    Number.parseInt(n.slice(2, 4), 16),
    Number.parseInt(n.slice(4, 6), 16),
  ];
}

function luminance(hex: string): number {
  const [r, g, b] = rgb(hex);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(foreground: string, background: string): number {
  const lighter = Math.max(luminance(foreground), luminance(background));
  const darker = Math.min(luminance(foreground), luminance(background));
  return (lighter + 0.05) / (darker + 0.05);
}

function composite(overlay: string, background: string): string {
  const match = overlay.match(/rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)/);
  if (!match) throw new Error(`expected rgba overlay, got ${overlay}`);
  const alpha = Number(match[4]);
  const base = rgb(background);
  const parts = [1, 2, 3].map((index, channelIndex) =>
    Math.round(alpha * Number(match[index]) + (1 - alpha) * base[channelIndex]),
  );
  return `#${parts.map((part) => part.toString(16).padStart(2, "0")).join("")}`;
}

const css = readFileSync(resolve(__dirname, "tokens.css"), "utf8");
const themes = {
  dark: parseTheme(css, "dark"),
  light: parseTheme(css, "light"),
} as const;

function pair(theme: Theme, foreground: string, background: string): number {
  const vars = themes[theme];
  return contrast(color(vars, foreground), color(vars, background));
}

function expectAA(theme: Theme, foreground: string, background: string) {
  const ratio = pair(theme, foreground, background);
  expect(ratio, `${theme} ${foreground} on ${background} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA);
}

describe("phase 1 token contrast", () => {
  const surfaces = [
    "--color-background",
    "--color-surface-1",
    "--color-surface-2",
    "--color-surface-raised",
  ] as const;
  const text = ["--color-text-primary", "--color-text-secondary", "--color-text-muted"] as const;

  it("keeps text tokens at WCAG AA on every surface", () => {
    for (const theme of ["dark", "light"] as const) {
      for (const background of surfaces) {
        for (const foreground of text) expectAA(theme, foreground, background);
      }
    }
  });

  it("keeps semantic text at WCAG AA on the app background and surface 2", () => {
    const roles = [
      "--color-accent",
      "--color-highlight",
      "--color-positive",
      "--color-negative",
      "--color-warning",
      "--color-danger",
      "--color-info",
      "--color-blocked",
      "--color-stale",
      "--color-paper",
      "--color-mode-paper",
      "--color-mode-demo",
      "--color-success",
      "--color-risk-info",
      "--color-risk-low",
      "--color-risk-medium",
      "--color-risk-high",
      "--color-risk-critical",
    ];
    for (const theme of ["dark", "light"] as const) {
      for (const role of roles) {
        expectAA(theme, role, "--color-background");
        expectAA(theme, role, "--color-surface-2");
      }
    }
  });

  it("keeps button labels at WCAG AA on their fills", () => {
    for (const theme of ["dark", "light"] as const) {
      expectAA(theme, "--color-accent-foreground", "--color-accent");
      expectAA(theme, "--color-accent-foreground", "--color-accent-hover");
      expectAA(theme, "--color-text-inverse", "--color-danger");
      expectAA(theme, "--color-text-inverse", "--color-warning");
      const solid = contrast("#ffffff", color(themes[theme], "--color-accent-solid"));
      expect(solid, `${theme} white on accent-solid is ${solid.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA);
    }
  });

  it("keeps mode badge text at WCAG AA on the tinted fill", () => {
    for (const theme of ["dark", "light"] as const) {
      const vars = themes[theme];
      const surface = color(vars, "--color-surface-1");
      for (const role of ["paper", "mode-paper", "mode-demo"] as const) {
        const fill = composite(color(vars, `--color-${role}-muted`), surface);
        const ratio = contrast(color(vars, `--color-${role}`), fill);
        expect(ratio, `${theme} ${role} badge is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA);
      }
    }
  });

  it("separates profit, interactive blue, and paper", () => {
    for (const theme of ["dark", "light"] as const) {
      const vars = themes[theme];
      const accent = color(vars, "--color-accent").toLowerCase();
      const profit = color(vars, "--color-positive").toLowerCase();
      const paper = color(vars, "--color-paper").toLowerCase();
      const demo = color(vars, "--color-mode-demo").toLowerCase();
      expect(new Set([accent, profit, paper, demo]).size).toBe(4);
      expect(color(vars, "--color-mode-paper").toLowerCase()).toBe(paper);
      expect(color(vars, "--color-highlight").toLowerCase()).not.toBe(accent);
    }
  });

  it("uses the approved phase 1 values", () => {
    const dark = themes.dark;
    const light = themes.light;
    expect(color(dark, "--color-background")).toBe("#080b14");
    expect(color(dark, "--color-accent")).toBe("#4c8dff");
    expect(color(dark, "--color-accent-hover")).toBe("#6ea2ff");
    expect(color(dark, "--color-accent-solid")).toBe("#2563eb");
    expect(color(dark, "--color-highlight")).toBe("#fb923c");
    expect(color(dark, "--color-positive")).toBe("#22c55e");
    expect(color(dark, "--color-negative")).toBe("#f87171");
    expect(color(dark, "--color-paper")).toBe("#a78bfa");
    expect(color(dark, "--color-mode-demo")).toBe("#22d3ee");
    expect(color(dark, "--color-text-muted")).toBe("#8b97ab");
    expect(color(light, "--color-accent")).toBe("#1d4ed8");
    expect(color(light, "--color-highlight")).toBe("#c2410c");
    expect(color(light, "--color-positive")).toBe("#047857");
    expect(color(light, "--color-negative")).toBe("#b91c1c");
    expect(color(light, "--color-warning")).toBe("#9a5b06");
    expect(color(light, "--color-paper")).toBe("#6d28d9");
    expect(color(light, "--color-mode-demo")).toBe("#0e7490");
    expect(color(light, "--color-text-muted")).toBe("#556378");
    expect(dark["--text-eyebrow"]).toBe("0.6875rem");
    expect(dark["--text-body"]).toBe("0.875rem");
    expect(dark["--text-display"]).toBe("2rem");
    expect(dark["--content-max"]).toBe("1200px");
    expect(dark["--elevation-1"]).toBe("none");
  });
});
