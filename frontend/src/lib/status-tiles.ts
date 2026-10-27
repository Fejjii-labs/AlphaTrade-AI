import type { StatusTileProps } from "@/components/ui/status-tile";
import type { HealthResponse, ProviderStatus } from "@/lib/api/types";
import { humanizeToken } from "@/lib/format";

const KIND_LABELS: Record<string, string> = {
  llm: "Language model",
  embeddings: "Embeddings",
  vector: "Vector store",
  exchange: "Exchange",
  market_data: "Market data",
  news: "News",
  notifications: "Notifications",
  tracing: "Tracing",
  email: "Email",
  billing: "Billing",
};

type Tile = Omit<StatusTileProps, "className">;

function providerState(provider: ProviderStatus): Pick<Tile, "tone" | "state"> {
  if (provider.health === "unavailable") return { tone: "down", state: "Down" };
  if (provider.health === "degraded") return { tone: "warn", state: "Degraded" };
  if (provider.using_fallback) return { tone: "warn", state: "Fallback" };
  if (provider.health === "healthy") return { tone: "ok", state: "OK" };
  return { tone: "unknown", state: "Unknown" };
}

const NAME_LABELS: Record<string, string> = {
  redis: "Rate limiter",
  "binance-usdm": "Binance futures",
  blofin: "BloFin",
  "blofin-demo": "BloFin demo",
  bybit: "Bybit",
  openai: "OpenAI",
};

/** Provider name without mock/replay markers: `binance-usdm-perpetual-replay` → `binance-usdm`. */
function coreName(name: string): string {
  return name
    .toLowerCase()
    .replace(/^mock-|-mock$/g, "")
    .replace(/-(perpetual|replay|live)\b/g, "");
}

/**
 * Readable, unique tile names. A provider named after its kind (`mock-tracing`)
 * or alone in its kind shows the kind; others show a known or humanized name.
 */
function providerNames(providers: readonly ProviderStatus[]): string[] {
  const counts = new Map<string, number>();
  for (const p of providers) counts.set(p.kind, (counts.get(p.kind) ?? 0) + 1);
  const used = new Map<string, number>();
  return providers.map((p) => {
    const core = coreName(p.name);
    const kindLabel = KIND_LABELS[p.kind] ?? humanizeToken(p.kind);
    const base =
      NAME_LABELS[core] ??
      (core.replace(/-/g, "_") === p.kind || (counts.get(p.kind) ?? 0) < 2
        ? kindLabel
        : humanizeToken(core.replace(/-/g, "_")));
    const seen = (used.get(base) ?? 0) + 1;
    used.set(base, seen);
    return seen === 1 ? base : `${base} (${seen})`;
  });
}

export function providerTiles(providers: readonly ProviderStatus[]): Tile[] {
  const names = providerNames(providers);
  return providers.map((provider, index) => ({
    name: names[index]!,
    ...providerState(provider),
    tag: provider.is_mock ? "Simulated" : null,
  }));
}

export function apiTile(health: HealthResponse | null): Tile {
  const status = health?.status?.toLowerCase();
  if (!status) return { name: "API", tone: "unknown", state: "Unknown" };
  if (status === "ok" || status === "healthy") return { name: "API", tone: "ok", state: "OK" };
  if (status === "degraded") return { name: "API", tone: "warn", state: "Degraded" };
  return { name: "API", tone: "down", state: "Down" };
}
