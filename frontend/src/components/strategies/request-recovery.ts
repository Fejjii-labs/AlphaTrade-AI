import { ApiError } from "@/lib/api/client";

// These exact application refusals occur before either endpoint commits. Unknown
// responses, concurrency conflicts, timeouts and status zero retain retry identity.
export function rejectedBeforeCommit(cause: unknown, endpoint: "draft" | "screening"): boolean {
  if (!(cause instanceof ApiError) || !cause.body || typeof cause.body !== "object") return false;
  const error = (cause.body as { error?: unknown }).error;
  if (!error || typeof error !== "object") return false;
  const code = (error as { code?: unknown }).code;
  if (typeof code !== "string") return false;
  const common: Record<number, string[]> = { 401: ["unauthorized"], 403: ["forbidden", "readonly_persistence_denied"], 404: ["not_found"], 422: ["validation_error"] };
  if (common[cause.status]?.includes(code)) return true;
  if (endpoint === "draft") return cause.status === 409 && ["experiment_account_invalid", "experiment_account_unverified", "experiment_strategy_mismatch", "experiment_adapter_unavailable"].includes(code);
  return (cause.status === 503 && ["screening_disabled", "screening_source_not_enabled"].includes(code))
    || (cause.status === 409 && ["screening_family_mismatch", "screening_strategy_mismatch", "screening_configuration_mismatch"].includes(code));
}
