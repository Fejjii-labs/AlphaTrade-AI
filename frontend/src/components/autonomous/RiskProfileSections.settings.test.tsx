// Separate from RiskProfileSections.test.tsx. Renders the real demo leverage
// editor (LeverageSettingsSlot), unmocked, to check the settings page hands over to it.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AutonomousTradingSettingsView } from "@/lib/api/types";

import { RiskProfileSections } from "./RiskProfileSections";

const settings = {
  organization_id: "org",
  user_id: "user",
  daily_loss_limit_percent: "3",
  max_trades_per_day: 5,
  account_max_trades_per_day: 20,
  effective_max_trades_per_day: 5,
  strategy_options: [],
  symbol_options: ["BTCUSDT"],
  demo_leverage: 7,
  demo_margin_mode: "isolated",
  demo_leverage_overrides: {},
  demo_leverage_using_default: false,
  max_demo_leverage_ceiling: 20,
} as unknown as AutonomousTradingSettingsView;

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ membershipRole: "owner" }) }));
vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  api: {
    riskPolicy: { profile: () => Promise.reject(new Error("403")) },
    autonomous: { settings: vi.fn(), updateSettings: vi.fn() },
  },
}));

beforeEach(async () => {
  const { api } = await import("@/lib/api");
  vi.mocked(api.autonomous.settings).mockReset().mockResolvedValue(settings);
});

afterEach(cleanup);

describe("RiskProfileSections with the page's settings", () => {
  it("renders the demo leverage editor when the settings carry demo_leverage", async () => {
    render(<RiskProfileSections settings={settings} />);
    expect(await screen.findByTestId("demo-leverage-settings")).toBeInTheDocument();
    expect(await screen.findByTestId("demo-leverage")).toHaveValue("7");
    expect(screen.getByTestId("demo-leverage-save")).toBeInTheDocument();
    expect(screen.queryByTestId("leverage-unavailable")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("#430");
  });

  it("shows read-only leverage with an unavailable notice when the settings have no demo leverage", async () => {
    render(<RiskProfileSections settings={{} as AutonomousTradingSettingsView} />);
    expect(await screen.findByTestId("leverage-unavailable")).toHaveTextContent("Leverage settings unavailable.");
    expect(screen.queryByTestId("demo-leverage-settings")).not.toBeInTheDocument();
  });

  it("renders the demo leverage editor as before when no settings are passed", async () => {
    render(<RiskProfileSections />);
    expect(await screen.findByTestId("demo-leverage-settings")).toBeInTheDocument();
  });
});
