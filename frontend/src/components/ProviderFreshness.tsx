import type { ProviderStatusResponse } from "@/lib/api/types";

/** Shows when provider statuses were last checked; flags a stale cache as unknown. */
export function ProviderFreshness({ providers }: { providers: ProviderStatusResponse }) {
  const checked = providers.checked_at ?? null;
  const age = providers.age_seconds;
  const ageText = typeof age === "number" ? ` (${Math.round(age)}s ago)` : "";
  if (providers.stale || !checked) {
    return (
      <p className="text-sm text-amber-400" data-testid="provider-freshness" data-stale="true">
        Provider status unknown: last check {checked ? new Date(checked).toLocaleString() : "never completed"}
        {ageText}. Shown values may be out of date.
      </p>
    );
  }
  return (
    <p className="text-xs text-text-muted" data-testid="provider-freshness" data-stale="false">
      Last checked {new Date(checked).toLocaleString()}
      {ageText}
    </p>
  );
}
