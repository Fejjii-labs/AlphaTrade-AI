import type { trendpulseScreen } from "@/lib/api/generated/client";
import { experimentVersion } from "./experiment-fixtures";
export function researchReceipt(): Awaited<ReturnType<typeof trendpulseScreen>> {
  const v = experimentVersion();
  return { contract_version: "trendpulse-screening/v1", id: "99999999-9999-4999-8999-999999999999", request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", organization_id: v.organization_id,
    experiment_id: v.experiment_id, experiment_version_id: v.id, configuration_hash: "a".repeat(64), variant_key: "baseline", strategy_version_id: v.configuration.strategy_version_id, strategy_content_hash: "b".repeat(64),
    trigger_end: "2026-10-10T12:05:00Z", acquisition_started_at: "2026-10-10T12:06:00Z", decision_at: "2026-10-10T12:06:00Z", evidence_mode: "replay", receipt_provenance: "synthetic_fixture", status: "refused", reason: "trigger_expired", trend_receipts: 0, entry_receipts: 0, evidence_hash: "c".repeat(64), signal_id: null, duplicate_of: null,
    execution_authorized: false, management_authority: false, sample_eligible: false, performance: null, evidence: { trend_bars: [], trend_observations: [], entry_bars: [], entry_observations: [], instrument_rules: null }, signal: null };
}
