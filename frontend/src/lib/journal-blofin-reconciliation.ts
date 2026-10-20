const HISTORY_REF_PREFIX = "blofin-demo-history:";
export const BLOFIN_RECONCILED_TAG = "blofin-reconciled";

type JournalTradeIdentity = {
  source?: string | null;
  external_ref?: string | null;
  tags?: string[] | null;
};

/** AlphaTrade manual rows closed in place by the BloFin demo mirror (PR 282 / ADR-112). */
export function isBlofinReconciledJournalTrade(trade: JournalTradeIdentity): boolean {
  const ref = trade.external_ref ?? "";
  if (!ref.startsWith(HISTORY_REF_PREFIX)) {
    return false;
  }
  if (trade.source === "blofin_demo_manual") {
    return false;
  }
  if (trade.source === "manual" || trade.source === "manual_demo_test") {
    return true;
  }
  return (trade.tags ?? []).includes(BLOFIN_RECONCILED_TAG);
}

export function blofinHistoryId(externalRef: string | null | undefined): string | null {
  const ref = externalRef ?? "";
  if (!ref.startsWith(HISTORY_REF_PREFIX)) {
    return null;
  }
  const historyId = ref.slice(HISTORY_REF_PREFIX.length).trim();
  return historyId || null;
}
