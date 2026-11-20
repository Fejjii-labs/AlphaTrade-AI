import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { AutonomousTradingSettingsView } from "@/lib/api/types";
import { expectNoIdentifiers } from "@/test/no-identifiers";

import { BotSettings, botState } from "./BotSettings";

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
  risk_model_v2_enabled: false, demo_leverage: 3, demo_margin_mode: "isolated", max_demo_leverage_ceiling: 10,
} as AutonomousTradingSettingsView;

const strategy = (symbol: string, direction: string, enabled: boolean, kind = "operational_nested_continuation/v1") => ({
  strategy_id: `s-${symbol}-${direction}`, version_id: `v-${symbol}-${direction}`, version: 1, name: "x", status: "approved",
  enabled, execution_permission: "", spec: { kind, symbol, direction, trigger_timeframe: "15m", parameters: {} },
});

beforeEach(() => {
  auth.membershipRole = "owner";
  appContext.killSwitchStatus = { active: false };
  vi.spyOn(api.autonomous, "settings").mockResolvedValue(settings);
  vi.spyOn(api.autonomous, "bounds");
  vi.spyOn(api.riskPolicy, "profile");
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

  it("binds leverage to demo_leverage, lists 1x through 10x and saves on change with a Saved mark", async () => {
    vi.spyOn(api.autonomous, "updateSettings").mockResolvedValue({ ...settings, demo_leverage: 10 });
    render(<BotSettings />);
    const select = await screen.findByRole("combobox", { name: "Leverage" });
    expect(select).toHaveValue("3");
    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual(Array.from({ length: 10 }, (_, i) => `${i + 1}×`));
    expect(api.riskPolicy.profile).not.toHaveBeenCalled();
    expect(api.autonomous.bounds).not.toHaveBeenCalled();
    fireEvent.change(select, { target: { value: "10" } });
    // No confirmation step: the pick is saved straight away.
    await waitFor(() => expect(api.autonomous.updateSettings).toHaveBeenCalledWith({ demo_leverage: 10 }));
    await waitFor(() => expect(select).toHaveValue("10"));
    expect(await screen.findByTestId("leverage-status")).toHaveTextContent("Saved");
    expect(screen.queryByTestId("leverage-raise-confirm")).not.toBeInTheDocument();
  });

  it("shows v2 leverage and preserves the latest full payload on save", async () => {
    vi.mocked(api.autonomous.settings).mockResolvedValue({ ...settings, risk_model_v2_enabled: true, demo_risk_v2_leverage: 6 });
    const latest = {
      leverage: 7, margin_mode: "isolated" as const, daily_stop_pct: "3.00",
      risk_per_trade_pct: "0.75", grade_risk_overrides: { A: "1.25" }, grades_gate_entries: true,
    };
    const read = vi.spyOn(api.riskPolicy, "demoBotSettings").mockResolvedValue(latest);
    const write = vi.spyOn(api.riskPolicy, "updateDemoBotSettings").mockResolvedValue({ ...latest, leverage: 9 });
    const legacy = vi.spyOn(api.autonomous, "updateSettings");
    render(<BotSettings />);
    const select = await screen.findByRole("combobox", { name: "Leverage" });
    await waitFor(() => expect(select).toHaveValue("6"));
    fireEvent.change(select, { target: { value: "9" } });
    await waitFor(() => expect(select).toHaveValue("9"));
    expect(read).toHaveBeenCalledTimes(1);
    expect(write).toHaveBeenCalledWith({ ...latest, leverage: 9 });
    expect(legacy).not.toHaveBeenCalled();
  });

  it("shows a short error and keeps the saved value when saving fails", async () => {
    vi.spyOn(api.autonomous, "updateSettings").mockRejectedValue(new TypeError("Failed to fetch"));
    render(<BotSettings />);
    const select = await screen.findByRole("combobox", { name: "Leverage" });
    fireEvent.change(select, { target: { value: "5" } });
    expect(await screen.findByTestId("leverage-status")).toHaveTextContent("Not saved. Try again.");
    expect(screen.getByTestId("leverage-status")).not.toHaveTextContent("Failed to fetch");
    expect(select).toHaveValue("3");
  });

  it("keeps the v2 selection when the v2 PUT fails", async () => {
    vi.mocked(api.autonomous.settings).mockResolvedValue({
      ...settings, risk_model_v2_enabled: true, demo_risk_v2_leverage: 6,
    });
    vi.spyOn(api.riskPolicy, "demoBotSettings").mockResolvedValue({
      leverage: 6, margin_mode: "isolated", daily_stop_pct: "3.00",
      risk_per_trade_pct: "0.75", grade_risk_overrides: {}, grades_gate_entries: false,
    });
    vi.spyOn(api.riskPolicy, "updateDemoBotSettings").mockRejectedValue(new Error("Write failed"));
    const legacy = vi.spyOn(api.autonomous, "updateSettings");
    render(<BotSettings />);
    const select = await screen.findByRole("combobox", { name: "Leverage" });
    fireEvent.change(select, { target: { value: "9" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved. Try again.");
    expect(select).toHaveValue("6");
    expect(legacy).not.toHaveBeenCalled();
  });

  it("disables saving while the configured worker has not reported the risk model", async () => {
    vi.mocked(api.autonomous.settings).mockResolvedValue({ ...settings, risk_model_v2_enabled: null });
    render(<BotSettings />);
    const select = await screen.findByRole("combobox", { name: "Leverage" });
    expect(select).toHaveValue("");
    expect(select).toBeDisabled();
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
});
