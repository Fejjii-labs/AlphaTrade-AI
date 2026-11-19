import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ADVANCED_ROUTES } from "@/components/layout/advanced-routes";
import type { HealthResponse, ProviderStatus, ProviderStatusResponse } from "@/lib/api/types";
import { routesOfKind } from "@/lib/navigation/route-registry";
import { apiTile, providerTiles } from "@/lib/status-tiles";
import { expectNoIdentifiers } from "@/test/no-identifiers";
import DiagnosticsPage from "./page";
import AdvancedRedirectPage from "../advanced/page";

const runtime = {
  health: null as HealthResponse | null,
  providers: null as ProviderStatusResponse | null,
  loading: false,
  refreshStatus: vi.fn(),
};
vi.mock("@/contexts/AppContext", () => ({
  useAppContext: () => runtime,
  useSafetyPosture: () => ({
    executionMode: runtime.health?.execution_mode ?? null,
    realTradingEnabled: runtime.health?.real_trading_enabled ?? null,
    providerMode: "fallback",
    postureKnown: runtime.health?.execution_mode != null,
  }),
}));

const provider = (over: Partial<ProviderStatus>): ProviderStatus => ({
  name: "mock-llm",
  kind: "llm",
  health: "healthy",
  using_fallback: false,
  is_mock: false,
  ...over,
});

beforeEach(() => {
  runtime.health = { status: "ok" } as HealthResponse;
  runtime.providers = {
    generated_at: "2026-10-27T09:00:00Z",
    providers: [
      provider({ name: "mock-llm", kind: "llm", is_mock: true }),
      provider({ name: "binance-usdm", kind: "market_data", health: "degraded" }),
      provider({ name: "bybit", kind: "exchange", health: "unavailable" }),
      provider({ name: "redis", kind: "tracing", using_fallback: true, is_mock: true }),
    ],
  };
  runtime.refreshStatus = vi.fn().mockResolvedValue(undefined);
});
afterEach(cleanup);

describe("Diagnostics", () => {
  it("shows the paper explanation, provider mode and all service health chips", () => {
    runtime.health = { status: "ok", execution_mode: "paper", real_trading_enabled: false } as HealthResponse;
    runtime.providers!.providers.push(
      provider({ name: "openai-embeddings", kind: "embeddings" }),
      provider({ name: "qdrant", kind: "vector" }),
    );
    render(<DiagnosticsPage />);
    for (const text of [
      "Paper mode active", "Real trading disabled", "Provider mode: fallback",
      "Market: binance-usdm", "LLM: mock-llm", "Embeddings: openai-embeddings",
      "Qdrant: qdrant", "Redis: healthy", "API ok",
    ]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(screen.getByText(/No real exchange orders are placed/)).toHaveTextContent(
      "Approvals gate paper execution only. Market data is read-only (Binance public API or mock fallback).",
    );
  });

  it("lists every diagnostics route from the registry, grouped", () => {
    render(<DiagnosticsPage />);
    const tools = screen.getByRole("heading", { name: "Tools" }).parentElement!;
    const hrefs = within(tools).getAllByRole("link").map((link) => link.getAttribute("href"));
    expect(hrefs.sort()).toEqual(routesOfKind("diagnostics").map((route) => route.path).sort());
    expect(ADVANCED_ROUTES.map((route) => route.href).sort()).toEqual(hrefs.sort());
    expect([...tools.querySelectorAll('[data-row-part="label"]')].map((label) => label.textContent)).toEqual([
      "Market & alerts",
      "Decisions",
      "Validation & research",
      "Journal tools",
      "Portfolio",
      "System",
    ]);
    expect(within(tools).getByRole("link", { name: "Scanner" })).toHaveAttribute("href", "/watcher");
  });

  it("shows API and provider status as colour-coded tiles with one-word states", () => {
    render(<DiagnosticsPage />);
    const tiles = screen.getAllByTestId("status-tile");
    expect(tiles.map((tile) => [tile.textContent, tile.dataset.tone])).toEqual([
      ["APIOK", "ok"],
      ["Language modelOKSimulated", "ok"],
      ["Binance futuresDegraded", "warn"],
      ["BybitDown", "down"],
      ["Rate limiterFallbackSimulated", "warn"],
    ]);
    expect(screen.queryByText("mock-llm")).not.toBeInTheDocument();
    expectNoIdentifiers();
  });

  it("keeps unknown status honest", () => {
    runtime.health = null;
    runtime.providers = null;
    render(<DiagnosticsPage />);
    const [api] = screen.getAllByTestId("status-tile");
    expect(api).toHaveAttribute("data-tone", "unknown");
    expect(api).toHaveTextContent("Unknown");
    expect(screen.getByText("Provider status unavailable.")).toBeInTheDocument();
  });

  it("refreshes status", () => {
    render(<DiagnosticsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(runtime.refreshStatus).toHaveBeenCalledOnce();
  });

  it("keeps build configuration and disclaimers closed", () => {
    render(<DiagnosticsPage />);
    expect(screen.getByTestId("settings-build-config")).not.toHaveAttribute("open");
    expect(screen.getByTestId("settings-build-config")).toHaveTextContent("API URL");
    expect(screen.getByText("Safety & disclaimers").closest("details")).not.toHaveAttribute("open");
  });

  it("renders the same view at the old /settings/advanced page", () => {
    render(<AdvancedRedirectPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Diagnostics" })).toBeInTheDocument();
  });
});

describe("status tile mapping", () => {
  it("gives each provider a readable, unique name", () => {
    expect(
      providerTiles([
        provider({ name: "mock-market-data", kind: "market_data" }),
        provider({ name: "binance-usdm-perpetual-replay", kind: "market_data" }),
        provider({ name: "redis", kind: "tracing" }),
        provider({ name: "mock-tracing", kind: "tracing" }),
        provider({ name: "mock-exchange", kind: "exchange" }),
        provider({ name: "blofin-demo", kind: "exchange" }),
        provider({ name: "acme-feed", kind: "news" }),
        provider({ name: "other-feed", kind: "news" }),
        provider({ name: "acme-feed", kind: "news" }),
      ]).map((tile) => tile.name),
    ).toEqual([
      "Market data",
      "Binance futures",
      "Rate limiter",
      "Tracing",
      "Exchange",
      "BloFin demo",
      "Acme Feed",
      "Other Feed",
      "Acme Feed (2)",
    ]);
  });

  it("maps API health", () => {
    expect(apiTile({ status: "degraded" } as HealthResponse)).toMatchObject({ tone: "warn", state: "Degraded" });
    expect(apiTile({ status: "error" } as HealthResponse)).toMatchObject({ tone: "down", state: "Down" });
  });
});
