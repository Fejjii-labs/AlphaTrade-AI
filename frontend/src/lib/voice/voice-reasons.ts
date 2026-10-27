/**
 * Plain text for server voice reason codes (UI overhaul B7b). Codes come from
 * `GET /agent/voice/config` and `session.error`. A limit means the session ran
 * into a budget, quota, turn or time cap; anything else is "unavailable".
 */
export type VoiceReason = { limit: boolean; text: string };

const LIMITS: Record<string, string> = {
  voice_daily_request_quota_exhausted: "Daily voice limit reached.",
  voice_monthly_cost_quota_exhausted: "Monthly voice limit reached.",
  voice_monthly_token_quota_exhausted: "Monthly voice limit reached.",
  voice_agent_feature_quota_exhausted: "Monthly voice limit reached.",
  voice_approved_budget_exhausted: "Voice budget used up.",
  verified_cap_cannot_cover_session: "Voice budget used up.",
  voice_turn_cap_exhausted: "Turn limit reached for this voice session.",
  voice_response_cap_exhausted: "Turn limit reached for this voice session.",
  voice_reasoning_attempt_cap: "Turn limit reached for this voice session.",
  live_transcript_cap: "Turn limit reached for this voice session.",
  voice_audio_duration_exhausted: "Time limit reached for this voice session.",
  voice_session_expired: "Time limit reached for this voice session.",
};

const UNAVAILABLE: Record<string, string> = {
  voice_not_enabled: "Voice is off.",
  gpt_live_not_enabled: "Voice is off.",
  realtime_not_enabled: "Voice is off.",
  voice_budget_tenant_mismatch: "Voice is not set up for this workspace.",
  voice_permission_denied: "Your role cannot use voice.",
  voice_provider_unavailable: "The voice provider is unavailable.",
};

/** Null when the reason is missing or unknown, so callers keep their own text. */
export function voiceReason(code: string | null | undefined): VoiceReason | null {
  if (!code) return null;
  if (LIMITS[code]) return { limit: true, text: LIMITS[code] };
  if (UNAVAILABLE[code]) return { limit: false, text: UNAVAILABLE[code] };
  if (
    code === "provider_credential_unbound" ||
    code === "approved_live_route_unavailable" ||
    code.startsWith("verified_")
  )
    return { limit: false, text: "Voice is not set up." };
  return null;
}

/** "2:41" for a countdown. */
export function formatCountdown(ms: number): string {
  const seconds = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
