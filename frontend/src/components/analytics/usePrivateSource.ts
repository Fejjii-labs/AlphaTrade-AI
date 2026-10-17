"use client";

import { useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { okSource, failedSource } from "@/components/workflows";
import { usePrivateQueryScope } from "@/components/query/PrivateQueryProvider";

export function privateSourceKey(scope: { organizationId: string; userId: string } | null, endpoint: string, filters: string) {
  return ["private", scope?.organizationId, scope?.userId, endpoint, filters] as const;
}

/** Raw transport failures stay rejected for Query's retry policy; domain data stays resolved. */
export function usePrivateSource<T>(
  enabled: boolean, endpoint: string, filters: string,
  fetcher: (signal: AbortSignal) => Promise<T>,
) {
  const { client, scope } = usePrivateQueryScope();
  const active = enabled && Boolean(scope);
  const query = useQuery({
    queryKey: privateSourceKey(scope, endpoint, filters),
    enabled: active,
    queryFn: async ({ signal }) => {
      const data = await fetcher(signal);
      signal.throwIfAborted();
      return data;
    },
  }, client);
  const { refetch } = query;
  const reload = useCallback(async () => { if (active) await refetch(); }, [active, refetch]);
  const source = useMemo(() => !active ? null : query.data !== undefined ? okSource(query.data) :
    query.isError ? failedSource<T>(query.error) : null, [active, query.isError, query.error, query.data]);
  return {
    source,
    loading: active && query.isPending,
    retryLoading: active && query.isFetching && !query.isPending,
    refreshError: active && query.isError && query.data !== undefined ? failedSource(query.error).error : null,
    reload,
    loadedKey: active && (query.data !== undefined || query.isError) ? filters : null,
  };
}
