import type {
  ApprovalRequest,
  CanonicalCandidateRead,
  CanonicalCurrentPriceRead,
  CanonicalEligibilityRead,
  CanonicalEvidenceRead,
  CanonicalMarketMonitorStatusRead,
  CanonicalLearningRecordRead,
  CanonicalSetupAssessmentRead,
  JournalEntry,
  LessonCandidate,
  MarketAnalyzeResponse,
  PaperOrder,
  PaperValidationCandidateItem,
  TradeProposal,
} from "@/lib/api/types";
import { projectActionEligibility, projectCanonicalEligibility } from "@/lib/canonical-decision/eligibility";
import {
  candidateSetupLabel,
  executionFromOrder,
  tradePlanFromProposal,
} from "@/lib/canonical-decision/trade-plan";
import type {
  CanonicalFreshnessPillState,
  CandidateLifecycleState,
  CandidateWorkspaceView,
  CurrentPriceHonesty,
  DecisionCase,
  DecisionQueueSnapshot,
  DecisionStage,
  EvidenceFact,
  LearningView,
  MarketQualityGrade,
  MarketQualityView,
  OutcomeView,
  PerpetualMarketStatusView,
  PaperApprovalView,
  SetupAssessmentState,
} from "@/lib/canonical-decision/types";
import { DECISION_STAGES } from "@/lib/canonical-decision/types";
import { missingBindings } from "@/lib/canonical-decision/bindings";
import { canExecutePaperOrder } from "@/lib/workflow";

export type DecisionComposeInput = {
  canonicalCandidates?: CanonicalCandidateRead[];
  candidates: PaperValidationCandidateItem[];
  proposals: TradeProposal[];
  approvals: ApprovalRequest[];
  orders: PaperOrder[];
  journals: JournalEntry[];
  lessons: LessonCandidate[];
  killSwitchActive: boolean;
  executionMode: string | null;
  realTradingEnabled: boolean | null;
};

function emptyStageCounts(): Record<DecisionStage, number> {
  return Object.fromEntries(DECISION_STAGES.map((stage) => [stage, 0])) as Record<
    DecisionStage,
    number
  >;
}

export function canonicalPriceFreshnessState(
  price: CanonicalCurrentPriceRead,
): CanonicalFreshnessPillState {
  if (price.fallback_used) return "fallback";
  if (price.presentation === "replay_fixture" || price.is_mock) return "replay";
  if (!price.usable_as_current_market_price) {
    if (price.presentation === "stale" || price.freshness.state === "stale") return "stale";
    if (price.presentation === "degraded") return "degraded";
    return "unavailable";
  }
  if (price.freshness.state === "aging") return "delayed";
  if (price.freshness.state === "fresh" || price.presentation === "live_mark") return "live";
  return "unavailable";
}

export function currentPriceHonestyFromCanonical(
  price: CanonicalCurrentPriceRead,
): CurrentPriceHonesty {
  const replayPrice = price.presentation === "replay_fixture";
  return {
    usableAsCurrentMarketPrice: price.usable_as_current_market_price,
    presentation: price.presentation,
    price:
      price.usable_as_current_market_price || replayPrice ? (price.price ?? null) : null,
    sourceTime: price.source_time ?? null,
    venueTradeId: price.venue_trade_id ?? null,
    isLive: price.is_live,
    isMock: price.is_mock,
    fallbackUsed: false,
    freshnessState: canonicalPriceFreshnessState(price),
    freshnessPolicyVersion: price.freshness.policy_version,
    ageSeconds: price.freshness.age_seconds ?? null,
  };
}

export function availabilityFreshnessState(
  availability: string,
): CanonicalFreshnessPillState {
  if (availability === "fresh") return "live";
  if (availability === "stale") return "stale";
  if (availability === "degraded") return "degraded";
  if (availability === "replay") return "replay";
  return "unavailable";
}

export function perpetualMarketStatusFromCanonical(
  read: CanonicalMarketMonitorStatusRead,
): PerpetualMarketStatusView {
  const price = currentPriceHonestyFromCanonical(read.current_price);
  const replay = read.mode === "replay" || read.availability === "replay" || read.source.is_mock;
  return {
    symbol: read.symbol,
    mode: read.mode,
    availability: read.availability,
    reason: read.reason,
    lastUpdate: read.last_update,
    sourceLabel: `${read.source.venue} USD-M ${read.source.market_type} · ${read.source.provider_symbol}`,
    providerName: read.provider.name,
    providerHealth: read.provider.health,
    perpetual: true,
    watcherActivated: false,
    currentPrice: price,
    streamLabel: `${read.stream.reconnect_state} · gap ${read.stream.gap_state} · warm-up ${read.stream.warm_up_status}`,
    ohlcvLabel: `15m ${read.ohlcv.completeness_15m} · 4h ${read.ohlcv.completeness_4h}`,
    cvdLabel: read.cvd.available
      ? `complete · Δ ${read.cvd.signed_quote_delta ?? "—"}`
      : `unavailable · ${read.cvd.reason ?? read.reason}`,
    summary: replay
      ? "Replay fixture stream. This price is not a current live perpetual mark."
      : read.availability === "fresh" && price.usableAsCurrentMarketPrice
        ? "Live Binance USD-M perpetual mark from contracted trades. Watcher stays off."
        : "Perpetual monitor failed closed or is degraded. Replay, demo-seed, and compatibility prices are not used.",
    activationLabel: read.activation
      ? `${read.activation.state} · ${read.activation.configured_source} · ${read.activation.trade_freshness_seconds}s freshness · read-only`
      : undefined,
  };
}

export function marketQualityFromCanonicalEvidence(
  read: CanonicalEvidenceRead,
): MarketQualityView {
  const price = currentPriceHonestyFromCanonical(read.current_price);
  const setup = read.setup_evidence;
  const completeness = setup.completeness;
  const complete =
    setup.available &&
    completeness.ohlcv_15m === "complete" &&
    completeness.ohlcv_4h === "complete" &&
    completeness.cvd === "complete" &&
    completeness.signed_flow === "complete";
  const replay = read.source.is_mock || read.current_price.presentation === "replay_fixture";
  // Deployed APIs serve the worker row; the setup window is not persisted there.
  // That is "not assessed", not a poor market.
  const setupNotPersisted = !setup.available && setup.reason === "setup_not_persisted";
  const sourceNotCurrent =
    read.unavailable_reason === "monitor_state_missing" ||
    read.unavailable_reason === "monitor_state_stale";
  const sourceStale = read.unavailable_reason === "monitor_state_stale";
  const grade: MarketQualityGrade = setupNotPersisted
    ? "unknown"
    : !setup.available || !complete
    ? "poor"
    : replay
      ? "unknown"
      : "watch";
  const setupState: SetupAssessmentState = grade === "watch" ? "watch" : "unknown";
  const evidence: EvidenceFact[] = [
    {
      label: "Source",
      detail: `${read.source.venue} · ${read.source.market_type} · ${read.source.provider_symbol} · ${read.source.source_family}`,
      isLive: read.source.is_live && !sourceNotCurrent,
      fallbackUsed: read.source.fallback_used,
      stale: sourceStale,
      freshnessState: replay
        ? "replay"
        : sourceStale
          ? "stale"
          : read.source.is_live && !sourceNotCurrent
            ? "live"
            : "unavailable",
    },
    {
      label: "Current price",
      detail: price.usableAsCurrentMarketPrice
        ? `${price.price ?? "—"} · ${price.presentation}`
        : `${price.presentation} — not a current market price`,
      freshness: price.sourceTime,
      isLive: price.usableAsCurrentMarketPrice && price.isLive,
      fallbackUsed: false,
      stale: price.freshnessState === "stale",
      freshnessState: price.freshnessState,
    },
    {
      label: "OHLCV",
      detail: `15m ${completeness.ohlcv_15m} · 4h ${completeness.ohlcv_4h}`,
    },
    {
      label: "CVD",
      detail: setup.available
        ? `${completeness.cvd} · Δ ${setup.cvd_signed_quote_delta ?? "—"}`
        : completeness.cvd,
    },
    {
      label: "Aggressive signed flow",
      detail: setup.available
        ? `${completeness.signed_flow} · ratio ${setup.signed_flow_ratio ?? "—"}`
        : completeness.signed_flow,
    },
  ];
  if (setup.evidence_window_hash) {
    evidence.push({
      label: "Evidence window",
      detail: setup.evidence_window_hash,
    });
  }
  if (read.unavailable_reason) {
    evidence.push({
      label: "Unavailable",
      detail: read.unavailable_reason,
      freshnessState: price.freshnessState === "stale" ? "stale" : "unavailable",
    });
  }
  return {
    authority: "canonical",
    grade,
    setupState,
    dataQuality: complete ? "complete" : completeness.cvd,
    confidence: null,
    confidencePenaltyApplied: false,
    symbol: read.symbol,
    timeframe: "15m",
    direction: null,
    evidence,
    summary: replay
      ? "Replay fixture evidence. Prices here are not live market marks and do not grant eligibility."
      : setup.available
        ? "Canonical USD-M evidence. Completeness and freshness are independent of action eligibility."
        : "Canonical evidence failed closed. Missing, stale, or incomplete perpetual data is not replaced.",
    currentPrice: price,
    doesNotGrantEligibility: true,
  };
}

export function marketQualityFromAnalysis(
  analysis: MarketAnalyzeResponse,
  symbol: string,
  timeframe: string,
): MarketQualityView {
  const meta = analysis.snapshot.meta;
  const best = analysis.strategy_signals[0];
  const snapshotFreshness: CanonicalFreshnessPillState = meta.is_stale
    ? "stale"
    : meta.fallback_used
      ? "fallback"
      : meta.is_live
        ? "live"
        : "unavailable";
  const evidence: EvidenceFact[] = [
    {
      label: "Market data",
      detail: `${meta.source} · ${meta.provider_name} (compatibility snapshot, not a canonical current price)`,
      freshness: meta.retrieved_at,
      isLive: meta.is_live,
      fallbackUsed: meta.fallback_used,
      stale: meta.is_stale,
      freshnessState: snapshotFreshness,
    },
    ...analysis.strategy_signals.flatMap((signal) =>
      signal.evidence.map((item) => ({
        label: signal.strategy_id,
        detail: item,
      })),
    ),
  ];
  const grade: MarketQualityGrade = meta.is_stale || meta.fallback_used
    ? "poor"
    : analysis.data_quality.toLowerCase().includes("poor")
      ? "poor"
      : best && (best.confidence ?? 0) >= 0.65
        ? "tradeable"
        : best
          ? "watch"
          : "unknown";
  const setupState: SetupAssessmentState =
    grade === "tradeable" ? "confirmed_setup" : grade === "watch" ? "watch" : "unknown";
  return {
    authority: "compatibility_projection",
    grade,
    setupState,
    dataQuality: analysis.data_quality,
    confidence: best?.confidence ?? null,
    confidencePenaltyApplied: analysis.confidence_penalty_applied,
    symbol,
    timeframe,
    direction: best?.direction ?? null,
    evidence,
    summary:
      grade === "poor"
        ? "Compatibility market snapshot is degraded. It is not a canonical current price and does not decide eligibility."
        : "Compatibility market snapshot only. Canonical current price lives on GET /canonical/evidence.",
    currentPrice: null,
    doesNotGrantEligibility: true,
  };
}

export function marketQualityFromCandidate(candidate: PaperValidationCandidateItem): MarketQualityView {
  const evidence: EvidenceFact[] = [];
  if (candidate.thesis) evidence.push({ label: "Thesis", detail: candidate.thesis });
  if (candidate.entry_criteria) evidence.push({ label: "Entry", detail: candidate.entry_criteria });
  if (candidate.invalidation_criteria) {
    evidence.push({ label: "Invalidation", detail: candidate.invalidation_criteria });
  }
  if (candidate.evidence_tier) {
    evidence.push({ label: "Evidence tier", detail: candidate.evidence_tier });
  }
  const grade: MarketQualityGrade =
    (candidate.confidence ?? 0) >= 0.65 ? "tradeable" : candidate.confidence != null ? "watch" : "unknown";
  return {
    authority: "compatibility_projection",
    grade,
    setupState: candidate.candidate_status === "archived" ? "expired" : "unknown",
    dataQuality: candidate.evidence_tier ?? null,
    confidence: candidate.confidence ?? null,
    confidencePenaltyApplied: false,
    symbol: candidate.symbol ?? null,
    timeframe: candidate.timeframe ?? null,
    direction: candidate.direction ?? null,
    evidence,
    summary:
      "Compatibility projection from the paper-validation queue. Not canonical SetupAssessment.",
    currentPrice: null,
    doesNotGrantEligibility: true,
  };
}

function lifecycleFromCandidate(candidate: PaperValidationCandidateItem): CandidateLifecycleState {
  if (candidate.candidate_status === "archived") return "skipped";
  if (candidate.candidate_status === "reviewing") return "active";
  return "active";
}

function approvalView(approval: ApprovalRequest | undefined): PaperApprovalView | null {
  if (!approval) return null;
  return {
    approvalId: approval.id,
    proposalId: approval.proposal_id,
    status: approval.status,
    createdAt: approval.created_at,
    decidedAt: approval.decided_at ?? null,
    reason: approval.approval_reason ?? null,
    humanApprovalRequired: true,
    aiCannotBypass: true,
    recordsAuthorizationOnly: true,
    doesNotPlaceOrder: true,
  };
}

function outcomeFromJournal(entry: JournalEntry | undefined): OutcomeView | null {
  if (!entry) return null;
  return {
    journalId: entry.id,
    positionId: entry.linked_position_id ?? null,
    proposalId: entry.linked_proposal_id ?? null,
    symbol: entry.symbol,
    result: entry.result,
    pnl: entry.pnl ?? null,
    lessons: entry.lessons ?? null,
    href: `/decision/outcomes/${entry.id}`,
  };
}

function learningFromLesson(lesson: LessonCandidate | undefined): LearningView | null {
  if (!lesson) return null;
  return {
    lessonId: lesson.id,
    status: lesson.status,
    lessonText: lesson.lesson_text,
    mistakeType: lesson.mistake_type,
    href: `/lessons?id=${lesson.id}`,
  };
}

export function deriveProposalStage(input: {
  proposal: TradeProposal;
  approval: ApprovalRequest | undefined;
  order: PaperOrder | undefined;
  journal: JournalEntry | undefined;
  lesson: LessonCandidate | undefined;
}): DecisionStage {
  if (input.lesson) return "learning";
  if (input.journal) return "outcome";
  if (input.order) return "paper_execution";
  if (input.approval) {
    if (input.approval.status === "approved") return "paper_execution";
    return "approval";
  }
  if (input.proposal.status === "approved") return "paper_execution";
  if (input.proposal.status === "pending_approval" || input.proposal.approval_required) {
    return "approval";
  }
  return "trade_plan";
}

function candidateWorkspaceFromPvc(
  candidate: PaperValidationCandidateItem,
  facts: Pick<DecisionComposeInput, "killSwitchActive" | "executionMode" | "realTradingEnabled">,
): CandidateWorkspaceView {
  const marketQuality = marketQualityFromCandidate(candidate);
  const actionable = candidate.candidate_status !== "archived";
  return {
    authority: "compatibility_projection",
    caseId: `pvc:${candidate.candidate_id}`,
    sourceId: candidate.candidate_id,
    sourceKind: "compatibility_candidate",
    lifecycleState: lifecycleFromCandidate(candidate),
    symbol: candidate.symbol ?? null,
    timeframe: candidate.timeframe ?? null,
    direction: candidate.direction ?? null,
    setupLabel: candidateSetupLabel(candidate),
    thesis: candidate.thesis ?? null,
    entryCriteria: candidate.entry_criteria ?? null,
    invalidation: candidate.invalidation_criteria ?? candidate.invalidation_level?.toString() ?? null,
    confidence: candidate.confidence ?? null,
    evidence: marketQuality.evidence,
    strategyId: candidate.strategy_id ?? null,
    createdAt: candidate.created_at,
    legacyHref: `/paper-validation/candidates/${candidate.candidate_id}`,
    marketQuality,
    eligibility: projectActionEligibility({
      killSwitchActive: facts.killSwitchActive,
      executionMode: facts.executionMode,
      realTradingEnabled: facts.realTradingEnabled,
      candidateActionable: actionable,
      setupConfirmed: marketQuality.grade === "tradeable",
    }),
  };
}

function lifecycleFromCanonical(state: string): CandidateLifecycleState {
  switch (state) {
    case "active":
    case "plan_created":
    case "rejected":
    case "skipped":
    case "expired":
    case "invalidated":
      return state;
    default:
      return "unknown";
  }
}

export function marketQualityFromSetupAssessment(
  assessment: CanonicalSetupAssessmentRead,
  candidate: CanonicalCandidateRead["candidate"],
): MarketQualityView {
  const setupState = assessment.assessment_state as SetupAssessmentState;
  const grade: MarketQualityGrade =
    setupState === "confirmed_setup"
      ? "tradeable"
      : setupState === "watch" || setupState === "partial_match"
        ? "watch"
        : setupState === "no_setup" || setupState === "unknown"
          ? "unknown"
          : "poor";
  return {
    authority: "canonical",
    grade,
    setupState,
    dataQuality: assessment.evidence_window_hash,
    confidence: candidate.confidence ?? null,
    confidencePenaltyApplied: false,
    symbol: candidate.evidence_instrument ?? null,
    timeframe: candidate.timeframe,
    direction: candidate.direction,
    evidence: [
      { label: "Setup assessment", detail: `${assessment.assessment_id} · ${setupState}` },
      { label: "Evidence window", detail: assessment.evidence_window_hash },
    ],
    summary:
      "Canonical SetupAssessment lineage. Market/setup truth does not grant action eligibility.",
    currentPrice: null,
    doesNotGrantEligibility: true,
  };
}

export function workspaceFromCanonicalCandidate(
  read: CanonicalCandidateRead,
  facts: Pick<DecisionComposeInput, "killSwitchActive" | "executionMode" | "realTradingEnabled">,
  extras?: {
    eligibility?: CanonicalEligibilityRead | null;
    assessment?: CanonicalSetupAssessmentRead | null;
  },
): CandidateWorkspaceView {
  const candidate = read.candidate;
  const marketQuality = extras?.assessment
    ? marketQualityFromSetupAssessment(extras.assessment, candidate)
    : {
        authority: "canonical" as const,
        grade: "unknown" as MarketQualityGrade,
        setupState: "unknown" as SetupAssessmentState,
        dataQuality: candidate.evidence_window_hash,
        confidence: candidate.confidence ?? null,
        confidencePenaltyApplied: false,
        symbol: candidate.evidence_instrument ?? null,
        timeframe: candidate.timeframe,
        direction: candidate.direction,
        evidence: [{ label: "Evidence window", detail: candidate.evidence_window_hash }],
        summary:
          "Canonical Candidate. SetupAssessment was not loaded on this queue row; market truth still does not grant eligibility.",
        currentPrice: null,
        doesNotGrantEligibility: true as const,
      };
  const eligibility = extras?.eligibility
    ? projectCanonicalEligibility(extras.eligibility, facts)
    : projectActionEligibility({
        killSwitchActive: facts.killSwitchActive,
        executionMode: facts.executionMode,
        realTradingEnabled: facts.realTradingEnabled,
        candidateActionable: candidate.state === "active" || candidate.state === "plan_created",
        setupConfirmed: extras?.assessment?.assessment_state === "confirmed_setup",
      });
  return {
    authority: "canonical",
    caseId: `canonical:${candidate.candidate_id}`,
    sourceId: candidate.candidate_id,
    sourceKind: "canonical_candidate",
    lifecycleState: lifecycleFromCanonical(candidate.state),
    symbol: candidate.evidence_instrument ?? null,
    timeframe: candidate.timeframe,
    direction: candidate.direction,
    setupLabel: candidate.setup_definition_id ?? candidate.strategy_version_id,
    thesis: null,
    entryCriteria: null,
    invalidation: candidate.valid_until ? `Valid until ${candidate.valid_until}` : null,
    confidence: candidate.confidence ?? null,
    evidence: marketQuality.evidence,
    strategyId: candidate.strategy_version_id,
    createdAt: candidate.created_at ?? null,
    legacyHref: null,
    marketQuality,
    eligibility,
  };
}

export function learningFromCanonicalRecord(read: CanonicalLearningRecordRead): LearningView {
  const outcome = read.record.facts?.outcome;
  return {
    lessonId: read.record.attribution_id,
    status: outcome?.status ?? "canonical_record",
    lessonText:
      read.record.narrative_explanation ??
      "Canonical learning attribution (record-only). LLM narrative is not a fact.",
    mistakeType: outcome?.result ?? null,
    href: `/decision/candidates/${read.record.candidate_id}`,
  };
}

export function outcomeFromCanonicalRecord(read: CanonicalLearningRecordRead): OutcomeView {
  const outcome = read.record.facts?.outcome;
  return {
    journalId: read.record.journal_trade_id ?? null,
    positionId: null,
    proposalId: null,
    symbol: null,
    result: outcome?.result ?? null,
    pnl: outcome?.net_pnl ?? null,
    lessons: read.record.narrative_explanation ?? null,
    href: `/decision/candidates/${read.record.candidate_id}`,
  };
}

export function composeDecisionCases(input: DecisionComposeInput): DecisionQueueSnapshot {
  const approvalsByProposal = new Map(input.approvals.map((item) => [item.proposal_id, item]));
  const ordersByProposal = new Map(
    input.orders
      .filter((order) => order.proposal_id)
      .map((order) => [order.proposal_id as string, order]),
  );
  const journalsByProposal = new Map(
    input.journals
      .filter((entry) => entry.linked_proposal_id)
      .map((entry) => [entry.linked_proposal_id as string, entry]),
  );
  const lessonsByJournal = new Map(
    input.lessons
      .filter((lesson) => lesson.related_journal_entry_id)
      .map((lesson) => [lesson.related_journal_entry_id as string, lesson]),
  );

  const cases: DecisionCase[] = [];

  for (const read of input.canonicalCandidates ?? []) {
    const workspace = workspaceFromCanonicalCandidate(read, input);
    const stage: DecisionStage =
      workspace.eligibility.state === "blocked" ? "eligibility" : "candidate";
    cases.push({
      id: workspace.caseId,
      kind: "canonical_candidate",
      stage,
      symbol: workspace.symbol ?? "Candidate",
      direction: workspace.direction,
      timeframe: workspace.timeframe,
      title: `${workspace.symbol ?? "Candidate"} · ${workspace.setupLabel ?? "canonical"}`,
      summary: `${workspace.lifecycleState.replaceAll("_", " ")} · ${workspace.eligibility.state}`,
      href: `/decision/candidates/${read.candidate.candidate_id}`,
      createdAt: workspace.createdAt,
      candidate: workspace,
      tradePlan: null,
      approval: null,
      execution: null,
      outcome: null,
      learning: null,
    });
  }

  for (const proposal of input.proposals) {
    const approval = approvalsByProposal.get(proposal.id);
    const order = ordersByProposal.get(proposal.id);
    const journal = journalsByProposal.get(proposal.id);
    const lesson = journal ? lessonsByJournal.get(journal.id) : undefined;
    const stage = deriveProposalStage({ proposal, approval, order, journal, lesson });
    const paperGate = canExecutePaperOrder(proposal, approval);
    const tradePlan = tradePlanFromProposal(proposal);
    const eligibility = projectActionEligibility({
      killSwitchActive: input.killSwitchActive,
      executionMode: input.executionMode,
      realTradingEnabled: input.realTradingEnabled,
      proposal,
      approval,
      setupConfirmed: true,
    });
    const candidate: CandidateWorkspaceView = {
      authority: "compatibility_projection",
      caseId: `proposal:${proposal.id}`,
      sourceId: proposal.id,
      sourceKind: "legacy_proposal",
      lifecycleState: stage === "trade_plan" || stage === "approval" ? "plan_created" : "plan_created",
      symbol: proposal.symbol,
      timeframe: proposal.timeframe,
      direction: proposal.direction,
      setupLabel: proposal.strategy_id,
      thesis: proposal.rationale,
      entryCriteria: `Entry ${proposal.entry_price}`,
      invalidation: proposal.exit.invalidation,
      confidence: proposal.confidence,
      evidence: [{ label: "Rationale", detail: proposal.rationale }],
      strategyId: proposal.strategy_id,
      createdAt: proposal.created_at,
      legacyHref: `/proposals?id=${proposal.id}`,
      marketQuality: {
        authority: "compatibility_projection",
        grade: proposal.confidence >= 0.65 ? "tradeable" : "watch",
        setupState: "unknown",
        dataQuality: null,
        confidence: proposal.confidence,
        confidencePenaltyApplied: false,
        symbol: proposal.symbol,
        timeframe: proposal.timeframe,
        direction: proposal.direction,
        evidence: [{ label: "Proposal rationale", detail: proposal.rationale }],
        summary: "Proposal-derived market notes. Not canonical SetupAssessment.",
        currentPrice: null,
        doesNotGrantEligibility: true,
      },
      eligibility,
    };
    cases.push({
      id: `proposal:${proposal.id}`,
      kind: "legacy_proposal",
      stage,
      symbol: proposal.symbol,
      direction: proposal.direction,
      timeframe: proposal.timeframe,
      title: `${proposal.symbol} ${proposal.direction}`,
      summary: `${stage.replaceAll("_", " ")} · ${eligibility.state}`,
      href:
        stage === "outcome" && journal
          ? `/decision/outcomes/${journal.id}`
          : stage === "paper_execution" && order
            ? `/decision/executions/${order.id}`
            : approval
              ? `/decision/approvals/${approval.id}`
              : `/decision/plans/${proposal.id}`,
      createdAt: proposal.created_at,
      candidate,
      tradePlan,
      approval: approvalView(approval),
      execution: executionFromOrder(
        order ?? null,
        proposal,
        approval ?? null,
        paperGate.allowed ? null : paperGate.reason ?? null,
      ),
      outcome: outcomeFromJournal(journal),
      learning: learningFromLesson(lesson),
    });
  }

  for (const candidate of input.candidates) {
    const workspace = candidateWorkspaceFromPvc(candidate, input);
    const stage: DecisionStage =
      workspace.eligibility.state === "blocked" ? "eligibility" : "candidate";
    cases.push({
      id: workspace.caseId,
      kind: "compatibility_candidate",
      stage,
      symbol: candidate.symbol ?? "Unknown",
      direction: candidate.direction ?? null,
      timeframe: candidate.timeframe ?? null,
      title: `${candidate.symbol ?? "Candidate"} · ${candidateSetupLabel(candidate)}`,
      summary: `${workspace.lifecycleState.replaceAll("_", " ")} · ${workspace.eligibility.state}`,
      href: `/decision/candidates/${candidate.candidate_id}`,
      createdAt: candidate.created_at,
      candidate: workspace,
      tradePlan: null,
      approval: null,
      execution: null,
      outcome: null,
      learning: null,
    });
  }

  cases.sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""));
  const stageCounts = emptyStageCounts();
  for (const item of cases) {
    stageCounts[item.stage] += 1;
  }
  return {
    cases,
    stageCounts,
    missingBindings: missingBindings().map((item) => item.id),
  };
}
