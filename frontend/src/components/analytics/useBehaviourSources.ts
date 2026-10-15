"use client";

import { useCallback, useMemo } from "react";

import { usePrivateSource } from "./usePrivateSource";
import { api } from "@/lib/api";

import {
  buildAnalyticsWindowFilterKey,
  buildLearningWindowFilterKey,
  buildRuleComplianceFilterKey,
  type AnalyticsFilterParams,
} from "./filterValidation";

/**
 * Behaviour-tab loaders with independent source slots, keys, and retry actions.
 * A slow or failed source never delays already completed sibling widgets.
 */
export function useBehaviourSources(params: AnalyticsFilterParams, enabled: boolean) {
  const ruleComplianceKey = useMemo(
    () => buildRuleComplianceFilterKey(params.ruleComplianceJournal),
    [params.ruleComplianceJournal],
  );
  const analyticsWindowKey = useMemo(
    () => buildAnalyticsWindowFilterKey(params.analyticsWindow),
    [params.analyticsWindow],
  );
  const learningWindowKey = useMemo(
    () => buildLearningWindowFilterKey(params.learningWindow),
    [params.learningWindow],
  );

  const ruleComplianceSlot = usePrivateSource(
    enabled,
    "/journal/statistics",
    ruleComplianceKey,
    (signal) => api.journal.statistics(params.ruleComplianceJournal, { signal }),
  );
  const proposalDisciplineSlot = usePrivateSource(
    enabled,
    "/analytics/discipline",
    analyticsWindowKey,
    (signal) => api.analytics.discipline(params.analyticsWindow, { signal }),
  );
  const learningDisciplineSlot = usePrivateSource(
    enabled,
    "/learning-analytics/discipline",
    learningWindowKey,
    (signal) => api.learningAnalytics.discipline(params.learningWindow, { signal }),
  );
  const riskBehaviorSlot = usePrivateSource(
    enabled,
    "/analytics/risk-behavior",
    analyticsWindowKey,
    (signal) => api.analytics.riskBehavior(params.analyticsWindow, { signal }),
  );

  const reloadRuleCompliance = ruleComplianceSlot.reload;
  const reloadProposalDiscipline = proposalDisciplineSlot.reload;
  const reloadLearningDiscipline = learningDisciplineSlot.reload;
  const reloadRiskBehavior = riskBehaviorSlot.reload;

  const reload = useCallback(async () => {
    await Promise.all([
      reloadRuleCompliance(),
      reloadProposalDiscipline(),
      reloadLearningDiscipline(),
      reloadRiskBehavior(),
    ]);
  }, [
    reloadRuleCompliance,
    reloadProposalDiscipline,
    reloadLearningDiscipline,
    reloadRiskBehavior,
  ]);

  const loading =
    ruleComplianceSlot.loading ||
    proposalDisciplineSlot.loading ||
    learningDisciplineSlot.loading ||
    riskBehaviorSlot.loading;

  return {
    refreshFailures: [
      { name: "Rule compliance", error: ruleComplianceSlot.refreshError, retry: reloadRuleCompliance },
      { name: "Proposal discipline", error: proposalDisciplineSlot.refreshError, retry: reloadProposalDiscipline },
      { name: "Learning discipline", error: learningDisciplineSlot.refreshError, retry: reloadLearningDiscipline },
      { name: "Risk behavior", error: riskBehaviorSlot.refreshError, retry: reloadRiskBehavior },
    ],
    ruleCompliance: ruleComplianceSlot.source,
    ruleComplianceLoading: ruleComplianceSlot.loading,
    ruleComplianceRetryLoading: ruleComplianceSlot.retryLoading,
    proposalDiscipline: proposalDisciplineSlot.source,
    proposalDisciplineLoading: proposalDisciplineSlot.loading,
    proposalDisciplineRetryLoading: proposalDisciplineSlot.retryLoading,
    learningDiscipline: learningDisciplineSlot.source,
    learningDisciplineLoading: learningDisciplineSlot.loading,
    learningDisciplineRetryLoading: learningDisciplineSlot.retryLoading,
    riskBehavior: riskBehaviorSlot.source,
    riskBehaviorLoading: riskBehaviorSlot.loading,
    riskBehaviorRetryLoading: riskBehaviorSlot.retryLoading,
    loading,
    reload,
    reloadRuleCompliance,
    reloadProposalDiscipline,
    reloadLearningDiscipline,
    reloadRiskBehavior,
    ruleComplianceKey,
    analyticsWindowKey,
    learningWindowKey,
    ruleComplianceLoadedKey: ruleComplianceSlot.loadedKey,
    proposalDisciplineLoadedKey: proposalDisciplineSlot.loadedKey,
    learningDisciplineLoadedKey: learningDisciplineSlot.loadedKey,
    riskBehaviorLoadedKey: riskBehaviorSlot.loadedKey,
  };
}
