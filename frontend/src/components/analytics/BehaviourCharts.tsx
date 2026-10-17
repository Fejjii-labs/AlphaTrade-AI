"use client";
import { SourceRefreshFailures } from "./SourceRefreshFailures";

import { useMemo } from "react";

import type { AnalyticsFilterParams } from "./filterValidation";
import {
  formatAnalyticsWindowFiltersSummary,
  formatJournalStatsFiltersSummary,
  formatLearningAnalyticsFiltersSummary,
} from "./filterValidation";
import { DisciplineScoreCards } from "./DisciplineScoreCards";
import { RiskBehaviourCounters } from "./RiskBehaviourCounters";
import { RuleComplianceChart } from "./AnalyticsCharts";
import {
  gateSourceByFreshness,
  journalFreshnessTimestamp,
  journalSourceStale,
  NO_SERVER_FRESHNESS_TIMESTAMP_NOTE,
} from "./sourceFreshness";
import { useBehaviourSources } from "./useBehaviourSources";

export type BehaviourChartsProps = {
  apiParams: AnalyticsFilterParams;
  enabled?: boolean;
};

export function BehaviourCharts({ apiParams, enabled = true }: BehaviourChartsProps) {
  const {
    refreshFailures = [],
    ruleCompliance,
    ruleComplianceLoading,
    ruleComplianceRetryLoading,
    proposalDiscipline,
    proposalDisciplineLoading,
    proposalDisciplineRetryLoading,
    learningDiscipline,
    learningDisciplineLoading,
    learningDisciplineRetryLoading,
    riskBehavior,
    riskBehaviorLoading,
    riskBehaviorRetryLoading,
    reloadRuleCompliance,
    reloadProposalDiscipline,
    reloadLearningDiscipline,
    reloadRiskBehavior,
  } = useBehaviourSources(apiParams, enabled);

  const ruleComplianceSummary = formatJournalStatsFiltersSummary(
    apiParams.ruleComplianceJournal,
  );
  const analyticsWindowSummary = formatAnalyticsWindowFiltersSummary(
    apiParams.analyticsWindow,
  );
  const learningWindowSummary = formatLearningAnalyticsFiltersSummary(
    apiParams.learningWindow,
  );

  const gatedRuleCompliance = useMemo(
    () => gateSourceByFreshness(ruleCompliance, journalFreshnessTimestamp(ruleCompliance)),
    [ruleCompliance],
  );

  const ruleComplianceStale = useMemo(
    () => journalSourceStale(ruleCompliance),
    [ruleCompliance],
  );

  if (!enabled) return null;

  return (
    <div className="space-y-6" data-testid="behaviour-charts">
      <SourceRefreshFailures sources={refreshFailures} />
      {ruleComplianceRetryLoading || proposalDisciplineRetryLoading || learningDisciplineRetryLoading || riskBehaviorRetryLoading ? <p role="status" className="text-caption text-text-muted">Refreshing requested sources…</p> : null}
      <RuleComplianceChart
        source={gatedRuleCompliance}
        loading={ruleComplianceLoading}
        onRetry={() => void reloadRuleCompliance()}
        filtersSummary={ruleComplianceSummary}
        staleWholeTab={ruleComplianceStale}
      />
      <DisciplineScoreCards
        proposalSource={proposalDiscipline}
        learningSource={learningDiscipline}
        proposalLoading={proposalDisciplineLoading}
        learningLoading={learningDisciplineLoading}
        onRetryProposal={() => void reloadProposalDiscipline()}
        onRetryLearning={() => void reloadLearningDiscipline()}
        proposalFiltersSummary={analyticsWindowSummary}
        learningFiltersSummary={learningWindowSummary}
        proposalFreshnessNote={NO_SERVER_FRESHNESS_TIMESTAMP_NOTE}
        learningFreshnessNote={NO_SERVER_FRESHNESS_TIMESTAMP_NOTE}
      />
      <RiskBehaviourCounters
        source={riskBehavior}
        loading={riskBehaviorLoading}
        onRetry={() => void reloadRiskBehavior()}
        filtersSummary={analyticsWindowSummary}
        freshnessNote={NO_SERVER_FRESHNESS_TIMESTAMP_NOTE}
      />
    </div>
  );
}
