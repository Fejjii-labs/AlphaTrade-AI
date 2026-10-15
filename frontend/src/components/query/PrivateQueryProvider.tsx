"use client";

import { createContext, useContext, useEffect, useMemo } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { createPrivateQueryClient, registerPrivateClient, clearPrivateQueries } from "@/lib/private-query-client";
import { onSessionCleared } from "@/lib/auth/session-events";

type Scope = { organizationId: string; userId: string };
const PrivateQueryContext = createContext<{ client: QueryClient; scope: Scope | null } | null>(null);

export function PrivateQueryProvider({ organizationId, userId, children }: {
  organizationId: string | null; userId: string | null; children: React.ReactNode;
}) {
  // Each identity owns a separate client, so even a late response cannot reach the next identity.
  const identity = `${organizationId ?? ""}/${userId ?? ""}`;
  const state = useMemo(() => ({ identity, client: createPrivateQueryClient() }), [identity]);
  const client = state.client;
  const scope = useMemo(() => organizationId && userId ? { organizationId, userId } : null,
    [organizationId, userId]);
  useEffect(() => registerPrivateClient(client), [client]);
  useEffect(() => onSessionCleared(clearPrivateQueries), []);
  return <PrivateQueryContext.Provider value={{ client, scope }}>{children}</PrivateQueryContext.Provider>;
}

export function usePrivateQueryScope() {
  const context = useContext(PrivateQueryContext);
  if (!context) throw new Error("Private source queries require an authenticated query scope");
  return context;
}
