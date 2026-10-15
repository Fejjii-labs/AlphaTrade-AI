"use client";

import { useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import type {
  JournalTradeSource,
  MarketRegime,
  PortfolioSourceFilter,
  TradeRuleCompliance,
} from "@/lib/api/types";

import { isoDateOnly, addDays } from "./format";
import {
  DEFAULT_VALIDATION_DIMENSION,
  DEFAULT_VALIDATION_MIN_SAMPLE,
  TAB_SCOPED_PARAM_KEYS,
  buildAnalyticsApiParams,
  buildSetupAnalyticsApiParams,
  parseAnalyticsSearchParams,
  type AnalyticsTab,
  type SetupGroupBy,
  type ValidationDimension,
} from "./filterValidation";

export type {
  AnalyticsFilterParams,
  AnalyticsFilterState,
  AnalyticsTab,
  SetupAnalyticsApiParams,
  SetupGroupBy,
  ValidationDimension,
} from "./filterValidation";

export type DatePreset = "7d" | "30d" | "90d" | "ytd" | "all";

const VALID_PRESETS = new Set<DatePreset>(["7d", "30d", "90d", "ytd", "all"]);

function buildHref(pathname: string, params: URLSearchParams): string {
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
}

function presetToRange(preset: DatePreset, now = new Date()): { from: string | null; to: string | null } {
  const today = isoDateOnly(now);
  switch (preset) {
    case "7d":
      return { from: isoDateOnly(addDays(now, -6)), to: today };
    case "30d":
      return { from: isoDateOnly(addDays(now, -29)), to: today };
    case "90d":
      return { from: isoDateOnly(addDays(now, -89)), to: today };
    case "ytd":
      return { from: `${now.getUTCFullYear()}-01-01`, to: today };
    case "all":
    default:
      return { from: null, to: null };
  }
}

export {
  buildAnalyticsApiParams,
  buildFilterKey,
  buildSharedAnalyticsFilterKey,
  buildSetupAnalyticsApiParams,
  buildSetupFilterKey,
  formatAnalyticsWindowFiltersSummary,
  formatAppliedFiltersSummary,
  formatComparisonFiltersSummary,
  formatJournalStatsFiltersSummary,
  formatLearningAnalyticsFiltersSummary,
  formatStrategyQualityFiltersSummary,
  formatSetupEvidenceFiltersSummary,
  formatSetupEvidenceLimitationNote,
  formatValidationFiltersSummary,
} from "./filterValidation";

export type AnalyticsDraft = {
  dateFrom?: string | null;
  dateTo?: string | null;
  symbol?: string | null;
  timeframe?: string | null;
  portfolioSource?: PortfolioSourceFilter | null;
  setupId?: string | null;
  userStrategyId?: string | null;
  strategyVersionId?: string | null;
  journalSource?: JournalTradeSource | null;
  ruleCompliance?: TradeRuleCompliance | null;
  marketRegime?: MarketRegime | null;
  minSample?: number | null;
};

function setOrDelete(params: URLSearchParams, key: string, value: string | null | undefined): void {
  if (value) params.set(key, value);
  else params.delete(key);
}

function dropTabScopedParams(params: URLSearchParams): void {
  for (const key of TAB_SCOPED_PARAM_KEYS) params.delete(key);
}

export function useAnalyticsFilters() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const state = useMemo(
    () => parseAnalyticsSearchParams(searchParams),
    [searchParams],
  );

  const apiParams = useMemo(() => buildAnalyticsApiParams(state), [state]);
  const setupApiParams = useMemo(() => buildSetupAnalyticsApiParams(state), [state]);

  const pushParams = useCallback(
    (mutator: (params: URLSearchParams) => void) => {
      const params = new URLSearchParams(searchParams.toString());
      mutator(params);
      router.push(buildHref(pathname, params), { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const replaceParams = useCallback(
    (mutator: (params: URLSearchParams) => void) => {
      const params = new URLSearchParams(searchParams.toString());
      mutator(params);
      router.replace(buildHref(pathname, params), { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const setTab = useCallback(
    (tab: AnalyticsTab) => {
      pushParams((params) => {
        if (tab === "overview") params.delete("tab");
        else params.set("tab", tab);
        dropTabScopedParams(params);
      });
    },
    [pushParams],
  );

  const applyDraft = useCallback(
    (draft: AnalyticsDraft) => {
      pushParams((params) => {
        if (Object.keys(draft).length > 0) params.delete("offset");
        if ("dateFrom" in draft) setOrDelete(params, "date_from", draft.dateFrom);
        if ("dateTo" in draft) setOrDelete(params, "date_to", draft.dateTo);
        if ("symbol" in draft) setOrDelete(params, "symbol", draft.symbol);
        if ("timeframe" in draft) setOrDelete(params, "timeframe", draft.timeframe);
        if ("portfolioSource" in draft) {
          if (draft.portfolioSource && draft.portfolioSource !== "all") {
            params.set("source", draft.portfolioSource);
          } else if (state.tab === "performance") {
            params.delete("source");
          }
        }
        if ("journalSource" in draft) {
          if (draft.journalSource) params.set("source", draft.journalSource);
          else if (state.tab === "setups" || state.tab === "behaviour" || state.tab === "comparison") {
            params.delete("source");
          }
          params.delete("offset");
        }
        if ("setupId" in draft) {
          setOrDelete(params, "setup_id", draft.setupId);
          params.delete("offset");
        }
        if ("userStrategyId" in draft) {
          setOrDelete(params, "user_strategy_id", draft.userStrategyId);
          params.delete("offset");
        }
        if ("strategyVersionId" in draft) {
          setOrDelete(params, "strategy_version_id", draft.strategyVersionId);
        }
        if ("ruleCompliance" in draft) {
          setOrDelete(params, "rule_compliance", draft.ruleCompliance);
        }
        if ("marketRegime" in draft) {
          setOrDelete(params, "market_regime", draft.marketRegime);
        }
        if ("minSample" in draft) {
          if (
            draft.minSample != null &&
            draft.minSample !== DEFAULT_VALIDATION_MIN_SAMPLE
          ) {
            params.set("min_sample", String(draft.minSample));
          } else if (state.tab === "validation") {
            params.delete("min_sample");
          }
        }
      });
    },
    [pushParams, state.tab],
  );

  const applyDatePreset = useCallback(
    (preset: DatePreset) => {
      if (!VALID_PRESETS.has(preset)) return;
      const range = presetToRange(preset);
      applyDraft({ dateFrom: range.from, dateTo: range.to });
    },
    [applyDraft],
  );

  const setGroupBy = useCallback(
    (groupBy: SetupGroupBy) => {
      pushParams((params) => {
        if (groupBy === "setup") params.delete("group_by");
        else params.set("group_by", groupBy);
        params.delete("offset");
      });
    },
    [pushParams],
  );

  const setBucketOffset = useCallback(
    (offset: number) => {
      pushParams((params) => {
        if (offset <= 0) params.delete("offset");
        else params.set("offset", String(offset));
      });
    },
    [pushParams],
  );

  const setDimension = useCallback(
    (dimension: ValidationDimension) => {
      pushParams((params) => {
        params.delete("offset");
        if (dimension === DEFAULT_VALIDATION_DIMENSION) params.delete("dimension");
        else params.set("dimension", dimension);
      });
    },
    [pushParams],
  );

  const clearFilters = useCallback(() => {
    pushParams((params) => {
      params.delete("date_from");
      params.delete("date_to");
      params.delete("symbol");
      params.delete("timeframe");
      dropTabScopedParams(params);
    });
  }, [pushParams]);

  const cleanupIgnoredParams = useCallback(() => {
    if (state.ignoredParams.length === 0) return;
    replaceParams((params) => {
      for (const key of state.ignoredParams) params.delete(key);
    });
  }, [replaceParams, state.ignoredParams]);

  return {
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
  };
}
