import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AutonomousTradingSettingsPage from "./page";

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
      updateSettings: vi.fn(),
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
      data: settings,
      loading: false,
      error: null,
      reload: vi.fn(),
    });
  });

  it("renders typed fields and selectors", () => {
    render(<AutonomousTradingSettingsPage />);
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
    render(<AutonomousTradingSettingsPage />);
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
  });

  it("keeps the paper switch disabled for a trader membership even if user.role says owner", () => {
    auth.user = { id: "user", role: "owner" };
    auth.membershipRole = "trader";
    render(<AutonomousTradingSettingsPage />);
    expect(screen.getByTestId("autonomous-paper-switch-input")).toBeDisabled();
  });

  it("enables Save for an owner with no owner-only reason", () => {
    auth.membershipRole = "owner";
    render(<AutonomousTradingSettingsPage />);
    expect(screen.getByRole("button", { name: /save settings/i })).toBeEnabled();
    expect(screen.queryByTestId("autonomous-save-owner-reason")).not.toBeInTheDocument();
    expect(screen.queryByTestId("autonomous-switch-owner-reason")).not.toBeInTheDocument();
  });

  it("disables Save for a trader and names their role", () => {
    render(<AutonomousTradingSettingsPage />);
    expect(screen.getByRole("button", { name: /save settings/i })).toBeDisabled();
    expect(screen.getByTestId("autonomous-save-owner-reason")).toHaveTextContent(
      "Owner only (your role: Trader)",
    );
    expect(screen.getByTestId("autonomous-switch-owner-reason")).toHaveTextContent(
      "Owner only (your role: Trader)",
    );
  });

  it("shows an unknown role when the membership role is missing", () => {
    auth.membershipRole = null;
    render(<AutonomousTradingSettingsPage />);
    expect(screen.getByRole("button", { name: /save settings/i })).toBeDisabled();
    expect(screen.getByTestId("autonomous-paper-switch-input")).toBeDisabled();
    expect(screen.getByTestId("autonomous-save-owner-reason")).toHaveTextContent(
      "Owner only (your role: unknown)",
    );
  });

  it("labels the bot cap and shows the effective cap held by the account limit", () => {
    useAsyncDataMock.mockReturnValue({
      data: { ...settings, using_defaults: false, max_trades_per_day: 5, account_max_trades_per_day: 3, effective_max_trades_per_day: 3 },
      loading: false,
      error: null,
      reload: vi.fn(),
    });
    render(<AutonomousTradingSettingsPage />);
    expect(screen.getByText("Bot: max trades/day (≤ account)")).toBeInTheDocument();
    expect(screen.getByTestId("autonomous-effective-max-trades")).toHaveTextContent(
      "Effective 3 (capped by account limit of 3)",
    );
  });

  it("shows the account cap as effective until bot settings are saved", () => {
    render(<AutonomousTradingSettingsPage />);
    expect(screen.getByTestId("autonomous-effective-max-trades")).toHaveTextContent(
      "Effective 20 · the bot limit applies once saved",
    );
  });

  it("explains a bot cap above the account cap", async () => {
    const { api, ApiError } = await import("@/lib/api");
    vi.mocked(api.autonomous.updateSettings).mockRejectedValue(
      new ApiError("max_trades_per_day_exceeds_account", 422, null),
    );
    auth.membershipRole = "owner";
    render(<AutonomousTradingSettingsPage />);
    fireEvent.change(screen.getByTestId("autonomous-max-trades"), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: /save settings/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Bot max trades per day cannot exceed the account limit of 20.",
      );
    });
  });

  it("shows a ceiling validation message from the API", async () => {
    auth.membershipRole = "owner";
    const { api, ApiError } = await import("@/lib/api");
    vi.mocked(api.autonomous.updateSettings).mockRejectedValue(
      new ApiError("daily_loss_limit_exceeds_ceiling", 422, null),
    );
    render(<AutonomousTradingSettingsPage />);
    fireEvent.change(screen.getByTestId("autonomous-daily-loss-percent"), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save settings/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/cannot exceed 6%/i);
    });
  });
});
