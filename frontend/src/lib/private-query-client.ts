import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./api/client";

const activeClients = new Set<QueryClient>();

export function retryPrivateRead(failureCount: number, error: unknown): boolean {
  if (failureCount >= 1) return false;
  if (error instanceof DOMException && error.name === "AbortError") return false;
  if (error instanceof ApiError) return error.status >= 500 || error.status === 429;
  return error instanceof TypeError; // Fetch network failures; domain evidence is successful data.
}

export function createPrivateQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: {
    queries: { staleTime: 30_000, gcTime: 5 * 60_000, retry: retryPrivateRead,
      retryDelay: 250, refetchOnWindowFocus: false, refetchOnReconnect: false },
    mutations: { retry: false },
  } });
}

export function registerPrivateClient(client: QueryClient): () => void {
  activeClients.add(client);
  return () => { activeClients.delete(client); void client.cancelQueries(); client.clear(); };
}

export function clearPrivateQueries(): void {
  for (const client of activeClients) { void client.cancelQueries(); client.clear(); }
}
