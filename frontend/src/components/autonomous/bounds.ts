import type { AutonomousSettingsBounds, AutonomousTradingSettingsView, NumericBound } from "@/lib/api/types";

/** Short "1–1000" / "Max 5%" text for a server bound; null when the server sent none. */
export function boundHint(bound: NumericBound | undefined | null, unit = ""): string | null {
  if (!bound) return null;
  const hasMin = bound.min != null && !(bound.min_exclusive && Number(bound.min) === 0);
  if (hasMin && bound.max != null) return `${bound.min}${unit}–${bound.max}${unit}`;
  if (bound.max != null) return `Max ${bound.max}${unit}`;
  if (hasMin) return `Min ${bound.min}${unit}`;
  return null;
}

/** HTML min/max for an input. An exclusive minimum is left to the server. */
export function inputLimits(bound: NumericBound | undefined | null): { min?: string; max?: string } {
  if (!bound) return {};
  return {
    ...(bound.min != null && !bound.min_exclusive ? { min: bound.min } : {}),
    ...(bound.max != null ? { max: bound.max } : {}),
  };
}

/**
 * GET /autonomous/settings carries BloFin demo leverage. Without a numeric
 * `demo_leverage` (for example when the settings failed to load) the page shows
 * leverage read-only and says the settings are unavailable.
 */
export function hasDemoLeverageConfig(view: AutonomousTradingSettingsView | null | undefined): boolean {
  return view != null && "demo_leverage" in view && typeof (view as { demo_leverage?: unknown }).demo_leverage === "number";
}

export type Bounds = AutonomousSettingsBounds | null;
