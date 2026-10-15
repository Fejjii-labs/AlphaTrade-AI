"use client";

import { useCallback, useMemo } from "react";

import { usePrivateSource } from "./usePrivateSource";
import { api } from "@/lib/api";
import type {
  LearningAnalyticsParams,
  LearningAnalyticsSummaryResponse,
  SetupPerformanceResponse,
  SetupRankingResponse,
  StrategyQualitySummaryResponse,
} from "@/lib/api/types";

import {
  buildStrategyQualityFilterKey,
  buildValidationFilterKey,
  type AnalyticsFilterParams,
} from "./filterValidation";

export function learningParamsFromValidation(
  params: AnalyticsFilterParams,
): LearningAnalyticsParams {
  return {
    start_date: params.validation.start_date,
    end_date: params.validation.end_date,
    min_sample: params.validation.min_sample,
  };
}

/**
 * Validation-tab loaders with independent source slots, keys, and retry actions.
 * A slow or failed source never delays already completed sibling widgets.
 */
export function useValidationSources(params: AnalyticsFilterParams, enabled: boolean) {
  const summaryParams = useMemo(() => learningParamsFromValidation(params), [params]);
  const setupParams = useMemo(
    () => ({
      ...learningParamsFromValidation(params),
      dimension: params.validation.dimension,
    }),
    [params],
  );
  const strategyParams = useMemo(() => params.strategyQuality, [params.strategyQuality]);

  const summaryKey = useMemo(
    () => buildValidationFilterKey(summaryParams),
    [summaryParams],
  );
  const setupPerformanceKey = useMemo(
    () => buildValidationFilterKey(setupParams),
    [setupParams],
  );
  const setupRankingKey = useMemo(
    () => buildValidationFilterKey(setupParams),
    [setupParams],
  );
  const strategyQualityKey = useMemo(
    () => buildStrategyQualityFilterKey(strategyParams),
    [strategyParams],
  );

  const summarySlot = usePrivateSource<LearningAnalyticsSummaryResponse>(
    enabled,
    "/learning-analytics/summary",
    summaryKey,
    (signal) => api.learningAnalytics.summary(summaryParams, { signal }),
  );
  const setupPerformanceSlot = usePrivateSource<SetupPerformanceResponse>(
    enabled,
    "/learning-analytics/setup-performance",
    setupPerformanceKey,
    (signal) => api.learningAnalytics.setupPerformance(setupParams, { signal }),
  );
  const setupRankingSlot = usePrivateSource<SetupRankingResponse>(
    enabled,
    "/learning-analytics/setup-ranking",
    setupRankingKey,
    (signal) => api.learningAnalytics.setupRanking(setupParams, { signal }),
  );
  const strategyQualitySlot = usePrivateSource<StrategyQualitySummaryResponse>(
    enabled,
    "/strategy-quality/summary",
    strategyQualityKey,
    (signal) => api.strategyQuality.summary(strategyParams, { signal }),
  );

  const reloadSummary = summarySlot.reload;
  const reloadSetupPerformance = setupPerformanceSlot.reload;
  const reloadSetupRanking = setupRankingSlot.reload;
  const reloadStrategyQuality = strategyQualitySlot.reload;

  const reload = useCallback(async () => {
    await Promise.all([
      reloadSummary(),
      reloadSetupPerformance(),
      reloadSetupRanking(),
      reloadStrategyQuality(),
    ]);
  }, [
    reloadSummary,
    reloadSetupPerformance,
    reloadSetupRanking,
    reloadStrategyQuality,
  ]);

  const loading =
    summarySlot.loading ||
    setupPerformanceSlot.loading ||
    setupRankingSlot.loading ||
    strategyQualitySlot.loading;

  return {
    summary: summarySlot.source,
    refreshFailures: [
      { name: "Validation summary", error: summarySlot.refreshError, retry: reloadSummary },
      { name: "Setup performance", error: setupPerformanceSlot.refreshError, retry: reloadSetupPerformance },
      { name: "Setup ranking", error: setupRankingSlot.refreshError, retry: reloadSetupRanking },
      { name: "Strategy quality", error: strategyQualitySlot.refreshError, retry: reloadStrategyQuality },
    ],
    summaryLoading: summarySlot.loading,
    summaryRetryLoading: summarySlot.retryLoading,
    setupPerformance: setupPerformanceSlot.source,
    setupPerformanceLoading: setupPerformanceSlot.loading,
    setupPerformanceRetryLoading: setupPerformanceSlot.retryLoading,
    setupRanking: setupRankingSlot.source,
    setupRankingLoading: setupRankingSlot.loading,
    setupRankingRetryLoading: setupRankingSlot.retryLoading,
    strategyQuality: strategyQualitySlot.source,
    strategyQualityLoading: strategyQualitySlot.loading,
    strategyQualityRetryLoading: strategyQualitySlot.retryLoading,
    loading,
    reload,
    reloadSummary,
    reloadSetupPerformance,
    reloadSetupRanking,
    reloadStrategyQuality,
    summaryKey,
    setupPerformanceKey,
    setupRankingKey,
    strategyQualityKey,
    summaryLoadedKey: summarySlot.loadedKey,
    setupPerformanceLoadedKey: setupPerformanceSlot.loadedKey,
    setupRankingLoadedKey: setupRankingSlot.loadedKey,
    strategyQualityLoadedKey: strategyQualitySlot.loadedKey,
  };
}
