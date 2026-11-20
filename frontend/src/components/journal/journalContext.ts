export type JournalQueryContext = {
  proposalId: string | null;
  positionId: string | null;
  entryId: string | null;
  tradeId: string | null;
  sessionId: string | null;
};

export type JournalContextIssue = {
  kind: "invalid_prefill" | "stale_entry" | "unsupported_trade" | "invalid_session";
  message: string;
};

type SearchParamsLike = { get: (key: string) => string | null };

/** Parse typed journal deep-link / quick-entry query parameters. */
export function parseJournalQuery(searchParams: SearchParamsLike): JournalQueryContext {
  return {
    proposalId: searchParams.get("proposal_id"),
    positionId: searchParams.get("position_id"),
    entryId: searchParams.get("entry"),
    tradeId: searchParams.get("trade_id") ?? searchParams.get("trade"),
    sessionId: searchParams.get("session_id") ?? searchParams.get("run_session_id"),
  };
}

export function hasPrefillContext(context: JournalQueryContext): boolean {
  return Boolean(context.proposalId || context.positionId);
}

export function journalEntryHref(entryId: string): string {
  return `/journal?entry=${encodeURIComponent(entryId)}`;
}

export function relatedPlanHref(proposalId: string): string {
  return `/proposals?id=${encodeURIComponent(proposalId)}`;
}

export function relatedValidationHref(sessionId: string): string {
  return `/paper-validation/run-sessions/${encodeURIComponent(sessionId)}`;
}

export function relatedLessonsHref(candidateId?: string | null): string {
  if (candidateId) {
    return `/lessons?candidate=${encodeURIComponent(candidateId)}`;
  }
  return "/lessons";
}

/** Knowledge moved into Agent → Documents; old `/journal?tab=knowledge…` links follow it. */
export function knowledgeRedirect(searchParams: URLSearchParams): string | null {
  if (searchParams.get("tab") !== "knowledge") return null;
  const next = new URLSearchParams(searchParams);
  next.set("tab", "documents");
  return `/agent?${next}`;
}
