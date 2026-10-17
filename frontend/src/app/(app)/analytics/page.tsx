"use client";

import { useValidationPrefetch } from "@/components/analytics/useValidationPrefetch";
import { useEffect, useMemo } from "react";

import {
  AnalyticsFilterBar,
  BehaviourCharts,
  ComparisonCharts,
  OverviewStats,
  PerformanceCharts,
  SetupsCharts,
  ValidationCharts,
  formatAppliedFiltersSummary,
  formatSetupEvidenceFiltersSummary,
  formatSetupEvidenceLimitationNote,
  gateSourceByFreshness,
  journalFreshnessTimestamp,
  portfolioFreshnessTimestamp,
  tabSourcesStale,
  useAnalyticsFilters,
  useAnalyticsSources,
  useSetupAnalyticsSources,
} from "@/components/analytics";
import { PageHeader } from "@/components/ui/page-header";
import { VerifiedPaperModeIndicator } from "@/components/ui/paper-mode-indicator";
import { TabPanel, Tabs, TabsRoot } from "@/components/ui/tabs";
import { ErrorState, LoadingState, StaleState } from "@/components/states";
import { WorkflowFreshnessAdapter } from "@/components/workflows";

/**
 * Section heading for a tab panel, so tab content starts at `h2` instead of
 * jumping from the page `h1` straight to chart `h3`s (FP2-217). The Overview
 * panel's heading comes from `OverviewStats`.
 */
function TabHeading({ children }: { children: string }) {
  return <h2 className="text-lg font-medium text-text-primary">{children}</h2>;
}

const TAB_ITEMS = [
  { id: "overview", label: "Overview" },
  { id: "performance", label: "Performance" },
  { id: "setups", label: "Setups" },
  { id: "behaviour", label: "Behaviour" },
  { id: "validation", label: "Validation" },
  { id: "comparison", label: "Comparison" },
];

export default function AnalyticsPage() {
  const {
    state,
    apiParams,
    setupApiParams,
    setTab,
    applyDraft,
    applyDatePreset,
    setGroupBy,
    setBucketOffset,
    setDimension,
    clearFilters,
    cleanupIgnoredParams,
  } = useAnalyticsFilters();

  const prefetchValidation = useValidationPrefetch(apiParams);
  const sharedEnabled = state.tab === "overview" || state.tab === "performance";
  const shared = useAnalyticsSources(apiParams, { enabled: sharedEnabled });
  const setups = useSetupAnalyticsSources(setupApiParams, { enabled: state.tab === "setups" });

  useEffect(() => {
    cleanupIgnoredParams();
  }, [cleanupIgnoredParams]);

  const filtersSummary = formatAppliedFiltersSummary(state);
  const evidenceFiltersSummary = formatSetupEvidenceFiltersSummary(state);
  const evidenceLimitationNote = formatSetupEvidenceLimitationNote(state);

  const gatedJournal = useMemo(
    () => gateSourceByFreshness(shared.journal, journalFreshnessTimestamp(shared.journal)),
    [shared.journal],
  );

  const gatedPortfolio = useMemo(
    () => gateSourceByFreshness(shared.portfolio, portfolioFreshnessTimestamp(shared.portfolio)),
    [shared.portfolio],
  );

  const gatedSetupJournal = useMemo(
    () => gateSourceByFreshness(setups.journal, journalFreshnessTimestamp(setups.journal)),
    [setups.journal],
  );

  const staleWholeTab = useMemo(() => {
    if (state.tab === "setups") {
      return tabSourcesStale("setups", setups.journal, null);
    }
    return tabSourcesStale(state.tab, shared.journal, shared.portfolio);
  }, [setups.journal, shared.journal, shared.portfolio, state.tab]);

  const sharedInitialLoad = shared.loading && !shared.journal && !shared.portfolio;
  const setupsInitialLoad = setups.loading && !setups.journal && !setups.evidence;

  // FP2-226: feed shell freshness from tab sources when timestamps exist; otherwise the
  // TopBar suppresses the misleading "Freshness unavailable" label on this hub.
  const freshnessSources = useMemo(() => {
    if (state.tab === "setups" && setups.journal) {
      return [
        {
          name: "Setup journal statistics",
          available: setups.journal.available,
          required: true,
          timestamp: journalFreshnessTimestamp(setups.journal),
          fallbackUsed: setups.journal.fallbackUsed,
        },
      ];
    }
    if ((state.tab === "overview" || state.tab === "performance") && sharedEnabled) {
      const sources = [];
      if (shared.journal) {
        sources.push({
          name: "Journal statistics",
          available: shared.journal.available,
          required: true,
          timestamp: journalFreshnessTimestamp(shared.journal),
          fallbackUsed: shared.journal.fallbackUsed,
        });
      }
      if (shared.portfolio) {
        sources.push({
          name: "Paper portfolio",
          available: shared.portfolio.available,
          required: true,
          timestamp: portfolioFreshnessTimestamp(shared.portfolio),
          fallbackUsed: shared.portfolio.fallbackUsed,
        });
      }
      return sources;
    }
    return [];
  }, [setups.journal, shared.journal, shared.portfolio, sharedEnabled, state.tab]);

  return (
    <div className="space-y-8 max-w-full" data-testid="analytics-page">
      <WorkflowFreshnessAdapter sources={freshnessSources} />
      <PageHeader
        title="Analytics"
        description="Paper-only statistical hub — overview, performance, setups, behaviour, validation, and human-versus-system comparison with honest source states."
        meta={<VerifiedPaperModeIndicator />}
      />

      <TabsRoot value={state.tab} onChange={(tab) => setTab(tab as typeof state.tab)}>
        <div data-testid="analytics-tabs">
          <Tabs items={TAB_ITEMS} onIntent={prefetchValidation} aria-label="Analytics sections" />
        </div>

        <AnalyticsFilterBar
          state={state}
          strategies={setups.strategies}
          strategiesLoading={setups.strategiesLoading}
          strategiesLoaded={setups.strategiesLoaded}
          strategiesError={setups.strategiesError}
          onRetryStrategies={() => void setups.reloadStrategies()}
          onApplyDraft={applyDraft}
          onApplyPreset={applyDatePreset}
          onClear={clearFilters}
        />

        {state.tab !== "setups" &&
        state.tab !== "behaviour" &&
        state.tab !== "validation" &&
        state.tab !== "comparison" &&
        shared.partialData ? (
          <p
            className="text-sm text-amber-500/90"
            data-testid="analytics-partial-data"
            role="status"
          >
            Partial analytics data — some sources failed. Retry unavailable sections before
            treating this as complete.
          </p>
        ) : null}

        {state.tab === "setups" &&
        ((setups.evidence && !setups.evidence.available && setups.journal?.available) ||
          (setups.journal && !setups.journal.available && setups.evidence?.available)) ? (
          <p
            className="text-sm text-amber-500/90"
            data-testid="analytics-partial-data"
            role="status"
          >
            Partial analytics data — one Setups source failed. Retry unavailable sections before
            treating this as complete.
          </p>
        ) : null}

        <TabPanel id="overview">
          {state.tab === "overview" && sharedInitialLoad ? (
            <LoadingState label="Loading analytics…" />
          ) : null}
          {state.tab === "overview" && shared.bothFailed ? (
            <ErrorState
              message="Analytics sources unavailable. Journal statistics and paper portfolio both failed."
              onRetry={() => void shared.reload()}
            />
          ) : null}
          {state.tab === "overview" && !shared.bothFailed ? (
            <>
              {staleWholeTab ? (
                <div data-testid="overview-stale-state">
                  <StaleState message="Analytics sources may be delayed or stale for this view." />
                </div>
              ) : null}
              <OverviewStats
                journal={gatedJournal}
                portfolio={gatedPortfolio}
                loading={shared.loading || shared.retryLoading}
                onRetryJournal={() => void shared.reload()}
                onRetryPortfolio={() => void shared.reload()}
              />
            </>
          ) : null}
        </TabPanel>

        <TabPanel id="performance">
          <TabHeading>Performance</TabHeading>
          {state.tab === "performance" && sharedInitialLoad ? (
            <LoadingState label="Loading analytics…" />
          ) : null}
          {state.tab === "performance" && shared.bothFailed ? (
            <ErrorState
              message="Analytics sources unavailable. Journal statistics and paper portfolio both failed."
              onRetry={() => void shared.reload()}
            />
          ) : null}
          {state.tab === "performance" && !shared.bothFailed ? (
            <PerformanceCharts
              source={gatedPortfolio}
              loading={(shared.loading && !shared.portfolio) || shared.retryLoading}
              onRetry={() => void shared.reload()}
              filtersSummary={filtersSummary}
              staleWholeTab={staleWholeTab}
            />
          ) : null}
        </TabPanel>

        <TabPanel id="setups">
          <TabHeading>Setups</TabHeading>
          {state.tab === "setups" && setupsInitialLoad ? (
            <LoadingState label="Loading analytics…" />
          ) : null}
          {state.tab === "setups" && !setupsInitialLoad ? (
            <SetupsCharts
              source={gatedSetupJournal}
              evidence={setups.evidence}
              loading={setups.loading && !setups.journal}
              evidenceLoading={setups.loading && !setups.evidence}
              onRetry={() => void setups.reload()}
              onRetryEvidence={() => void setups.reload()}
              filtersSummary={filtersSummary}
              evidenceFiltersSummary={evidenceFiltersSummary}
              evidenceLimitationNote={evidenceLimitationNote}
              staleWholeTab={staleWholeTab}
              groupBy={state.groupBy}
              onGroupByChange={setGroupBy}
              bucketOffset={state.bucketOffset}
              onPageChange={setBucketOffset}
            />
          ) : null}
        </TabPanel>

        <TabPanel id="behaviour">
          <TabHeading>Behaviour</TabHeading>
          {state.tab === "behaviour" ? (
            <BehaviourCharts apiParams={apiParams} enabled />
          ) : null}
        </TabPanel>

        <TabPanel id="validation">
          <TabHeading>Validation</TabHeading>
          {state.tab === "validation" ? (
            <ValidationCharts
              apiParams={apiParams}
              enabled
              onDimensionChange={setDimension}
            />
          ) : null}
        </TabPanel>

        <TabPanel id="comparison">
          <TabHeading>Comparison</TabHeading>
          {state.tab === "comparison" ? (
            <ComparisonCharts apiParams={apiParams} enabled />
          ) : null}
        </TabPanel>
      </TabsRoot>
    </div>
  );
}
