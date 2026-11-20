import type { CanonicalJournalTradeListItem } from "./api/types";

/** The lifecycle link is projected by execution, never accepted from a manual claim. */
export function isAlphaTradeBloFinExecution(
  trade: CanonicalJournalTradeListItem,
): boolean {
  return Boolean(
    /blofin/i.test(trade.exchange ?? "") &&
    ["manual_demo_test", "paper_execution"].includes(trade.source ?? "") &&
    trade.execution_lifecycle_id &&
    trade.entry_time &&
    !["planned", "cancelled"].includes(trade.status),
  );
}

/** Trades page rows: bot executions on BloFin demo plus my own mirrored BloFin demo trades. */
export function isBloFinDemoTrade(trade: CanonicalJournalTradeListItem): boolean {
  if (trade.source === "blofin_demo_manual") {
    return Boolean(trade.entry_time && !["planned", "cancelled"].includes(trade.status));
  }
  return isAlphaTradeBloFinExecution(trade);
}
