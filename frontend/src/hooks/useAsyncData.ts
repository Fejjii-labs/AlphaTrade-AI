"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Shared async loader with honest failure semantics.
 *
 * - A failed load clears `data`: stale results are never rendered beside an
 *   error as if they were current (FP2-101).
 * - A request-generation guard drops stale responses: when a newer load starts
 *   (deps change or manual reload), an older in-flight response can no longer
 *   overwrite the newer result.
 */
export function useAsyncData<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const generationRef = useRef(0);

  const reload = useCallback(async () => {
    const generation = ++generationRef.current;
    setLoading(true);
    setError(null);
    try {
      const result = await loader();
      if (!canCommit(generation, generationRef.current)) return;
      setData(result);
    } catch (err) {
      if (!canCommit(generation, generationRef.current)) return;
      setData(null);
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      if (canCommit(generation, generationRef.current)) {
        setLoading(false);
      }
    }
  }, [loader]);

  useEffect(() => {
    void reload();
    return () => {
      // Drop the in-flight load so a late callback cannot call setState
      // after unmount, including once the test environment has removed window.
      generationRef.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, error, reload };
}

function canCommit(generation: number, current: number): boolean {
  return generation === current && typeof window !== "undefined";
}
