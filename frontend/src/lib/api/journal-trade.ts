import { apiFetch } from "./client";
import type { ManualDemoAttempt } from "./manual-demo";
import type { CanonicalJournalTradeListItem } from "./types";

export type JournalObservation = {
  id: string;
  category: string;
  observation: string;
  created_at: string;
};
/** One recorded partial exit: a take-profit/stop fill or a stop move. */
export type JournalTradeExitEvent = {
  kind: "fill" | "stop_move" | string;
  fill_kind: "target" | "stop" | "runner_stop" | string | null;
  leg: number | null;
  quantity: string | null;
  price: string | null;
  stop_reason: "breakeven" | "trail" | string | null;
  previous_stop: string | null;
  new_stop: string | null;
  occurred_at: string;
};
export type JournalTradeDetail = {
  trade: CanonicalJournalTradeListItem & {
    size: string | null;
    gross_pnl: string | null;
    funding: string | null;
    exit_time: string | null;
    execution_lifecycle_id: string | null;
    notes?: string | null;
    grade?: string | null;
    leverage?: string | null;
    external_ref?: string | null;
    planned_stop_price?: string | null;
    exit_reason?: string | null;
    tags?: string[] | null;
  };
  manual_demo?: ManualDemoAttempt | null;
  evidence: Array<{ id: string; kind: string; caption: string | null; ref: string | null }>;
  rule_checks: Array<{ id: string; rule_key: string; status: string; notes: string | null }>;
  observations: JournalObservation[];
  exit_events?: JournalTradeExitEvent[];
};
export const journalTradeApi = {
  detail: (id: string) => apiFetch<JournalTradeDetail>(`/journal/trades/${encodeURIComponent(id)}`, { auth: true }),
  reflect: (id: string, observation: string) => apiFetch<JournalObservation>(
    `/journal/trades/${encodeURIComponent(id)}/observations`, {
      auth: true,
      method: "POST",
      body: JSON.stringify({ category: "behavioral", observation, emotion_tags: [] }),
    },
  ),
  annotate: (
    id: string,
    body: { strategy_label?: string; grade?: string; notes?: string },
  ) => apiFetch<JournalTradeDetail["trade"]>(
    `/journal/trades/${encodeURIComponent(id)}/annotation`,
    { auth: true, method: "PATCH", body: JSON.stringify(body) },
  ),
};
