/**
 * Bot decision reason codes as one plain sentence. Exact codes first, then
 * keyword families; anything unknown reads as a generic safety-check skip so a
 * raw code never reaches the screen.
 */
const EXACT: Record<string, string> = {
  insufficient_daily_loss_allocation: "Daily loss budget used up.",
  demo_leverage_exceeds_profile_max: "Leverage is above the risk profile maximum.",
  demo_leverage_exceeds_ceiling: "Leverage is above the allowed maximum.",
  demo_liquidation_inside_stop: "Liquidation would hit before the stop.",
  demo_liquidation_inside_stop_buffer: "Liquidation would be too close to the stop.",
  demo_liquidation_estimate_invalid: "Could not estimate the liquidation price.",
  demo_maintenance_tiers_unavailable: "BloFin margin tiers were unavailable.",
  risk_limits_unverified: "Risk limits could not be checked.",
  kill_switch_unverified: "Kill switch state could not be checked.",
  stale_evidence: "Market data was too old.",
  no_trade_rules: "The strategy has no trade rules.",
};

const FAMILIES: Array<[RegExp, string]> = [
  [/kill_switch/, "Paused by the kill switch."],
  [/daily_loss|daily_target/, "Daily loss limit reached."],
  [/trades_per_day|daily_trade|trade_cap|bot_cap/, "Daily trade limit reached."],
  [/trade_slots|open_positions/, "Too many open positions."],
  [/exposure/, "Too much open exposure."],
  [/notional|position_size/, "Position would be too large."],
  [/liquidation/, "Liquidation would be too close to the stop."],
  [/leverage/, "Leverage setting does not match."],
  [/stop/, "Stop distance not allowed."],
  [/grade/, "Setup grade not allowed."],
  [/credential|not_connected|exchange_mode/, "BloFin demo is not connected."],
  [/stale|fresh/, "Market data was too old."],
  [/unreconciled|duplicate|already_open/, "A previous trade is still open."],
];

export function botReasonSentence(code: string | null | undefined): string | null {
  if (!code) return null;
  const key = code.trim().toLowerCase();
  if (EXACT[key]) return EXACT[key];
  for (const [pattern, sentence] of FAMILIES) if (pattern.test(key)) return sentence;
  return "Skipped by a safety check.";
}
