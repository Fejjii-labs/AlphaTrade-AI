import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AutonomousTradingSettingsView, UserRiskSettings } from "@/lib/api/types";
import { autonomousBounds } from "@/test/autonomous-bounds-fixtures";

import { TradingLimitsCard } from "./TradingLimitsCard";

const bot: AutonomousTradingSettingsView = {
  organization_id: "org",
  user_id: "user",
  daily_loss_limit_percent: "3",
  max_trades_per_day: 5,
  risk_per_trade_percent: null,
  strategy_id: "strategy-1",
  symbol: "BTCUSDT",
  using_defaults: false,
  account_max_trades_per_day: 3,
  effective_max_trades_per_day: 3,
  strategy_options: [
    { strategy_id: "strategy-1", name: "Nested BTC" },
    { strategy_id: "strategy-2", name: "SFP ETH" },
  ],
  symbol_options: ["BTCUSDT", "ETHUSDT"],
  max_daily_loss_percent_ceiling: "6",
  max_risk_per_trade_percent_ceiling: "5",
};

const account: UserRiskSettings = {
  organization_id: "org",
  user_id: "user",
  daily_loss_limit: "50",
  daily_target: null,
  max_trades_per_day: 3,
  max_risk_per_trade_percent: "1",
  default_account_balance: "10000",
  timezone: "UTC",
  green_day_protection_enabled: true,
  one_loss_stop_enabled: false,
  overtrading_guard_enabled: true,
  notes: null,
  using_defaults: false,
  timezone_fallback: false,
};

vi.mock("@/lib/api", () => ({
  api: {
    autonomous: { updateSettings: vi.fn() },
    risk: { updateSettings: vi.fn(), resetSettings: vi.fn() },
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

const onBotSaved = vi.fn();
const onAccountSaved = vi.fn();

function renderCard(owner = true) {
  render(
    <TradingLimitsCard
      bot={bot}
      account={account}
      owner={owner}
      ownerReason={owner ? null : "Owner only (your role: Trader)"}
      onBotSaved={onBotSaved}
      onAccountSaved={onAccountSaved}
    />,
  );
}

const save = () => fireEvent.click(screen.getByTestId("limits-save"));
const change = (testId: string, value: string) =>
  fireEvent.change(screen.getByTestId(testId), { target: { value } });

describe("TradingLimitsCard", () => {
  beforeEach(async () => {
    const { api } = await import("@/lib/api");
    vi.mocked(api.autonomous.updateSettings).mockReset().mockImplementation(async (body) => ({ ...bot, ...body }) as AutonomousTradingSettingsView);
    vi.mocked(api.risk.updateSettings).mockReset().mockImplementation(async (body) => ({ ...account, ...body }) as UserRiskSettings);
    vi.mocked(api.risk.resetSettings).mockReset().mockResolvedValue({ ...account, using_defaults: true });
    onBotSaved.mockReset();
    onAccountSaved.mockReset();
  });
  afterEach(cleanup);

  it("holds every agreed parameter in one form", () => {
    renderCard();
    for (const testId of [
      "risk-max-trades",
      "autonomous-max-trades",
      "risk-daily-loss-limit",
      "autonomous-daily-loss-percent",
      "risk-max-risk-percent",
      "autonomous-risk-per-trade",
      "risk-default-balance",
      "autonomous-strategy",
      "autonomous-symbol",
    ]) {
      expect(screen.getByTestId(testId)).toBeInTheDocument();
    }
    expect(screen.getByTestId("autonomous-effective-max-trades")).toHaveTextContent(
      "Effective 3 · capped by account limit of 3",
    );
    expect(screen.getByTestId("limits-save")).toBeDisabled();
  });

  it("saves only bot settings when only bot fields change", async () => {
    const { api } = await import("@/lib/api");
    renderCard();
    change("autonomous-strategy", "strategy-2");
    change("autonomous-symbol", "ETHUSDT");
    change("autonomous-risk-per-trade", "0.5");
    save();
    await waitFor(() => expect(screen.getByTestId("limits-saved")).toHaveTextContent("Bot limits saved."));
    expect(api.risk.updateSettings).not.toHaveBeenCalled();
    expect(api.autonomous.updateSettings).toHaveBeenCalledExactlyOnceWith({
      daily_loss_limit_percent: "3",
      max_trades_per_day: 5,
      strategy_id: "strategy-2",
      symbol: "ETHUSDT",
      risk_per_trade_percent: "0.5",
    });
    expect(onBotSaved).toHaveBeenCalledOnce();
  });

  it("clears bot risk per trade back to grade risk when emptied", async () => {
    const { api } = await import("@/lib/api");
    renderCard();
    change("autonomous-daily-loss-percent", "2");
    save();
    await waitFor(() => expect(api.autonomous.updateSettings).toHaveBeenCalledOnce());
    expect(vi.mocked(api.autonomous.updateSettings).mock.calls[0][0]).toMatchObject({
      daily_loss_limit_percent: "2",
      clear_risk_per_trade_percent: true,
    });
  });

  it("saves only account limits when only account fields change", async () => {
    const { api } = await import("@/lib/api");
    renderCard();
    change("risk-default-balance", "25000");
    save();
    await waitFor(() => expect(screen.getByTestId("limits-saved")).toHaveTextContent("Account limits saved."));
    expect(api.autonomous.updateSettings).not.toHaveBeenCalled();
    expect(api.risk.updateSettings).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ default_account_balance: "25000", max_trades_per_day: 3, daily_loss_limit: "50" }),
    );
    expect(onAccountSaved).toHaveBeenCalledOnce();
  });

  it("saves the account cap before the bot cap so a raised account cap is in place", async () => {
    const { api } = await import("@/lib/api");
    const order: string[] = [];
    vi.mocked(api.risk.updateSettings).mockImplementation(async (body) => {
      order.push("account");
      return { ...account, ...body } as UserRiskSettings;
    });
    vi.mocked(api.autonomous.updateSettings).mockImplementation(async (body) => {
      order.push("bot");
      return { ...bot, ...body } as AutonomousTradingSettingsView;
    });
    renderCard();
    change("risk-max-trades", "10");
    change("autonomous-max-trades", "8");
    save();
    await waitFor(() =>
      expect(screen.getByTestId("limits-saved")).toHaveTextContent("Account limits saved. Bot limits saved."),
    );
    expect(order).toEqual(["account", "bot"]);
  });

  it("does not save bot limits when the account save fails", async () => {
    const { api } = await import("@/lib/api");
    vi.mocked(api.risk.updateSettings).mockRejectedValue(new Error("max_trades_per_day invalid"));
    renderCard();
    change("risk-max-trades", "10");
    change("autonomous-max-trades", "8");
    save();
    await waitFor(() =>
      expect(screen.getAllByTestId("limits-error").map((el) => el.textContent)).toEqual([
        "Account limits not saved: max_trades_per_day invalid.",
        "Bot limits not saved: skipped because the account limits failed.",
      ]),
    );
    expect(api.autonomous.updateSettings).not.toHaveBeenCalled();
  });

  it("says the account half saved when only the bot half fails", async () => {
    const { api, ApiError } = await import("@/lib/api");
    vi.mocked(api.autonomous.updateSettings).mockRejectedValue(new ApiError("max_trades_per_day_exceeds_account", 422, null));
    renderCard();
    change("risk-max-trades", "4");
    change("autonomous-max-trades", "25");
    save();
    await waitFor(() =>
      expect(screen.getByTestId("limits-error")).toHaveTextContent(
        "Bot limits not saved (account limits were saved): Bot max trades per day cannot exceed",
      ),
    );
    expect(screen.getByTestId("limits-saved")).toHaveTextContent("Account limits saved.");
  });

  it("ignores a second submit while a save is in flight", async () => {
    const { api } = await import("@/lib/api");
    let release: () => void = () => {};
    vi.mocked(api.risk.updateSettings).mockImplementation(
      (body) => new Promise((resolve) => { release = () => resolve({ ...account, ...body } as UserRiskSettings); }),
    );
    renderCard();
    change("risk-default-balance", "25000");
    const form = screen.getByTestId("limits-save").closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    release();
    await waitFor(() => expect(screen.getByTestId("limits-saved")).toHaveTextContent("Account limits saved."));
    expect(api.risk.updateSettings).toHaveBeenCalledOnce();
  });

  it("explains a bot cap above the account cap", async () => {
    const { api, ApiError } = await import("@/lib/api");
    vi.mocked(api.autonomous.updateSettings).mockRejectedValue(new ApiError("max_trades_per_day_exceeds_account", 422, null));
    renderCard();
    change("autonomous-max-trades", "25");
    save();
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Bot max trades per day cannot exceed the account limit of 3."),
    );
  });

  it("never sends bot settings for a non-owner and keeps account limits savable", async () => {
    const { api } = await import("@/lib/api");
    renderCard(false);
    expect(screen.getByTestId("autonomous-max-trades")).toBeDisabled();
    change("risk-daily-loss-limit", "75");
    save();
    await waitFor(() => expect(api.risk.updateSettings).toHaveBeenCalledOnce());
    expect(api.autonomous.updateSettings).not.toHaveBeenCalled();
  });

  it("reads every limit hint from the server bounds, not the client", () => {
    render(
      <TradingLimitsCard
        bot={bot}
        account={account}
        owner
        ownerReason={null}
        onBotSaved={onBotSaved}
        onAccountSaved={onAccountSaved}
        bounds={{
          ...autonomousBounds,
          bot_daily_loss_limit_percent: { ...autonomousBounds.bot_daily_loss_limit_percent, max: "4" },
          bot_risk_per_trade_percent: { ...autonomousBounds.bot_risk_per_trade_percent, max: "3" },
          account_max_trades_per_day: { ...autonomousBounds.account_max_trades_per_day, max: "50" },
        }}
      />,
    );
    const card = screen.getByTestId("trading-limits-card");
    expect(card).toHaveTextContent("Max 4% of equity");
    expect(card).toHaveTextContent("Max 3% of portfolio");
    expect(card).toHaveTextContent("Allowed Max 10%");
    expect(screen.getByTestId("autonomous-effective-max-trades")).toHaveTextContent("allowed 1–1000");
    expect(screen.getByTestId("risk-max-trades")).toHaveAttribute("min", "1");
    expect(screen.getByTestId("risk-max-trades")).toHaveAttribute("max", "50");
  });

  it("falls back to the ceilings on the bot view without bounds and links the watchlist", () => {
    renderCard();
    const card = screen.getByTestId("trading-limits-card");
    expect(card).toHaveTextContent("Max 6% of equity");
    expect(card).toHaveTextContent("Max 5% of portfolio");
    expect(screen.getByRole("link", { name: "Edit watchlist" })).toHaveAttribute("href", "/settings/watcher");
  });

  it("resets account limits to defaults", async () => {
    const { api } = await import("@/lib/api");
    renderCard();
    fireEvent.click(screen.getByTestId("risk-settings-reset"));
    expect(screen.getByRole("dialog", { name: "Reset account limits to defaults?" })).toBeInTheDocument();
    expect(api.risk.resetSettings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("risk-reset-confirm-cancel"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.risk.resetSettings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("risk-settings-reset"));
    fireEvent.click(screen.getByTestId("risk-reset-confirm-confirm"));
    await waitFor(() => expect(screen.getByTestId("limits-saved")).toHaveTextContent("Account limits reset to defaults."));
    expect(api.risk.resetSettings).toHaveBeenCalledOnce();
    expect(onAccountSaved).toHaveBeenCalledWith(expect.objectContaining({ using_defaults: true }));
  });
});
