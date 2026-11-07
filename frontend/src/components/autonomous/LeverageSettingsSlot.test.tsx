import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AutonomousTradingSettingsView } from "@/lib/api/types";

import { LeverageSettingsSlot, riskierLeverageChanges } from "./LeverageSettingsSlot";

const settings = {
  organization_id: "org",
  user_id: "user",
  daily_loss_limit_percent: "3",
  max_trades_per_day: 5,
  risk_per_trade_percent: null,
  strategy_id: "strategy-1",
  symbol: "BTCUSDT",
  using_defaults: true,
  account_max_trades_per_day: 20,
  effective_max_trades_per_day: 20,
  strategy_options: [{ strategy_id: "strategy-1", name: "Nested BTC" }],
  symbol_options: ["BTCUSDT", "ETHUSDT"],
  max_daily_loss_percent_ceiling: "6",
  max_risk_per_trade_percent_ceiling: "5",
  demo_leverage: 10,
  demo_margin_mode: "isolated",
  demo_leverage_overrides: {},
  demo_leverage_using_default: true,
  max_demo_leverage_ceiling: 20,
} as unknown as AutonomousTradingSettingsView;

const auth = { membershipRole: "owner" as string | null };

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    api: { autonomous: { settings: vi.fn(), updateSettings: vi.fn() } },
  };
});

beforeEach(async () => {
  auth.membershipRole = "owner";
  const { api } = await import("@/lib/api");
  vi.mocked(api.autonomous.settings).mockReset().mockResolvedValue(settings);
  vi.mocked(api.autonomous.updateSettings).mockReset();
});

afterEach(() => cleanup());

describe("LeverageSettingsSlot demo leverage editor", () => {
  it("shows the demo leverage defaults and saves leverage, margin mode and a per-coin override", async () => {
    const { api } = await import("@/lib/api");
    vi.mocked(api.autonomous.updateSettings).mockResolvedValue({
      ...settings,
      demo_leverage: 5,
      demo_margin_mode: "cross",
      demo_leverage_overrides: { ETHUSDT: { leverage: 3 } },
      demo_leverage_using_default: false,
    });
    render(<LeverageSettingsSlot profiles={[]} />);
    expect(await screen.findByTestId("demo-leverage")).toHaveValue("10");
    expect(screen.getByTestId("demo-margin-mode")).toHaveValue("isolated");
    expect(screen.getByTestId("demo-leverage-settings")).toHaveTextContent("1–20x");
    fireEvent.change(screen.getByTestId("demo-leverage"), { target: { value: "5" } });
    fireEvent.change(screen.getByTestId("demo-margin-mode"), { target: { value: "cross" } });
    fireEvent.click(screen.getByTestId("demo-leverage-add-override"));
    fireEvent.change(screen.getByLabelText("Override coin"), { target: { value: "ETHUSDT" } });
    fireEvent.change(screen.getByLabelText("Override leverage"), { target: { value: "3" } });
    fireEvent.click(screen.getByTestId("demo-leverage-save"));
    // Switching to cross margin raises liquidation risk, so it asks first.
    expect(screen.getByTestId("demo-leverage-confirm-changes")).toHaveTextContent("isolated → cross");
    fireEvent.click(screen.getByTestId("demo-leverage-confirm-confirm"));
    await waitFor(() => {
      expect(api.autonomous.updateSettings).toHaveBeenCalledWith({
        demo_leverage: 5,
        demo_margin_mode: "cross",
        demo_leverage_overrides: { ETHUSDT: { leverage: 3 } },
      });
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Leverage saved.");
    expect(screen.getByTestId("demo-leverage")).toHaveValue("5");
  });

  it("explains that higher leverage does not increase trade size at the exposure limit", async () => {
    render(<LeverageSettingsSlot profiles={[]} />);
    const hint = await screen.findByTestId("demo-leverage-size-hint");
    expect(hint).toHaveTextContent("higher leverage only lowers the margin used and moves liquidation closer");
    expect(hint).toHaveTextContent("It does not increase trade size.");
  });

  it("clears overrides when none are set and sends only leverage fields", async () => {
    const { api } = await import("@/lib/api");
    vi.mocked(api.autonomous.updateSettings).mockResolvedValue(settings);
    render(<LeverageSettingsSlot profiles={[]} />);
    fireEvent.click(await screen.findByTestId("demo-leverage-save"));
    await waitFor(() => {
      expect(api.autonomous.updateSettings).toHaveBeenCalledWith({
        demo_leverage: 10,
        demo_margin_mode: "isolated",
        clear_demo_leverage_overrides: true,
      });
    });
  });

  it("refuses leverage above the 20x ceiling before calling the API", async () => {
    const { api } = await import("@/lib/api");
    render(<LeverageSettingsSlot profiles={[]} />);
    fireEvent.change(await screen.findByTestId("demo-leverage"), { target: { value: "25" } });
    fireEvent.click(screen.getByTestId("demo-leverage-save"));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Leverage cannot exceed 20x.");
    });
    expect(api.autonomous.updateSettings).not.toHaveBeenCalled();
  });

  it("explains a server leverage ceiling rejection", async () => {
    const { api, ApiError } = await import("@/lib/api");
    vi.mocked(api.autonomous.updateSettings).mockRejectedValue(
      new ApiError("demo_leverage_exceeds_ceiling", 422, null),
    );
    render(<LeverageSettingsSlot profiles={[]} />);
    fireEvent.click(await screen.findByTestId("demo-leverage-save"));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Leverage cannot exceed 20x (code ceiling).");
    });
  });

  it("disables the editor for non-owners", async () => {
    auth.membershipRole = "trader";
    render(<LeverageSettingsSlot profiles={[]} />);
    expect(await screen.findByTestId("demo-leverage")).toBeDisabled();
    expect(screen.getByTestId("demo-leverage-owner-reason")).toHaveTextContent("Owner only.");
  });

  it("uses the page's settings and the bounds ceiling without fetching again", async () => {
    const { api } = await import("@/lib/api");
    const bounds = { leverage: { demo_effective_max: "15", demo_hard_cap: "20", profile_max_leverage_ceiling: "20" } } as never;
    render(<LeverageSettingsSlot profiles={[]} settings={settings} bounds={bounds} />);
    expect(await screen.findByTestId("demo-leverage-settings")).toHaveTextContent("1–15x");
    expect(api.autonomous.settings).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("demo-leverage"), { target: { value: "16" } });
    fireEvent.click(screen.getByTestId("demo-leverage-save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Leverage cannot exceed 15x.");
    expect(api.autonomous.updateSettings).not.toHaveBeenCalled();
  });

  it("never invents a 20x ceiling when neither bounds nor settings report one", async () => {
    const { api, ApiError } = await import("@/lib/api");
    const noCeiling: Record<string, unknown> = { ...settings };
    delete noCeiling.max_demo_leverage_ceiling;
    vi.mocked(api.autonomous.settings).mockResolvedValue(noCeiling as unknown as AutonomousTradingSettingsView);
    vi.mocked(api.autonomous.updateSettings).mockRejectedValue(new ApiError("demo_leverage_exceeds_ceiling", 422, null));
    render(<LeverageSettingsSlot profiles={[]} />);
    expect(await screen.findByTestId("demo-leverage-settings")).toHaveTextContent("the server enforces the ceiling");
    expect(screen.getByTestId("demo-leverage-settings")).not.toHaveTextContent("20x");
    fireEvent.click(screen.getByTestId("demo-leverage-save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Leverage exceeds the server ceiling.");
  });

  it("asks before raising leverage and sends nothing on cancel", async () => {
    const { api } = await import("@/lib/api");
    vi.mocked(api.autonomous.updateSettings).mockResolvedValue({ ...settings, demo_leverage: 20 });
    render(<LeverageSettingsSlot profiles={[]} />);
    fireEvent.change(await screen.findByTestId("demo-leverage"), { target: { value: "20" } });
    fireEvent.click(screen.getByTestId("demo-leverage-save"));
    expect(screen.getByTestId("demo-leverage-confirm-changes")).toHaveTextContent("Default leverage 10× → 20×");
    expect(api.autonomous.updateSettings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("demo-leverage-confirm-cancel"));
    expect(api.autonomous.updateSettings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("demo-leverage-save"));
    fireEvent.click(screen.getByTestId("demo-leverage-confirm-confirm"));
    await waitFor(() =>
      expect(api.autonomous.updateSettings).toHaveBeenCalledWith(expect.objectContaining({ demo_leverage: 20 })),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Leverage saved.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("flags only changes that raise liquidation risk", () => {
    const base = { demo_overrides: [], demo_margin_mode: "isolated" as const };
    expect(riskierLeverageChanges(settings, { ...base, demo_leverage: "5" })).toEqual([]);
    expect(riskierLeverageChanges(settings, { ...base, demo_leverage: "10", demo_margin_mode: "cross" })).toEqual([
      "Default margin mode isolated → cross",
    ]);
    expect(
      riskierLeverageChanges(settings, {
        ...base,
        demo_leverage: "10",
        demo_overrides: [{ symbol: "ETHUSDT", leverage: "12", margin_mode: "" }, { symbol: "BTCUSDT", leverage: "3", margin_mode: "" }],
      }),
    ).toEqual(["ETHUSDT leverage 10× → 12×"]);
  });
});
