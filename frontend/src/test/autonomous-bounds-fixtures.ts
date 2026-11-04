import type { AutonomousSettingsBounds } from "@/lib/api/types";

/** GET /autonomous/settings/bounds as the server returns it with default config. */
export const autonomousBounds: AutonomousSettingsBounds = {
  bot_risk_per_trade_percent: { min: "0", min_exclusive: true, max: "5", editable: true, source: "code_ceiling" },
  bot_daily_loss_limit_percent: { min: "0", min_exclusive: true, max: "6", editable: true, source: "code_ceiling" },
  bot_max_trades_per_day: { min: "1", min_exclusive: false, max: "1000", editable: true, source: "request_schema" },
  account_max_risk_per_trade_percent: { min: "0", min_exclusive: true, max: "10", editable: true, source: "request_schema" },
  account_max_trades_per_day: { min: "1", min_exclusive: false, max: null, editable: true, source: "request_schema" },
  stop_loss_band: {
    configured_min_percent: "1",
    configured_max_percent: "5",
    floor_percent: "1",
    ceiling_percent: "5",
    configured_valid: true,
    editable: false,
    source: "environment",
  },
  leverage: { profile_max_leverage_ceiling: "20", demo_hard_cap: "20", demo_effective_max: "20" },
  profiles: [
    {
      kind: "internal_paper",
      risk_per_trade_percent_ceiling: "5",
      notional_percent_ceiling: "2000",
      aggregate_open_risk_percent_ceiling: "6",
      daily_loss_percent_ceiling: "6",
      max_leverage_ceiling: "20",
      editable: false,
      source: "risk_policy_profile",
    },
    {
      kind: "demo",
      risk_per_trade_percent_ceiling: "5",
      notional_percent_ceiling: "2000",
      aggregate_open_risk_percent_ceiling: "6",
      daily_loss_percent_ceiling: "6",
      max_leverage_ceiling: "20",
      editable: false,
      source: "risk_policy_profile",
    },
  ],
  real_trading_enabled: false,
};
