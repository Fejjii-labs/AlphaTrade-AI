"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { resolveTimezone, type ResolvedTimezone } from "@/lib/timezone";

/**
 * Day-bucket timezone shared by the calendar, Daily Review and comparison:
 * risk settings → browser → UTC. `ready` turns true once the risk settings
 * answered (or failed), so callers can avoid a first request in the wrong zone.
 */
export function useResolvedTimezone(): ResolvedTimezone & { ready: boolean } {
  const [state, setState] = useState<ResolvedTimezone & { ready: boolean }>(() => ({
    ...resolveTimezone(null),
    ready: false,
  }));
  useEffect(() => {
    let active = true;
    Promise.resolve()
      .then(() => api.risk.settings())
      .then(
        (settings) => active && setState({ ...resolveTimezone(settings), ready: true }),
        () => active && setState({ ...resolveTimezone(null), ready: true }),
      );
    return () => {
      active = false;
    };
  }, []);
  return state;
}
