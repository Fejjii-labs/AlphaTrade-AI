import { apiFetch } from "./client";

export interface DashboardDemoAccount {
  venue: "BLOFIN_DEMO";
  account_id?: string | null;
  performance?: {
    status: "partial" | "unavailable";
    currency: "USDT";
    gross_pnl: string | null;
    fees: string | null;
    funding: string | null;
    net_pnl: string | null;
    verified_closed_trades: number;
    unresolved_trades: number;
    manual_test_trades: number;
    strategy_closed_trades: number | null;
    coverage: string;
    activity_sync_status?: "disabled" | "unavailable" | "partial" | "ready";
    pnl_method?: string;
    win_rate?: string | null;
    trade_count?: number | null;
    wins?: number | null;
    losses?: number | null;
    realized_pnl?: string | null;
    window_start?: string | null;
    window_end?: string | null;
    unresolved_round_trips?: number | null;
    activity_coverage?: string;
    manual_mirror_status?: "disabled" | "withheld" | "ready";
    manual_mirror_reason?: string;
    average_r?: string | null;
  };
  read_only: true;
  status:
    "ok" | "degraded" | "stale" | "unavailable" | "not_synced" | "inactive";
  can_refresh: boolean;
  snapshot_id: string | null;
  synced_at: string | null;
  expires_at: string | null;
  total_equity_usd: string | null;
  refresh_error: string | null;
  last_attempt_at: string | null;
  balances: Array<{ asset: string; total: string; available: string; equity: string | null }>;
  positions: Array<{
    symbol: string;
    side: "long" | "short";
    contracts: string;
    base_asset: string | null;
    quote_asset?: string | null;
    base_quantity: string | null;
    entry_price: string | null;
    mark_price: string | null;
    unrealized_pnl: string | null;
    leverage: string | null;
  }>;
  balances_truncated: boolean;
  positions_truncated: boolean;
  position_count: number | null;
  refresh_interval_seconds?: number;
  message: string;
}

export const demoAccountApi = {
  latest: (signal?: AbortSignal) =>
    apiFetch<DashboardDemoAccount>("/dashboard/demo-account", { auth: true, signal }),
  refresh: (signal?: AbortSignal) =>
    apiFetch<DashboardDemoAccount>("/dashboard/demo-account/refresh", {
      method: "POST",
      auth: true,
      signal,
    }),
};
