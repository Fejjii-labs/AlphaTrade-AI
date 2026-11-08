import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import MarketPage from "./page";
import type {
  CanonicalMarketMonitorStatusRead,
  MarketAnalyzeResponse,
  MarketSnapshotResponse,
} from "@/lib/api/types";

const mockReload = vi.fn<() => Promise<void>>();
const mockSnapshot = vi.fn();
const mockAnalyze = vi.fn();
const mockGetMarketStatus = vi.fn();

let asyncState: {
  data: MarketSnapshotResponse | null;
  loading: boolean;
  error: string | null;
};

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: () => ({
    data: asyncState.data,
    loading: asyncState.loading,
    error: asyncState.error,
    reload: mockReload,
  }),
}));

vi.mock("@/lib/api", () => ({
  api: {
    market: {
      snapshot: (...args: unknown[]) => mockSnapshot(...args),
      analyze: (...args: unknown[]) => mockAnalyze(...args),
    },
    canonical: {
      getMarketStatus: (...args: unknown[]) => mockGetMarketStatus(...args),
    },
  },
}));

const replayMonitor: CanonicalMarketMonitorStatusRead = {
  authority: "canonical_market_monitor",
  live_executable: false,
  watcher_activated: false,
  compatibility_price_used: false,
  symbol: "BTCUSDT",
  mode: "replay",
  availability: "replay",
  reason: "replay_fixture",
  perpetual: true,
  source: {
    venue: "binance",
    market_type: "perpetual",
    instrument_id: "binance:usdm_futures:perpetual:BTCUSDT",
    provider_symbol: "BTCUSDT",
    provider_name: "binance-usdm-perpetual-replay",
    source_family: "replay_fixture",
    adapter_version: "binance-usdm-perpetual/v1",
    is_live: false,
    is_mock: true,
    fallback_used: false,
  },
  current_price: {
    usable_as_current_market_price: false,
    presentation: "replay_fixture",
    price: "91234.5",
    source_time: "2026-01-15T16:15:04.000Z",
    venue_trade_id: "agg-1",
    is_live: false,
    is_mock: true,
    fallback_used: false,
    freshness: {
      policy_version: "first-slice-btc-usdt-usdm-freshness/v1",
      state: "fresh",
      evaluated_at: "2026-01-15T16:15:05.000Z",
    },
  },
  last_update: "2026-01-15T16:15:04.000Z",
  evaluated_at: "2026-01-15T16:15:05.000Z",
  stream: {
    reconnect_state: "continuous",
    gap_state: "none",
    warm_up_status: "complete",
    reconnect_count: 0,
  },
  coverage: { completeness: "complete", gap_state: "none" },
  cvd: { available: true, event_count: 2 },
  ohlcv: { available: true, completeness_15m: "complete", completeness_4h: "complete" },
  provider: {
    name: "binance-usdm-perpetual-replay",
    health: "healthy",
    is_mock: true,
    using_fallback: false,
  },
  backoff: { active: false, attempt: 0 },
  content_hash: "ab".repeat(32),
};

function makeSnapshot(overrides: Partial<MarketSnapshotResponse> = {}): MarketSnapshotResponse {
  const meta = {
    symbol: "BTCUSDT",
    exchange: "binance",
    timeframe: "1h" as const,
    timestamp: "2026-07-27T10:00:00.000Z",
    source: "mock",
    is_live: false,
    is_stale: false,
    fallback_used: true,
    cache_hit: false,
    retrieved_at: "2026-07-27T10:00:00.000Z",
    stale_reason: null,
    provider_name: "mock",
  };
  return {
    meta,
    ticker: {
      meta,
      last_price: "50123.45",
      bid: null,
      ask: null,
      volume_24h: null,
    },
    latest_bar: {
      open: "50000",
      high: "50500",
      low: "49800",
      close: "50123.45",
      volume: "12.5",
      timestamp: "2026-07-27T09:00:00.000Z",
    },
    indicators: {
      symbol: "BTCUSDT",
      timeframe: "1h",
      rsi: 55,
      ema_fast: "50010",
      macd: 1.2,
      atr: "250",
      timestamp: "2026-07-27T10:00:00.000Z",
    },
    ...overrides,
  };
}

describe("MarketPage route honesty (FP2-129)", () => {
  beforeEach(() => {
    asyncState = { data: null, loading: true, error: null };
    mockReload.mockResolvedValue(undefined);
    mockGetMarketStatus.mockResolvedValue(replayMonitor);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders a single h1 and read-only / no-execution posture copy", () => {
    asyncState = { data: makeSnapshot(), loading: false, error: null };
    render(<MarketPage />);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Market Monitor");
    expect(screen.getByText(/no exchange execution/i)).toBeInTheDocument();
    // No "mock" exchange option; the mock is only a badged server fallback.
    expect(screen.getByTestId("market-exchange")).toHaveTextContent("Binance");
    expect(screen.queryByRole("option", { name: "mock" })).not.toBeInTheDocument();
  });

  it("renders loading without an empty or fabricated snapshot", () => {
    asyncState = { data: null, loading: true, error: null };
    render(<MarketPage />);
    expect(screen.getByTestId("loading-state")).toBeInTheDocument();
    expect(screen.queryByTestId("empty-state")).not.toBeInTheDocument();
    expect(screen.queryByText(/No snapshot/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/50123/)).not.toBeInTheDocument();
  });

  it("renders failed request with retry and no empty success", () => {
    asyncState = { data: null, loading: false, error: "Market snapshot failed" };
    render(<MarketPage />);
    expect(screen.getByTestId("error-state")).toHaveTextContent("Market snapshot failed");
    expect(screen.queryByText(/No snapshot/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(mockReload).toHaveBeenCalledTimes(1);
  });

  it("renders honest empty state only when load succeeds with no data", () => {
    asyncState = { data: null, loading: false, error: null };
    render(<MarketPage />);
    expect(screen.getByTestId("empty-state")).toHaveTextContent(/No snapshot/i);
    expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    expect(screen.queryByTestId("error-state")).not.toBeInTheDocument();
  });

  it("renders successful mock fallback content without claiming live prices", async () => {
    asyncState = { data: makeSnapshot(), loading: false, error: null };
    render(<MarketPage />);
    expect(screen.getAllByText(/50123/).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/Using mock fallback — prices are not live exchange data/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Compatibility last price/i)).toBeInTheDocument();
    expect(screen.getByText(/Not a current live perpetual price/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("perpetual-market-status")).toBeInTheDocument());
    expect(screen.getByTestId("monitor-current-price-label")).toHaveTextContent(
      /not a current live perpetual price/i,
    );
  });

  it("runs analyze as a user action without inventing prior analysis", async () => {
    const snapshot = makeSnapshot();
    const analysis: MarketAnalyzeResponse = {
      snapshot,
      indicators: snapshot.indicators!,
      strategy_signals: [],
      data_quality: "mock",
      confidence_penalty_applied: false,
    };
    mockAnalyze.mockResolvedValue(analysis);
    asyncState = { data: makeSnapshot(), loading: false, error: null };
    render(<MarketPage />);
    expect(screen.queryByText(/No strategy signals/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^analyze$/i }));
    await waitFor(() => expect(mockAnalyze).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/No strategy signals/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Strategy signals/i })).toBeInTheDocument();
  });
});
