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
  strategy_options: [{ strategy_id: "strategy-1", name: "Nested BTC" }],
  symbol_options: ["BTCUSDT", "ETHUSDT"],
  max_daily_loss_percent_ceiling: "6",
  max_risk_per_trade_percent_ceiling: "5",
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
  useAuth: () => ({ user: { id: "user" }, organization: { id: "org" } }),
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
  });

  it("shows a ceiling validation message from the API", async () => {
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
