import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountLimitsCard } from "./AccountLimitsCard";

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

describe("Account limits card", () => {
  afterEach(() => {
    cleanup();
    useAsyncDataMock.mockClear();
  });

  beforeEach(() => {
    useAsyncDataMock.mockReturnValue({
      data: settings,
      loading: false,
      error: null,
      reload: vi.fn(),
    });
  });

  it("shows the account cap, loss limit and risk per trade first, the rest under More", () => {
    render(<AccountLimitsCard />);
    expect(screen.getByTestId("risk-settings-form-card")).toHaveAttribute("id", "account");
    expect(screen.getByText("Account limits (all trades)")).toBeInTheDocument();
    expect(screen.getByText("Account: max trades/day (all trades)")).toBeVisible();
    expect(screen.getByTestId("risk-max-trades")).toHaveValue(5);
    expect(screen.getByTestId("risk-daily-loss-limit")).toHaveValue("50");
    expect(screen.getByTestId("risk-max-risk-percent")).toHaveValue("1");
    const more = screen.getByText("More (target, balance, timezone)").closest("details");
    expect(more).not.toHaveAttribute("open");
    for (const id of ["risk-daily-target", "risk-default-balance", "risk-timezone", "risk-green-day", "risk-notes"])
      expect(more).toContainElement(screen.getByTestId(id));
  });

  it("saves the account limits", async () => {
    const { api } = await import("@/lib/api");
    vi.mocked(api.risk.updateSettings).mockResolvedValue({ ...settings, max_trades_per_day: 3 });
    render(<AccountLimitsCard />);
    fireEvent.change(screen.getByTestId("risk-max-trades"), { target: { value: "3" } });
    fireEvent.click(screen.getByTestId("risk-settings-save"));
    await waitFor(() =>
      expect(api.risk.updateSettings).toHaveBeenCalledWith(expect.objectContaining({ max_trades_per_day: 3 })),
    );
    expect(await screen.findByTestId("risk-settings-saved")).toHaveTextContent("Risk settings saved.");
  });

  it("renders loading and error states", () => {
    useAsyncDataMock.mockReturnValue({
      data: null,
      loading: true,
      error: null,
      reload: vi.fn(),
    });
    const { rerender } = render(<AccountLimitsCard />);
    expect(screen.getByText(/loading risk settings/i)).toBeInTheDocument();

    useAsyncDataMock.mockReturnValue({
      data: null,
      loading: false,
      error: "settings down",
      reload: vi.fn(),
    });
    rerender(<AccountLimitsCard />);
    expect(screen.getByText(/settings down/i)).toBeInTheDocument();
  });
});
