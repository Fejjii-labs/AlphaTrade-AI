import { describe, expect, it } from "vitest";

import { boundBindings, missingBindings, partialBindings } from "@/lib/canonical-decision/bindings";
import {
  composeDecisionCases,
  canonicalPriceFreshnessState,
  deriveProposalStage,
  marketQualityFromAnalysis,
  marketQualityFromCanonicalEvidence,
  perpetualMarketStatusFromCanonical,
} from "@/lib/canonical-decision/compose";
import { projectActionEligibility, projectCanonicalEligibility } from "@/lib/canonical-decision/eligibility";
import { buildDecisionSteps } from "@/lib/canonical-decision/steps";
import { mapPaperExecutionStatus, tradePlanFromProposal } from "@/lib/canonical-decision/trade-plan";
import type {
  ApprovalRequest,
  CanonicalCandidateRead,
  CanonicalEligibilityRead,
  CanonicalEvidenceRead,
  CanonicalMarketMonitorStatusRead,
  MarketAnalyzeResponse,
  PaperValidationCandidateItem,
  TradeProposal,
} from "@/lib/api/types";

const proposal: TradeProposal = {
  id: "prop-1",
  organization_id: "org",
  user_id: "user",
  strategy_id: "htf_trend_pullback",
  symbol: "BTCUSDT",
  timeframe: "1h",
  direction: "long",
  entry_price: "65000",
  position_size: "0.1",
  leverage: "1",
  exit: {
    invalidation: "64000",
    stop_loss: "64000",
    take_profits: [{ price: "67000", size_fraction: 1 }],
  },
  confidence: 0.72,
  risk_level: "medium",
  rationale: "Reclaim with defined invalidation.",
  status: "pending_approval",
  approval_required: true,
  created_at: "2026-09-19T12:00:00.000Z",
};

const approval: ApprovalRequest = {
  id: "appr-1",
  proposal_id: "prop-1",
  organization_id: "org",
  user_id: "user",
  status: "pending",
  risk_level: "medium",
  confidence: 0.72,
  created_at: "2026-09-19T12:01:00.000Z",
};

const candidate: PaperValidationCandidateItem = {
  candidate_id: "cand-1",
  draft_id: "draft-1",
  source_alert_id: "alert-1",
  symbol: "ETHUSDT",
  timeframe: "4h",
  condition: "liquidity_sweep",
  direction: "short",
  confidence: 0.8,
  thesis: "Sweep then displace.",
  entry_criteria: "Reclaim after sweep",
  invalidation_criteria: "Above sweep high",
  checklist_snapshot: {
    trend_checked: true,
    support_resistance_checked: true,
    volume_checked: false,
    risk_reward_checked: true,
    invalidation_checked: true,
    higher_timeframe_checked: false,
    news_or_funding_checked: false,
  },
  risk_mode: "moderate",
  candidate_status: "queued",
  created_at: "2026-09-19T11:00:00.000Z",
};

const canonicalCandidate: CanonicalCandidateRead = {
  authority: "canonical",
  candidate: {
    candidate_id: "cand-canonical-1",
    organization_id: "org",
    state: "active",
    assessment_id: "assess-1",
    evidence_window_hash: "ab".repeat(32),
    strategy_version_id: "strat-ver-1",
    setup_definition_id: "setup-1",
    direction: "long",
    timeframe: "1h",
    evidence_instrument: "BTCUSDT",
    valid_until: "2026-09-19T18:00:00.000Z",
    created_at: "2026-09-19T10:00:00.000Z",
    content_hash: "cd".repeat(32),
  },
};

const canonicalEligibility: CanonicalEligibilityRead = {
  authority: "canonical",
  evaluation: {
    eligibility: {
      eligibility_id: "elig-1",
      candidate_id: "cand-canonical-1",
      assessment_id: "assess-1",
      state: "blocked",
      reason_codes: ["blocked_kill_switch"],
      checked_at: "2026-09-19T12:00:00.000Z",
      valid_until: "2026-09-19T13:00:00.000Z",
    },
    paper_actionable: false,
    live_executable: false,
    content_hash: "ef".repeat(32),
  },
};

describe("canonical decision steps", () => {
  it("marks later paper stages blocked when the kill switch is on", () => {
    const steps = buildDecisionSteps({
      current: "eligibility",
      eligibilityBlocked: true,
      killSwitchActive: true,
    });
    expect(steps.find((step) => step.key === "eligibility")?.status).toBe("blocked");
    expect(steps.find((step) => step.key === "paper_execution")?.status).toBe("blocked");
    expect(steps.find((step) => step.key === "market_assessment")?.status).toBe("complete");
  });
});

describe("action eligibility projection", () => {
  it("keeps liveExecutable false and blocks on kill switch", () => {
    const view = projectActionEligibility({
      killSwitchActive: true,
      executionMode: "paper",
      realTradingEnabled: false,
    });
    expect(view.liveExecutable).toBe(false);
    expect(view.paperActionable).toBe(false);
    expect(view.state).toBe("blocked");
    expect(view.reasonCodes).toContain("blocked_kill_switch");
    expect(view.authority).toBe("compatibility_projection");
  });

  it("blocks real-trading configuration even if paper mode is claimed", () => {
    const view = projectActionEligibility({
      killSwitchActive: false,
      executionMode: "paper",
      realTradingEnabled: true,
    });
    expect(view.reasonCodes).toContain("blocked_configuration");
    expect(view.liveExecutable).toBe(false);
  });

  it("requires human approval before paper execution", () => {
    const view = projectActionEligibility({
      killSwitchActive: false,
      executionMode: "paper",
      realTradingEnabled: false,
      proposal,
      approval,
    });
    expect(view.reasonCodes).toContain("blocked_human_approval_required");
    expect(view.humanApprovalSatisfied).toBe(false);
  });

  it("keeps liveExecutable false on canonical ActionEligibility", () => {
    const view = projectCanonicalEligibility(canonicalEligibility, {
      killSwitchActive: true,
      executionMode: "paper",
      realTradingEnabled: false,
    });
    expect(view.authority).toBe("canonical");
    expect(view.liveExecutable).toBe(false);
    expect(view.paperActionable).toBe(false);
    expect(view.reasonCodes).toContain("blocked_kill_switch");
  });
});

describe("composeDecisionCases", () => {
  it("keeps paper-validation candidates separate from proposals", () => {
    const snapshot = composeDecisionCases({
      canonicalCandidates: [canonicalCandidate],
      candidates: [candidate],
      proposals: [proposal],
      approvals: [approval],
      orders: [],
      journals: [],
      lessons: [],
      killSwitchActive: false,
      executionMode: "paper",
      realTradingEnabled: false,
    });
    expect(snapshot.cases).toHaveLength(3);
    expect(snapshot.cases.some((item) => item.kind === "canonical_candidate")).toBe(true);
    expect(snapshot.cases.some((item) => item.kind === "compatibility_candidate")).toBe(true);
    expect(snapshot.cases.some((item) => item.kind === "legacy_proposal")).toBe(true);
    expect(snapshot.missingBindings).not.toContain("canonical-candidates");
    expect(snapshot.missingBindings).not.toContain("action-eligibility");
    expect(snapshot.missingBindings).not.toContain("canonical-learning-records");
    expect(snapshot.missingBindings).not.toContain("canonical-evidence");
  });

  it("advances a proposal to approval then paper execution", () => {
    expect(
      deriveProposalStage({
        proposal,
        approval,
        order: undefined,
        journal: undefined,
        lesson: undefined,
      }),
    ).toBe("approval");
    expect(
      deriveProposalStage({
        proposal: { ...proposal, status: "approved" },
        approval: { ...approval, status: "approved" },
        order: {
          id: "ord-1",
          symbol: "BTCUSDT",
          side: "buy",
          status: "filled",
          proposal_id: "prop-1",
          created_at: "2026-09-19T12:05:00.000Z",
        },
        journal: undefined,
        lesson: undefined,
      }),
    ).toBe("paper_execution");
  });
});

describe("trade plan mapping", () => {
  it("maps legacy proposal fields without inventing a revision hash", () => {
    const plan = tradePlanFromProposal(proposal);
    expect(plan.authority).toBe("compatibility_projection");
    expect(plan.entry).toBe("65000");
    expect(plan.stop).toBe("64000");
    expect(plan.targets[0]?.price).toBe("67000");
    expect(plan.contentHash).toBeNull();
    expect(plan.approvalRequired).toBe(true);
  });

  it("maps paper order statuses without a live state", () => {
    expect(mapPaperExecutionStatus("filled")).toBe("filled");
    expect(mapPaperExecutionStatus("pending")).toBe("pending");
  });
});

describe("backend bindings", () => {
  it("binds canonical reads and paper-plan execution", () => {
    const missing = missingBindings().map((item) => item.id);
    expect(missing).not.toContain("canonical-candidates");
    expect(missing).not.toContain("action-eligibility");
    expect(missing).not.toContain("execution-receipts");
    expect(missing).not.toContain("paper-execution");
    expect(missing).not.toContain("canonical-learning-records");
    expect(missing).not.toContain("canonical-paper-evaluation");
    expect(missing).not.toContain("canonical-evidence");
    const paperPlan = boundBindings().find((item) => item.id === "paper-execution");
    expect(paperPlan?.path).toBe("POST /execution/paper-plan");
    const paperEval = boundBindings().find((item) => item.id === "canonical-paper-evaluation");
    expect(paperEval?.path).toBe("GET /canonical/paper-evaluation/summary");
    const evidence = boundBindings().find((item) => item.id === "canonical-evidence");
    expect(evidence?.path).toBe("GET /canonical/evidence");
    const monitor = boundBindings().find((item) => item.id === "canonical-market-status");
    expect(monitor?.path).toBe("GET /canonical/market-status");
    const legacyPaper = boundBindings().find((item) => item.id === "legacy-paper-execution");
    expect(legacyPaper).toBeUndefined();
    const analyze = boundBindings().find((item) => item.id === "market-analyze");
    expect(analyze).toBeUndefined();
    expect(partialBindings().some((item) => item.id === "market-analyze")).toBe(true);
  });
});

const replayEvidence: CanonicalEvidenceRead = {
  authority: "canonical",
  live_executable: false,
  watcher_activated: false,
  organization_id: "org",
  symbol: "BTCUSDT",
  source: {
    venue: "binance",
    market_type: "perpetual",
    instrument_id: "binance:usdm_futures:perpetual:BTCUSDT",
    provider_symbol: "BTCUSDT",
    provider_name: "binance-usdm-perpetual-replay",
    source_family: "replay_fixture",
    adapter_version: "binance-usdm-perpetual/v1",
    is_live: false,
    is_mock: true,
    fallback_used: false,
  },
  current_price: {
    usable_as_current_market_price: false,
    presentation: "replay_fixture",
    price: "91234.5",
    source_time: "2026-01-15T16:15:04.000Z",
    venue_trade_id: "agg-1",
    is_live: false,
    is_mock: true,
    fallback_used: false,
    freshness: {
      policy_version: "first-slice-btc-usdt-usdm-freshness/v1",
      state: "fresh",
      evaluated_at: "2026-01-15T16:15:05.000Z",
      source_time: "2026-01-15T16:15:04.000Z",
      age_seconds: "1",
    },
  },
  setup_evidence: {
    available: true,
    evidence_window_hash: "ab".repeat(32),
    trigger_interval_start: "2026-01-15T16:00:00.000Z",
    trigger_interval_end: "2026-01-15T16:15:00.000Z",
    evaluated_at: "2026-01-15T16:15:05.000Z",
    cvd_signed_quote_delta: "-1000",
    signed_flow_ratio: "-0.4",
    completeness: {
      ohlcv_15m: "complete",
      ohlcv_4h: "complete",
      cvd: "complete",
      signed_flow: "complete",
    },
  },
  timestamps: {
    evaluated_at: "2026-01-15T16:15:05.000Z",
    current_price_source_time: "2026-01-15T16:15:04.000Z",
  },
  unavailable_reason: "replay_fixture",
};

describe("canonical evidence honesty", () => {
  const workerRow = (unavailable: string | null): CanonicalEvidenceRead => ({
    ...replayEvidence,
    source: {
      ...replayEvidence.source,
      is_live: unavailable === null,
      is_mock: false,
      source_family: "binance_usdm_futures_public",
      provider_name: "binance-usdm-perpetual",
    },
    current_price: {
      ...replayEvidence.current_price,
      usable_as_current_market_price: unavailable === null,
      presentation: unavailable === null ? "live_mark" : unavailable === "monitor_state_stale" ? "stale" : "unavailable",
      is_live: unavailable === null,
      is_mock: false,
    },
    setup_evidence: {
      ...replayEvidence.setup_evidence,
      available: false,
      reason: "setup_not_persisted",
    },
    unavailable_reason: unavailable,
  });

  it("grades a not-persisted setup as unknown, not poor", () => {
    expect(marketQualityFromCanonicalEvidence(workerRow(null)).grade).toBe("unknown");
    expect(marketQualityFromCanonicalEvidence(workerRow("monitor_state_missing")).grade).toBe("unknown");
  });

  it("never labels the source live when worker state is missing or stale", () => {
    const source = (u: string | null) =>
      marketQualityFromCanonicalEvidence(workerRow(u)).evidence.find((f) => f.label === "Source");
    expect(source(null)?.freshnessState).toBe("live");
    // Even if a server still claimed is_live, a missing/stale row is not live.
    const claimsLive = { ...workerRow("monitor_state_stale"), source: workerRow(null).source };
    const stale = marketQualityFromCanonicalEvidence(claimsLive).evidence.find((f) => f.label === "Source");
    expect(stale?.isLive).toBe(false);
    expect(stale?.stale).toBe(true);
    expect(stale?.freshnessState).toBe("stale");
    const missing = { ...workerRow("monitor_state_missing"), source: workerRow(null).source };
    const gone = marketQualityFromCanonicalEvidence(missing).evidence.find((f) => f.label === "Source");
    expect(gone?.isLive).toBe(false);
    expect(gone?.freshnessState).toBe("unavailable");
  });

  it("never treats replay fixture prices as live marks", () => {
    const view = marketQualityFromCanonicalEvidence(replayEvidence);
    expect(view.authority).toBe("canonical");
    expect(view.currentPrice?.usableAsCurrentMarketPrice).toBe(false);
    expect(view.currentPrice?.presentation).toBe("replay_fixture");
    expect(view.currentPrice?.freshnessState).toBe("replay");
    expect(view.currentPrice?.price).toBe("91234.5");
    expect(view.doesNotGrantEligibility).toBe(true);
    expect(view.grade).toBe("unknown");
    expect(canonicalPriceFreshnessState(replayEvidence.current_price)).toBe("replay");
  });

  it("labels a usable live mark only when the API says it is usable", () => {
    const live: CanonicalEvidenceRead = {
      ...replayEvidence,
      source: {
        ...replayEvidence.source,
        is_live: true,
        is_mock: false,
        source_family: "binance_usdm_futures_public",
        provider_name: "binance-usdm-perpetual",
      },
      current_price: {
        ...replayEvidence.current_price,
        usable_as_current_market_price: true,
        presentation: "live_mark",
        is_live: true,
        is_mock: false,
      },
      unavailable_reason: null,
    };
    const view = marketQualityFromCanonicalEvidence(live);
    expect(view.currentPrice?.usableAsCurrentMarketPrice).toBe(true);
    expect(view.currentPrice?.freshnessState).toBe("live");
    expect(view.grade).toBe("watch");
  });

  it("does not present compatibility snapshot prices as current market prices", () => {
    const analysis: MarketAnalyzeResponse = {
      snapshot: {
        meta: {
          symbol: "BTCUSDT",
          exchange: "binance",
          timeframe: "1h",
          timestamp: "2026-09-20T00:00:00.000Z",
          source: "mock",
          is_live: false,
          is_stale: false,
          provider_name: "mock-market-data",
          fallback_used: false,
          retrieved_at: "2026-09-20T00:00:00.000Z",
        },
        ticker: {
          meta: {
            symbol: "BTCUSDT",
            exchange: "binance",
            timestamp: "2026-09-20T00:00:00.000Z",
            source: "mock",
            is_live: false,
            is_stale: false,
            provider_name: "mock-market-data",
            fallback_used: false,
            retrieved_at: "2026-09-20T00:00:00.000Z",
          },
          last_price: "65000",
        },
      },
      indicators: {
        symbol: "BTCUSDT",
        timeframe: "1h",
        timestamp: "2026-09-20T00:00:00.000Z",
      },
      strategy_signals: [],
      data_quality: "ok",
      confidence_penalty_applied: false,
    };
    const view = marketQualityFromAnalysis(analysis, "BTCUSDT", "1h");
    expect(view.currentPrice).toBeNull();
    expect(view.authority).toBe("compatibility_projection");
    expect(view.evidence[0]?.freshnessState).toBe("unavailable");
    expect(view.evidence[0]?.isLive).toBe(false);
  });
});

describe("perpetual market monitor honesty", () => {
  it("never treats replay monitor prices as live marks", () => {
    const read: CanonicalMarketMonitorStatusRead = {
      authority: "canonical_market_monitor",
      live_executable: false,
      watcher_activated: false,
      compatibility_price_used: false,
      symbol: "BTCUSDT",
      mode: "replay",
      availability: "replay",
      reason: "replay_fixture",
      perpetual: true,
      source: replayEvidence.source,
      current_price: replayEvidence.current_price,
      last_update: "2026-01-15T16:15:04.000Z",
      evaluated_at: "2026-01-15T16:15:05.000Z",
      stream: {
        reconnect_state: "continuous",
        gap_state: "none",
        warm_up_status: "complete",
        reconnect_count: 0,
      },
      coverage: { completeness: "complete", gap_state: "none" },
      cvd: { available: true, event_count: 2 },
      ohlcv: { available: true, completeness_15m: "complete", completeness_4h: "complete" },
      provider: {
        name: "binance-usdm-perpetual-replay",
        health: "healthy",
        is_mock: true,
        using_fallback: false,
      },
      backoff: { active: false, attempt: 0 },
      content_hash: "ab".repeat(32),
    };
    const view = perpetualMarketStatusFromCanonical(read);
    expect(view.currentPrice.usableAsCurrentMarketPrice).toBe(false);
    expect(view.currentPrice.freshnessState).toBe("replay");
    expect(view.watcherActivated).toBe(false);
    expect(view.summary).toMatch(/not a current live perpetual mark/i);
  });
});
