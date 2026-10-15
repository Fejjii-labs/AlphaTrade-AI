import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RiskSettingsPage from "./page";

const settings = {
  organization_id: "org",
  user_id: "user",
  daily_loss_limit: "50",
  daily_target: "100",
  max_trades_per_day: 5,
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

vi.mock("@/contexts/AppContext", () => ({
  useAppContext: () => appContext,
  useSafetyPosture: () => safetyPosture,
}));

vi.mock("@/contexts/ShellFreshnessContext", () => ({
  useShellFreshness: () => ({
    freshness: { state: null },
    setFreshness: vi.fn(),
    clearFreshness: vi.fn(),
  }),
}));

const useAsyncDataMock = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user" }, organization: { id: "org" } }),
}));

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: (...args: unknown[]) => useAsyncDataMock(...args),
}));

vi.mock("@/lib/api", () => ({
  api: {
    risk: {
      settings: vi.fn(),
      updateSettings: vi.fn(),
      resetSettings: vi.fn(),
    },
  },
  ApiError: class ApiError extends Error {},
}));

describe("Risk settings route ownership", () => {
  afterEach(() => {
    cleanup();
    useAsyncDataMock.mockClear();
  });

  beforeEach(() => {
    appContext.killSwitchStatus = { execution_blocked: false, reason: null };
    appContext.killSwitchError = null;
    appContext.loading = false;
    useAsyncDataMock.mockReturnValue({
      data: settings,
      loading: false,
      error: null,
      reload: vi.fn(),
    });
  });

  it("shares the portfolio hub chrome header (FP2-222)", () => {
    render(<RiskSettingsPage />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Risk settings");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("shows the risk BLOCK panel when the kill switch blocks execution", () => {
    appContext.killSwitchStatus = { execution_blocked: true, reason: "Drawdown breach" };
    render(<RiskSettingsPage />);
    expect(screen.getByTestId("portfolio-risk-block")).toHaveTextContent(
      "Kill switch blocking execution: Drawdown breach",
    );
  });

  it("shows no BLOCK panel while the kill switch is clear", () => {
    render(<RiskSettingsPage />);
    expect(screen.queryByTestId("portfolio-risk-block")).not.toBeInTheDocument();
  });

  it("keeps /risk reachable as configuration-only with parent strategy navigation", () => {
    render(<RiskSettingsPage />);
    expect(screen.getByTestId("risk-settings-page")).toBeInTheDocument();
    expect(screen.getByTestId("risk-settings-form-card")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to Strategies/ })).toHaveAttribute("href", "/strategies#risk");
    expect(screen.getByTestId("risk-settings-ownership")).toHaveTextContent(/configuration only/i);
    expect(screen.getByTestId("risk-daily-loss-limit")).toBeInTheDocument();
    expect(screen.getByTestId("paper-mode-indicator")).toBeInTheDocument();
    expect(screen.queryByTestId("portfolio-hub-safety")).not.toBeInTheDocument();
    expect(screen.queryByTestId("paper-mode-banner")).not.toBeInTheDocument();
  });

  it("renders loading and error states", () => {
    useAsyncDataMock.mockReturnValue({
      data: null,
      loading: true,
      error: null,
      reload: vi.fn(),
    });
    const { rerender } = render(<RiskSettingsPage />);
    expect(screen.getByText(/loading risk settings/i)).toBeInTheDocument();

    useAsyncDataMock.mockReturnValue({
      data: null,
      loading: false,
      error: "settings down",
      reload: vi.fn(),
    });
    rerender(<RiskSettingsPage />);
    expect(screen.getByText(/settings down/i)).toBeInTheDocument();
  });
});
