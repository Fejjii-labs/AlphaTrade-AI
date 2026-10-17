/** Synthetic contract fixtures: no database or provider acceptance is implied. */
import type { components } from "@/lib/api/generated/types";
export const FIXTURE_UUID = "11111111-1111-4111-8111-111111111111";
export const agentTurnFixture: components["schemas"]["AgentTurnResult"] = {
  conversation_id: FIXTURE_UUID,
  user_message_id: "22222222-2222-4222-8222-222222222222",
  assistant_message_id: "33333333-3333-4333-8333-333333333333",
  capability: "general_conversation", operation: "read", artifact_kinds: [],
  reply: "Acknowledged fixture reply.", proposals: [], limitations: [],
  paper_safety: { exchange_mode: "paper_internal" },
  authority_mutated: false, execution_attempted: false, real_trading_enabled: false,
};
export const attentionFixture: components["schemas"]["AttentionQueue"] = {
  schema_version: "AttentionQueue/v1", organization_id: FIXTURE_UUID, user_id: FIXTURE_UUID,
  generated_at: "2026-10-09T12:00:00Z", items: [], recommended_next_action: null, limitations: [],
};
export const dailyReviewFixture: components["schemas"]["DailyReview"] = {
  review_id: FIXTURE_UUID, content_hash: "a".repeat(64), organization_id: FIXTURE_UUID,
  user_id: FIXTURE_UUID, window: { day: "2026-10-09", timezone: "UTC",
    start: "2026-10-09T00:00:00Z", end: "2026-10-10T00:00:00Z" },
  generated_at: "2026-10-09T12:00:00Z", facts: [], user_observations: [], system_inference: [],
  research_suggestions: [], counts: {}, daily_pnl: [], limitations: [],
};
