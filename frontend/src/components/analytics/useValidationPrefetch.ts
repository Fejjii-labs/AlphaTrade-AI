"use client";
import { useCallback } from "react";
import { usePrivateQueryScope } from "@/components/query/PrivateQueryProvider";
import { api } from "@/lib/api";
import { buildValidationFilterKey, type AnalyticsFilterParams } from "./filterValidation";
import { learningParamsFromValidation } from "./useValidationSources";
import { privateSourceKey } from "./usePrivateSource";

/** Prefetch only the validation summary on explicit tab intent, in this private identity scope. */
export function useValidationPrefetch(params: AnalyticsFilterParams) {
  const { client, scope } = usePrivateQueryScope();
  return useCallback((tab: string) => {
    if (tab !== "validation" || !scope) return;
    const summary = learningParamsFromValidation(params);
    void client.prefetchQuery({
      queryKey: privateSourceKey(scope, "/learning-analytics/summary", buildValidationFilterKey(summary)),
      queryFn: async ({ signal }) => {
        const data = await api.learningAnalytics.summary(summary, { signal });
        signal.throwIfAborted();
        return data;
      },
    });
  }, [client, params, scope]);
}
