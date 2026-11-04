import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AutonomousTradingSettingsView, RiskPolicyView } from "@/lib/api/types";
import { autonomousBounds } from "@/test/autonomous-bounds-fixtures";

import { hasDemoLeverageConfig } from "./bounds";
import { LeverageSection } from "./LeverageSection";

vi.mock("@/components/autonomous/LeverageSettingsSlot", () => ({
  LeverageSettingsSlot: () => <section data-testid="demo-leverage-editor-mock" />,
}));

const settings = {
  organization_id: "org",
  user_id: "user",
  daily_loss_limit_percent: "3",
  max_trades_per_day: 5,
  account_max_trades_per_day: 20,
  effective_max_trades_per_day: 5,
  strategy_options: [],
  symbol_options: [],
} as unknown as AutonomousTradingSettingsView;

const profile = (kind: string, max: string) =>
  ({ kind, profile: { default_leverage: "2", max_leverage: max } }) as unknown as RiskPolicyView;

afterEach(cleanup);

describe("LeverageSection", () => {
  it("is read-only and says leverage settings are unavailable when the settings have no demo leverage", () => {
    render(<LeverageSection profiles={[profile("internal_paper", "3"), profile("demo", "5")]} settings={settings} bounds={autonomousBounds} />);
    const card = screen.getByTestId("leverage-settings-slot");
    expect(card).toHaveAttribute("id", "leverage");
    expect(card).toHaveTextContent("Internal paperDefault 2× · max 3×");
    expect(card).toHaveTextContent("BloFin demoDefault 2× · max 5×");
    expect(card).toHaveTextContent("BloFin demo ceiling20×");
    expect(screen.getByTestId("leverage-unavailable")).toHaveTextContent("Read-only. Leverage settings unavailable.");
    expect(card).not.toHaveTextContent("#430");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByTestId("demo-leverage-editor-mock")).not.toBeInTheDocument();
  });

  it("hands over to the demo leverage editor once the settings carry demo_leverage", () => {
    const withLeverage = { ...settings, demo_leverage: 10, max_demo_leverage_ceiling: 20 } as AutonomousTradingSettingsView;
    render(<LeverageSection profiles={[]} settings={withLeverage} bounds={null} />);
    expect(screen.getByTestId("demo-leverage-editor-mock")).toBeInTheDocument();
    expect(screen.queryByTestId("leverage-unavailable")).not.toBeInTheDocument();
  });

  it("says leverage settings are unavailable when settings could not load", () => {
    render(<LeverageSection profiles={[]} settings={null} bounds={null} />);
    expect(screen.getByTestId("leverage-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("leverage-settings-slot")).toHaveTextContent("Leverage profile unavailable.");
  });

  it("feature-detects only a numeric demo_leverage", () => {
    expect(hasDemoLeverageConfig(settings)).toBe(false);
    expect(hasDemoLeverageConfig({ ...settings, demo_leverage: null } as unknown as AutonomousTradingSettingsView)).toBe(false);
    expect(hasDemoLeverageConfig({ ...settings, demo_leverage: 5 } as unknown as AutonomousTradingSettingsView)).toBe(true);
    expect(hasDemoLeverageConfig(null)).toBe(false);
  });
});
