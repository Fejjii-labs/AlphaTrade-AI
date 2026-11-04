import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RiskProfileSections } from "./RiskProfileSections";

const profile = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    riskPolicy: { profile: (kind: string) => profile(kind) },
    autonomous: { settings: () => Promise.reject(new Error("403")) },
  },
  ApiError: class ApiError extends Error {},
}));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ membershipRole: "trader" }) }));

const view = (kind: string, a: string, maxLeverage: string) => ({
  kind,
  source: "version",
  version_number: 2,
  effective_at: "2026-10-01T00:00:00Z",
  profile: {
    risk_percent_a_plus: "1",
    risk_percent_a: a,
    risk_percent_b_plus: "0.5",
    risk_percent_b: "0.25",
    risk_percent_c: "0",
    max_leverage: maxLeverage,
    default_leverage: "2",
    max_notional_per_trade_percent: "50",
    max_aggregate_open_risk_percent: "3",
    max_open_positions: "3",
  },
  scheduled_version_number: null,
  scheduled_effective_at: null,
  applies_to_new_trades_only: true,
  real_trading_enabled: false,
});

afterEach(() => {
  cleanup();
  profile.mockReset();
});

describe("RiskProfileSections", () => {
  it("shows grade risk per profile and the profile leverage in the leverage slot", async () => {
    profile.mockImplementation(async (kind: string) => view(kind, kind === "demo" ? "0.5" : "0.75", kind === "demo" ? "5" : "3"));
    render(<RiskProfileSections />);
    const table = await screen.findByTestId("grade-risk-table");
    expect(profile.mock.calls.map(([kind]) => kind)).toEqual(["internal_paper", "demo"]);
    expect(table).toHaveTextContent("Internal paper");
    expect(table).toHaveTextContent("BloFin demo");
    expect(table).toHaveTextContent("Grade A0.75%0.5%");
    expect(table).toHaveTextContent("Max open positions33");
    const slot = screen.getByTestId("leverage-settings-slot");
    expect(slot).toHaveAttribute("id", "leverage");
    expect(slot).toHaveTextContent("Default 2× · max 3×");
    expect(slot).toHaveTextContent("Default 2× · max 5×");
    await waitFor(() => expect(slot).toHaveTextContent("Demo leverage settings unavailable."));
  });

  it("keeps the leverage slot when profiles cannot load", async () => {
    profile.mockRejectedValue(new Error("403"));
    render(<RiskProfileSections />);
    await waitFor(() => expect(screen.getByTestId("leverage-settings-slot")).toHaveTextContent("Leverage profile unavailable."));
    expect(screen.getByTestId("grade-risk-card")).toHaveTextContent("Risk profile unavailable.");
  });
});
