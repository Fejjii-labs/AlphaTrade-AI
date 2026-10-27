import type { Timeframe, TradeDirection } from "./types";

export type BrainFamily = "nested_continuation" | "sfp";
export type BrainKind = "operational_nested_continuation/v1" | "swing_failure_pattern/v1";
export interface BrainDraftBinding {
  symbol: string;
  direction: TradeDirection;
  trigger_timeframe: Timeframe;
}
export interface SfpParameters {
  version: "sfp-research/v1";
  provisional: true;
  level_lookback: number;
  pivot_width: number;
  minimum_level_significance: string;
  minimum_sweep_depth: string;
  maximum_sweep_depth: string | null;
  equal_level_tolerance: string;
  reclaim_window: number;
  confirmation_window: number;
  breakout_confirmation_closes: number;
  structural_invalidation_buffer: string;
  expiry_bars: number;
  required_evidence_max_age_bars: number;
  quality_lookback: number;
  htf_alignment_tolerance: string;
  confirmation: "closed_break_of_reclaim_extreme";
}
export interface SfpDraft extends BrainDraftBinding {
  parameters: SfpParameters;
  paper_only: true;
}

export interface BrainStrategy {
  strategy_id: string;
  version_id: string;
  version: number;
  name: string;
  status: string;
  enabled: boolean;
  execution_permission: string;
  spec: BrainDraftBinding & { kind: BrainKind; parameters: Record<string, unknown> };
}
export interface BrainSetup {
  setup_id: string;
  strategy_version_id: string;
  state: string;
  symbol?: string;
  instrument: string;
  direction: string;
  timeframe: Timeframe;
  family?: BrainFamily;
  stage?: string;
  condition?: string;
  observed_at: string;
  expires_at: string;
  freshness: string;
  fresh_until: string;
  risk_state: string;
  reason_codes: string[];
  evidence_reference: string;
  evidence: Record<string, string>;
  candidate_id: string | null;
  decision_id: string | null;
  journal: { id: string; status: string; net_pnl: string | null } | null;
  quality_components: Record<string, string>;
  entry: string | null;
  stop: string | null;
  targets: string[];
  history?: Array<{ record_id: string; kind: string; occurred_at: string; payload: Record<string, unknown> }>;
}
export interface BrainOverview {
  watched_symbols: string[];
  strategies: BrainStrategy[];
  setups: BrainSetup[];
  /** Total matching setups before the server's 20-row bound; absent on older APIs. */
  setup_count?: number;
  limitations: string[];
  paper_only: boolean;
}
