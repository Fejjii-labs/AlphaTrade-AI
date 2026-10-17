import type { NativeActivityItem, NativeActivityPage } from "@/lib/api/blofin-activity";

export const activityOrganization = "11111111-1111-4111-8111-111111111111";
export function nativeFill(overrides: Partial<NativeActivityItem> = {}): NativeActivityItem {
  return {
    kind: "fill", native_id: "native-fill-1", order_id: "native-order-1", trade_id: "native-fill-1",
    client_order_id: null, instrument: "BTC-USDT", side: "buy", position_side: "long",
    occurred_at_ms: "1791626400123", created_at_ms: null, updated_at_ms: null,
    quantity: "0.123456789123456789", quantity_unit: "contracts", filled_quantity: null,
    price: "67000.123456789123456789", average_price: null, state: null, order_type: null,
    reduce_only: null, fee: "-0.000123456789123456789", fee_currency: null,
    realized_pnl: null, funding: null, origin: "native", command_id: null, strategy_id: null,
    contract_multiplier: "0.001", contract_type: "linear", base_currency: "BTC", settlement_currency: "USDT",
    metadata_observed_at: "2026-10-10T10:00:00Z", ...overrides,
  };
}

export function activityPage(overrides: Partial<NativeActivityPage> = {}): NativeActivityPage {
  return {
    schema_version: "BloFinActivityV1", organization_id: activityOrganization,
    venue: "BLOFIN", environment: "demo", account_uid: "synthetic-native-account",
    identity_status: "verified", identity_error_code: null, identity_verified_at: "2026-10-10T10:00:00Z",
    items: [nativeFill()], next_cursor: null,
    coverage: ["fill", "order"].map(kind => ({
      kind: kind as "fill" | "order", selection: kind === "fill" ? "time_window" as const : "cursor_sweep" as const,
      window_begin_ms: kind === "fill" ? "1791021600000" : null, window_end_ms: kind === "fill" ? "1791626400000" : null,
      native_cursor: null, window_complete: true, covered_begin_ms: kind === "fill" ? "1791021600000" : null, covered_end_ms: kind === "fill" ? "1791626400000" : null,
      gap_detected: false, last_successful_sync: "2026-10-10T10:00:00Z", last_attempt_at: "2026-10-10T10:00:00Z",
      last_error_code: null, next_retry_at: null,
    })),
    freshness: "fresh", partial_coverage: true,
    limitations: ["Bounded futures history; older history and funding are not established."],
    generated_at: "2026-10-10T10:00:01Z", ...overrides,
  };
}
