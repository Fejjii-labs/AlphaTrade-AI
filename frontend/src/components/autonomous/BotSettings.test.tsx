import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { autonomousBounds } from "@/test/autonomous-bounds-fixtures";

import { BotSettings } from "./BotSettings";

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
};

const account = {
  organization_id: "org",
  user_id: "user",
  daily_loss_limit: "50",
  daily_target: null,
  max_trades_per_day: 20,
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

const auth = {
  user: { id: "user", role: "trader" },
  organization: { id: "org" },
  membershipRole: "trader" as string | null,
};

const appContext = {
  killSwitchStatus: null as { execution_blocked: boolean; reason: string | null } | null,
  killSwitchError: null as string | null,
  loading: false,
};

const safetyPosture = {
  executionMode: "paper" as string | null,
  realTradingEnabled: false as boolean | null,
  providerMode: "fallback",
  postureKnown: true,
};

const useAsyncDataMock = vi.fn();

const riskProfileProps = vi.fn();
// Grade risk and leverage have their own load and tests (RiskProfileSections / LeverageSection).
vi.mock("@/components/autonomous/RiskProfileSections", () => ({
  RiskProfileSections: (props: Record<string, unknown>) => {
    riskProfileProps(props);
    return <section aria-label="Grade risk and leverage" />;
  },
}));

// The activity panel has its own load and tests (BotActivityPanel.test.tsx).
vi.mock("@/components/autonomous/BotActivityPanel", () => ({
  BotActivityPanel: () => <section aria-label="What the bot is doing" />,
}));

vi.mock("@/contexts/AppContext", () => ({
  useAppContext: () => appContext,
  useSafetyPosture: () => safetyPosture,
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => auth,
}));

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: (...args: unknown[]) => useAsyncDataMock(...args),
}));

vi.mock("@/lib/api", () => ({
  api: {
    autonomous: {
      settings: vi.fn(),
      bounds: vi.fn(),
      updateSettings: vi.fn(),
    },
    risk: {
      settings: vi.fn(),
      updateSettings: vi.fn(),
      resetSettings: vi.fn(),
    },
  },
  ApiError: class ApiError extends Error {
    status: number;
    body: unknown;
    constructor(message: string, status: number, body: unknown = null) {
      super(message);
      this.status = status;
      this.body = body;
    }
  },
}));

describe("Autonomous trading settings form", () => {
  afterEach(() => {
    cleanup();
    useAsyncDataMock.mockClear();
  });

  beforeEach(() => {
    auth.user = { id: "user", role: "trader" };
    auth.membershipRole = "trader";
    appContext.killSwitchStatus = { execution_blocked: false, reason: null };
    useAsyncDataMock.mockReturnValue({
      data: [settings, account],
      loading: false,
      error: null,
      reload: vi.fn(),
    });
  });

  it("loads settings once and passes them and the bounds to the guardrails, grade risk and leverage", () => {
    useAsyncDataMock.mockReturnValue({ data: [settings, account, autonomousBounds], loading: false, error: null, reload: vi.fn() });
    render(<BotSettings />);
    expect(screen.getByTestId("guardrails-stop-loss-band")).toHaveTextContent("1%–5% of equity per trade");
    expect(riskProfileProps).toHaveBeenLastCalledWith({ settings, bounds: autonomousBounds });
    expect(screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"))).toEqual([
      "Grade risk and leverage",
      "What the bot is doing",
    ]);
  });

  it("still shows grade risk and leverage when the settings fail to load", () => {
    useAsyncDataMock.mockReturnValue({ data: null, loading: false, error: "403", reload: vi.fn() });
    render(<BotSettings />);
    expect(screen.getByRole("region", { name: "Grade risk and leverage" })).toBeInTheDocument();
    expect(riskProfileProps).toHaveBeenLastCalledWith({});
  });

  it("renders typed fields and selectors", () => {
    render(<BotSettings />);
    expect(screen.getByRole("region", { name: "What the bot is doing" })).toBeInTheDocument();
    expect(screen.getByTestId("autonomous-daily-loss-percent")).toHaveValue("3");
    expect(screen.getByTestId("autonomous-max-trades")).toHaveValue("5");
    expect(screen.getByTestId("autonomous-risk-per-trade")).toHaveValue("");
    expect(screen.getByTestId("autonomous-strategy")).toHaveValue("strategy-1");
    expect(screen.getByTestId("autonomous-symbol")).toHaveValue("BTCUSDT");
    expect(screen.getByTestId("autonomous-paper-switch-input")).toBeDisabled();
    expect(screen.getByTestId("autonomous-paper-switch-note")).toHaveTextContent(
      /stops new entries/i,
    );
    expect(screen.getByTestId("autonomous-paper-switch-note")).toHaveTextContent(
      /does not close open positions/i,
    );
    expect(screen.getByTestId("autonomous-paper-switch-status")).toHaveTextContent(
      "Not reported",
    );
    expect(screen.queryByText("Running")).not.toBeInTheDocument();
  });

  it("lets an owner persist the paper switch without changing limits", async () => {
    // /auth/me reports user.role as admin/trader/viewer; ownership comes from the membership.
    auth.user = { id: "user", role: "trader" };
    auth.membershipRole = "owner";
    const { api } = await import("@/lib/api");
    vi.mocked(api.autonomous.updateSettings).mockResolvedValue({
      ...settings,
      internal_paper_enabled: true,
      internal_paper_env_ceiling: true,
      internal_paper_effective: false,
      worker_runtime_health: "STALE",
      worker_flags_stale: true,
      autonomy_mode: "off",
    });
    render(<BotSettings />);
    const toggle = screen.getByTestId("autonomous-paper-switch-input");
    expect(toggle).toBeEnabled();
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(api.autonomous.updateSettings).toHaveBeenCalledExactlyOnceWith({
        internal_paper_enabled: true,
      });
    });
    expect(screen.getByTestId("autonomous-paper-switch-input")).toBeChecked();
    expect(screen.getByTestId("autonomous-paper-switch-status")).toHaveTextContent("Stale");
    const tiles = screen.getAllByTestId("status-tile");
    expect(tiles.map((tile) => [tile.textContent, tile.dataset.tone])).toEqual([
      ["Environment ceilingOn", "ok"],
      ["EffectiveOff", "unknown"],
      ["WorkerStale", "warn"],
      ["ModeOff", "unknown"],
    ]);
  });

  it("keeps the paper switch disabled for a trader membership even if user.role says owner", () => {
    auth.user = { id: "user", role: "owner" };
    auth.membershipRole = "trader";
    render(<BotSettings />);
    expect(screen.getByTestId("autonomous-paper-switch-input")).toBeDisabled();
  });

  it("lets an owner edit bot limits with no owner-only reason", () => {
    auth.membershipRole = "owner";
    render(<BotSettings />);
    expect(screen.getByTestId("autonomous-max-trades")).toBeEnabled();
    expect(screen.queryByTestId("autonomous-save-owner-reason")).not.toBeInTheDocument();
    expect(screen.queryByTestId("autonomous-switch-owner-reason")).not.toBeInTheDocument();
  });

  it("locks bot limits for a trader, names their role, and keeps account limits editable", () => {
    render(<BotSettings />);
    expect(screen.getByTestId("autonomous-max-trades")).toBeDisabled();
    expect(screen.getByTestId("autonomous-strategy")).toBeDisabled();
    expect(screen.getByTestId("risk-max-trades")).toBeEnabled();
    expect(screen.getByTestId("autonomous-save-owner-reason")).toHaveTextContent(
      "Bot limits: Owner only (your role: Trader)",
    );
    expect(screen.getByTestId("autonomous-switch-owner-reason")).toHaveTextContent(
      "Owner only (your role: Trader)",
    );
  });

  it("shows an unknown role when the membership role is missing", () => {
    auth.membershipRole = null;
    render(<BotSettings />);
    expect(screen.getByTestId("autonomous-max-trades")).toBeDisabled();
    expect(screen.getByTestId("autonomous-paper-switch-input")).toBeDisabled();
    expect(screen.getByTestId("autonomous-save-owner-reason")).toHaveTextContent(
      "Owner only (your role: unknown)",
    );
  });

  it("renders one limits form with account and bot values side by side", () => {
    render(<BotSettings />);
    const form = screen.getByTestId("trading-limits-card");
    expect(form).toHaveAttribute("id", "limits");
    expect(screen.getByTestId("risk-max-trades")).toHaveValue(20);
    expect(screen.getByTestId("autonomous-max-trades")).toHaveValue("5");
    expect(screen.getByText("Account: max trades/day (all trades)")).toBeInTheDocument();
    expect(screen.getByText("Bot: max trades/day (≤ account)")).toBeInTheDocument();
    expect(screen.queryByTestId("autonomous-settings-form-card")).not.toBeInTheDocument();
    expect(screen.queryByTestId("risk-settings-form-card")).not.toBeInTheDocument();
  });
});
