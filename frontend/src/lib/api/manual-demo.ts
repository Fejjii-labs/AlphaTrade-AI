import { apiFetch } from "@/lib/api/client";

export type ManualDemoInput = {
  symbol: "BTCUSDT";
  side: "BUY" | "SELL";
  order_type: "MARKET";
  quantity: string;
  stop: string;
  target: string;
};
export type ManualDemoInstrument = {
  account_id: string;
  instrument: string;
  quantity_unit: "CONTRACTS";
  base_currency: "BTC";
  minimum_quantity: string;
  maximum_quantity: string;
  lot_increment: string;
  tick_size: string;
  contract_multiplier: string;
  minimum_notional: string;
  reference_price: string;
  observed_at: string;
};
export type ManualDemoPreview = {
  origin: "manual demo test";
  account_id: string;
  revision_id: string;
  content_hash: string;
  instrument: string;
  side: "BUY" | "SELL";
  order_type: "MARKET";
  quantity: string;
  quantity_unit: "CONTRACTS";
  base_quantity: string;
  reference_price: string;
  limit_price: null;
  entry_lower: string;
  entry_upper: string;
  stop: string;
  target: string;
  maximum_planned_loss: string;
  gross_reward_risk: string;
  valid_until: string;
  warnings: string[];
};
export type ManualDemoStatus = {
  origin: "manual demo test";
  revision_id: string;
  command_id: string;
  client_order_id: string;
  venue_order_id?: string | null;
  protection_order_ids?: string[];
  status: string;
  filled_quantity: string;
  remaining_quantity: string;
  average_fill_price: string | null;
  fees: string | null;
  protection: string;
  journal_trade_id: string | null;
  missing_evidence: string[];
  execution_status?: string;
  position_status?: string;
  account_status?: string;
  protection_history?: { tpsl_id: string; state: string }[];
  exit_fills?: { identity: string; order_id: string; trade_id: string; quantity: string; price: string; occurred_at: string; fee: string | null; fee_currency?: string | null; fillPnl: string | null; category: string }[];
  observed_at?: string | null;
  reconciliation_freshness?: string;
  exit_quantity?: string;
  exit_price?: string | null;
  exit_fees?: string | null;
  venue_reported_fill_pnl?: string | null;
  historical_protection?: string;
  triggered_protection?: string;
  gross_pnl?: string | null;
  entry_fees?: string | null;
  fee_convention?: "positive_cost_negative_rebate" | null;
  funding?: string | null;
  net_pnl?: string | null;
  protection_diagnostics?: NonNullable<ManualDemoStatus["reconciliation_diagnostics"]>;
  recovery_status?: string;
  recovery_reason?: string | null;
  account_claim_command_ids?: string[];
  reservation_status?: string;
  can_reconcile?: boolean;
  can_cancel?: boolean;
  can_resolve?: boolean;
  reconciliation_diagnostics?: {
    stage: string;
    reason_code: string;
    error_type: string;
    endpoint_name: string;
    http_status?: number | null;
    venue_error_code?: string | null;
    field_name?: string | null;
  }[];
};
export const manualDemo = {
  history: (filters: ManualDemoHistoryFilter = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) if (value !== undefined) query.set(key, String(value));
    return apiFetch<ManualDemoHistory>(`/execution/manual-demo/commands?${query}`, { auth: true });
  },
  detail: (command: string) => apiFetch<ManualDemoAttempt>(`/execution/manual-demo/commands/${encodeURIComponent(command)}`, { auth: true }),
  resolve: (command: string) => apiFetch<ManualDemoStatus>(`/execution/manual-demo/${encodeURIComponent(command)}/resolve`, { auth: true, method: "POST", body: JSON.stringify({ confirm: true }) }),
  instrument: () => apiFetch<ManualDemoInstrument>("/execution/manual-demo/instrument", { auth: true }),
  preview: (body: ManualDemoInput) => apiFetch<ManualDemoPreview>("/execution/manual-demo/preview", { auth: true, method: "POST", body: JSON.stringify(body) }),
  confirm: (preview: ManualDemoPreview) => apiFetch<ManualDemoStatus>("/execution/manual-demo/confirm", { auth: true, method: "POST", body: JSON.stringify({ revision_id: preview.revision_id, content_hash: preview.content_hash, confirm: true, label: "manual demo test" }) }),
  reconcile: (command: string) => apiFetch<ManualDemoStatus>(`/execution/manual-demo/${encodeURIComponent(command)}/reconcile`, { auth: true, method: "POST" }),
  cancel: (command: string) => apiFetch<ManualDemoStatus>(`/execution/manual-demo/${encodeURIComponent(command)}/cancel`, { auth: true, method: "POST", body: JSON.stringify({ confirm: true }) }),
};

export type ManualDemoAttempt = {
  command_id: string;
  account_id: string;
  account_name: string;
  venue: "BLOFIN_DEMO";
  origin: "manual_demo_test";
  attempted_at: string;
  submitted_at: string | null;
  symbol: string;
  side: "BUY" | "SELL";
  requested_contracts: string;
  base_quantity: string;
  stop: string;
  target: string;
  content_hash: string;
  submission_outcome: "ALLOW" | "BLOCKED";
  blocked_reason: string | null;
  detail_url: string;
  evidence: ManualDemoStatus;
};
export type ManualDemoHistory = { items: ManualDemoAttempt[]; total: number; limit: number; offset: number };
export type ManualDemoHistoryFilter = {
  limit?: number; offset?: number; account_id?: string; symbol?: string;
  side?: "BUY" | "SELL"; requested_quantity?: string; since?: string; until?: string;
  submission_status?: "attempt" | "blocked" | "submitted" | "uncertain" | "filled";
};
