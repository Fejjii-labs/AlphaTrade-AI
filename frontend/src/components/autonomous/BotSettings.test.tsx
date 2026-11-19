import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { AutonomousTradingSettingsView, RiskPolicyView } from "@/lib/api/types";
import { autonomousBounds } from "@/test/autonomous-bounds-fixtures";
import { expectNoIdentifiers } from "@/test/no-identifiers";

import { BotSettings, botState } from "./BotSettings";
import { leverageCap } from "./DemoLeverageControl";

const auth = { user: { id: "user" }, organization: { id: "org" }, membershipRole: "owner" as string | null };
const appContext = { killSwitchStatus: null as { active: boolean } | null };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/contexts/AppContext", () => ({ useAppContext: () => appContext }));
vi.mock("@/components/autonomous/BotActivityPanel", () => ({ BotActivityPanel: () => <p>activity</p> }));

const settings = {
  organization_id: "org", user_id: "user", daily_loss_limit_percent: "3", max_trades_per_day: 5,
  risk_per_trade_percent: null, strategy_id: null, symbol: null, using_defaults: true,
  account_max_trades_per_day: 20, effective_max_trades_per_day: 20, strategy_options: [], symbol_options: [],
  max_daily_loss_percent_ceiling: "6", max_risk_per_trade_percent_ceiling: "5",
  autonomy_mode: "blofin_demo", worker_runtime_health: "RUNNING", worker_flags_stale: false,
  demo_leverage: 3, demo_margin_mode: "isolated", max_demo_leverage_ceiling: 20,
} as AutonomousTradingSettingsView;

const profile = (max: string) => ({ kind: "demo", profile: { max_leverage: max, default_leverage: "1.00000000" } }) as unknown as RiskPolicyView;

const strategy = (symbol: string, direction: string, enabled: boolean, kind = "operational_nested_continuation/v1") => ({
  strategy_id: `s-${symbol}-${direction}`, version_id: `v-${symbol}-${direction}`, version: 1, name: "x", status: "approved",
  enabled, execution_permission: "", spec: { kind, symbol, direction, trigger_timeframe: "15m", parameters: {} },
});

beforeEach(() => {
  auth.membershipRole = "owner";
  appContext.killSwitchStatus = { active: false };
  vi.spyOn(api.autonomous, "settings").mockResolvedValue(settings);
  vi.spyOn(api.autonomous, "bounds").mockResolvedValue(autonomousBounds);
  vi.spyOn(api.riskPolicy, "profile").mockResolvedValue(profile("5.00000000"));
  vi.spyOn(api.strategyBrain, "overview").mockResolvedValue({
    strategies: [
      strategy("ETHUSDT", "short", false),
      strategy("BTCUSDT", "long", true),
      strategy("BTCUSDT", "short", true, "swing_failure_pattern/v1"),
    ],
    setups: [], watched_symbols: [], limitations: [], paper_only: true,
  } as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("BotSettings", () => {
  it("shows state, venue and worker in words, the strategies it trades (long and short), and hides the caps", async () => {
    render(<BotSettings />);
    const root = await screen.findByTestId("bot-settings");
    expect(root).toHaveTextContent("BotOn");
    expect(root).toHaveTextContent("VenueBloFin demo");
    expect(root).toHaveTextContent("WorkerFresh");
    const list = await screen.findByTestId("bot-strategies");
    expect([...list.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      "Nested · BTCUSDT · Long · 15mOn",
      "SFP · BTCUSDT · Short · 15mOn",
      "Nested · ETHUSDT · Short · 15mOff",
    ]);
    for (const gone of [/Max open/, /notional/i, /Trades today/, /Grade/, /00000000/]) {
      expect(root).not.toHaveTextContent(gone);
    }
    expectNoIdentifiers();
  });

  it("binds the leverage dropdown to demo_leverage, capped by the demo risk profile max", async () => {
    vi.spyOn(api.autonomous, "updateSettings").mockResolvedValue({ ...settings, demo_leverage: 2 });
    render(<BotSettings />);
    const select = await screen.findByRole("combobox", { name: "Leverage" });
    expect(select).toHaveValue("3");
    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["1×", "2×", "3×", "4×", "5×"]);
    fireEvent.change(select, { target: { value: "2" } });
    await waitFor(() => expect(api.autonomous.updateSettings).toHaveBeenCalledWith({ demo_leverage: 2 }));
    await waitFor(() => expect(select).toHaveValue("2"));
  });

  it("asks before raising leverage", async () => {
    vi.spyOn(api.autonomous, "updateSettings").mockResolvedValue({ ...settings, demo_leverage: 5 });
    render(<BotSettings />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Leverage" }), { target: { value: "5" } });
    expect(api.autonomous.updateSettings).not.toHaveBeenCalled();
    const dialog = await screen.findByTestId("leverage-raise-confirm");
    fireEvent.click(within(dialog).getByRole("button", { name: "Raise leverage" }));
    await waitFor(() => expect(api.autonomous.updateSettings).toHaveBeenCalledWith({ demo_leverage: 5 }));
  });

  it("is read-only for non-owners", async () => {
    auth.membershipRole = "trader";
    render(<BotSettings />);
    expect(await screen.findByRole("combobox", { name: "Leverage" })).toBeDisabled();
  });

  it("says Paused while the kill switch is on", () => {
    expect(botState(settings, true).state).toBe("Paused");
    expect(botState({ ...settings, autonomy_mode: "off" }, false).state).toBe("Off");
    expect(botState({ ...settings, autonomy_mode: "" }, false).state).toBe("Not reported");
  });

  it("caps leverage at the lower of the profile max and the server ceiling", () => {
    expect(leverageCap("1.00000000", 20)).toBe(1);
    expect(leverageCap("25", 20)).toBe(20);
    expect(leverageCap(undefined, 20)).toBe(20);
    expect(leverageCap(undefined, null)).toBeNull();
  });
});
