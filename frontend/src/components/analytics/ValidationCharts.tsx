"use client";

import type { AnalyticsFilterParams } from "./filterValidation";
import {
  formatLearningAnalyticsFiltersSummary,
  formatStrategyQualityFiltersSummary,
  formatValidationFiltersSummary,
  type ValidationDimension,
} from "./filterValidation";
import { NO_SERVER_FRESHNESS_TIMESTAMP_NOTE } from "./sourceFreshness";
import { useValidationSources } from "./useValidationSources";
import { SetupSuccessByDimension, ValidationOutcomeChart } from "./AnalyticsCharts";
import { ValidationRankingTable } from "./ValidationRankingTable";
import { SourceRefreshFailures } from "./SourceRefreshFailures";

export type ValidationChartsProps = {
  apiParams: AnalyticsFilterParams;
  enabled?: boolean;
  onDimensionChange: (dimension: ValidationDimension) => void;
};

export function ValidationCharts({
  apiParams,
  enabled = true,
  onDimensionChange,
}: ValidationChartsProps) {
  const {
    refreshFailures = [],
    summary,
    summaryLoading,
    summaryRetryLoading,
    setupPerformance,
    setupPerformanceLoading,
    setupPerformanceRetryLoading,
    setupRanking,
    setupRankingLoading,
    setupRankingRetryLoading,
    strategyQuality,
    strategyQualityLoading,
    strategyQualityRetryLoading,
    reloadSummary,
    reloadSetupPerformance,
    reloadSetupRanking,
    reloadStrategyQuality,
  } = useValidationSources(apiParams, enabled);

  const summaryFilters = formatLearningAnalyticsFiltersSummary({
    start_date: apiParams.validation.start_date,
    end_date: apiParams.validation.end_date,
    min_sample: apiParams.validation.min_sample,
  });
  const setupFilters = formatValidationFiltersSummary(apiParams.validation);
  const strategyFilters = formatStrategyQualityFiltersSummary(apiParams.strategyQuality);

  if (!enabled) return null;

  return (
    <div className="space-y-6 max-w-full" data-testid="validation-charts">
      <SourceRefreshFailures sources={refreshFailures} />
      {summaryRetryLoading || setupPerformanceRetryLoading || setupRankingRetryLoading || strategyQualityRetryLoading ? <p role="status" className="text-caption text-text-muted">Refreshing requested sources…</p> : null}
      <p className="text-caption text-text-muted" data-testid="validation-freshness-limitation">
        {NO_SERVER_FRESHNESS_TIMESTAMP_NOTE}
      </p>
      <ValidationOutcomeChart
        source={summary}
        loading={summaryLoading}
        onRetry={() => void reloadSummary()}
        filtersSummary={summaryFilters}
      />
      <SetupSuccessByDimension
        source={setupPerformance}
        dimension={apiParams.validation.dimension}
        onDimensionChange={onDimensionChange}
        loading={setupPerformanceLoading}
        onRetry={() => void reloadSetupPerformance()}
        filtersSummary={setupFilters}
      />
      <ValidationRankingTable
        rankingSource={setupRanking}
        strategyQualitySource={strategyQuality}
        rankingLoading={setupRankingLoading}
        strategyQualityLoading={strategyQualityLoading}
        onRetryRanking={() => void reloadSetupRanking()}
        onRetryStrategyQuality={() => void reloadStrategyQuality()}
        rankingFiltersSummary={setupFilters}
        strategyQualityFiltersSummary={strategyFilters}
      />
    </div>
  );
}
