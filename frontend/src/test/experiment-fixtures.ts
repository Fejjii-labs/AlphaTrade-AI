import type { Experiment, ExperimentPage, ExperimentVersion } from "@/lib/experiments";

export const experimentId = "11111111-1111-4111-8111-111111111111";
export const versionId = "22222222-2222-4222-8222-222222222222";
export function experimentVersion(overrides: Partial<ExperimentVersion> = {}): ExperimentVersion {
  return {
    id: versionId, experiment_id: experimentId,
    organization_id: "33333333-3333-4333-8333-333333333333",
    user_id: "44444444-4444-4444-8444-444444444444", version: 1, parent_version_id: null,
    state: "approved", revision: 2, configuration_hash: "a".repeat(64), strategy_content_hashes: {},
    sample_group_id: "55555555-5555-4555-8555-555555555555", sample_counts: { baseline: 0 },
    created_at: "2026-10-10T10:00:00Z", submitted_at: "2026-10-10T10:01:00Z",
    approved_at: "2026-10-10T10:02:00Z", approved_by: "44444444-4444-4444-8444-444444444444",
    authorized_until: "2026-10-11T10:00:00Z", started_at: null, paused_at: null, completed_at: null,
    promoted_at: null, promotion_version_id: null, runtime_activated: false, performance: null,
    configuration: {
      contract_version: "experiment-config/v1", mode: "exploration",
      account: { execution_account_id: "66666666-6666-4666-8666-666666666666", source: "internal_simulation", native_uid: null, execution_identity_audit_id: null },
      family: "operational_nested_continuation/v1",
      strategy_id: "77777777-7777-4777-8777-777777777777", strategy_version_id: "88888888-8888-4888-8888-888888888888",
      variants: [{ key: "baseline", strategy_version_id: "88888888-8888-4888-8888-888888888888", parameters: {} }],
      symbols: ["BTCUSDT"], timeframes: ["5m"], model_policy: { mode: "disabled", max_cost_usd: "0" },
      sample_target: { kind: "setup_observation", minimum: 30, maximum: 100 },
      risk_limits: { quote_currency: "USDT", max_risk_per_trade: "10", max_position_notional: "500", max_total_exposure: "1000", max_daily_loss: "50", max_weekly_loss: "100", max_drawdown: "100", max_leverage: "2", max_open_positions: 5, max_trades_per_day: 10, max_trades_total: 100, cost_allowance: "1" },
    }, ...overrides,
  };
}
export function experimentFixture(version = experimentVersion()): Experiment {
  return { id: version.experiment_id, name: "Nested comparison", versions: [version] };
}
export function experimentPage(items = [experimentFixture()]): ExperimentPage {
  return { items, total: items.length, limit: 12, offset: 0 };
}
