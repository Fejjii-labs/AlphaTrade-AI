import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { UserRiskSettings } from "@/lib/api/types";

const settings = vi.fn();

vi.mock("@/lib/api", () => ({
  api: { risk: { settings: () => settings() } },
}));

import { RiskSettingsSummary } from "./RiskSettingsSummary";

const base: UserRiskSettings = {
  organization_id: "org",
  user_id: "user",
  daily_loss_limit: "50.00000000",
  daily_target: null,
  max_trades_per_day: 5,
  max_risk_per_trade_percent: "0.50000000",
  default_account_balance: "10240.50000000",
  timezone: "UTC",
  green_day_protection_enabled: true,
  one_loss_stop_enabled: false,
  overtrading_guard_enabled: true,
  notes: null,
  using_defaults: false,
  timezone_fallback: false,
};

describe("RiskSettingsSummary", () => {
  beforeEach(() => settings.mockReset());

  it("formats Decimal strings as amounts and percent", async () => {
    settings.mockResolvedValue(base);
    render(<RiskSettingsSummary />);

    expect(await screen.findByText("50 USDT")).toBeInTheDocument();
    expect(screen.getByText("10,240.5 USDT")).toBeInTheDocument();
    expect(screen.getByText("0.5%")).toBeInTheDocument();
    expect(screen.getByText("Not set")).toBeInTheDocument();
    expect(screen.queryByText(/0{4,}/)).not.toBeInTheDocument();
  });
});
