"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  loadSource,
  pendingSource,
  type SourceResult,
} from "@/components/workflows/sourceResult";

type Loaders<T extends Record<string, unknown>> = {
  [K in keyof T]: () => Promise<T[K]>;
};
type Sources<T extends Record<string, unknown>> = {
  [K in keyof T]: SourceResult<T[K]>;
};

function allPending<T extends Record<string, unknown>>(keys: (keyof T)[]): Sources<T> {
  return Object.fromEntries(keys.map((key) => [key, pendingSource()])) as Sources<T>;
}

/**
 * Progressive loader: every source is requested in parallel and committed as
 * soon as it resolves, so one slow endpoint can no longer hold the whole page
 * behind a single skeleton. A generation guard drops responses from an older
 * load (reload / unmount), and a failed source stays explicitly unavailable.
 */
export function useDashboardSources<T extends Record<string, unknown>>(
  loaders: Loaders<T>,
) {
  const keys = Object.keys(loaders) as (keyof T)[];
  const [data, setData] = useState<Sources<T>>(() => allPending<T>(keys));
  const [inFlight, setInFlight] = useState(keys.length);
  const generationRef = useRef(0);

  const reload = useCallback(async () => {
    const generation = ++generationRef.current;
    setInFlight(keys.length);
    await Promise.all(
      keys.map(async (key) => {
        const result = await loadSource(loaders[key]());
        if (generation !== generationRef.current || typeof window === "undefined") return;
        setData((prev) => ({ ...prev, [key]: result }));
        setInFlight((count) => Math.max(0, count - 1));
      }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaders]);

  useEffect(() => {
    void reload();
    return () => {
      generationRef.current += 1;
    };
  }, [reload]);

  return { data, loading: inFlight > 0, reload };
}
