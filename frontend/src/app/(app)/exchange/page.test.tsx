import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ExchangeDiagnosticsPage from "./page";
import type { ExchangeDiagnosticsSummary } from "@/lib/api/types";

const mockReload = vi.fn<() => Promise<void>>();

let asyncState: {
  data: ExchangeDiagnosticsSummary | null;
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
    exchange: {
      diagnosticsSummary: vi.fn(),
    },
  },
}));

vi.mock("@/components/BloFinSyncPanel", () => ({
  BloFinSyncPanel: () => <div data-testid="blofin-sync-panel">BloFin sync</div>,
}));

vi.mock("@/components/exchange/ExchangeDemoAccountSync", () => ({
  ExchangeDemoAccountSync: () => <div data-testid="exchange-demo-sync">Demo sync</div>,
}));

const readySummary: ExchangeDiagnosticsSummary = {
  exchange_mode: "paper_exchange_demo",
  execution_mode: "paper",
  real_trading_enabled: false,
  demo_active: true,
  provider_health: "healthy",
  worker_enabled: false,
  telegram_enabled: false,
  position_mode: "long_short_mode",
  leverage: {
    inst_id: "BTC-USDT",
    margin_mode: "cross",
    leverage: "3",
    probe_ok: true,
  },
  instrument: {
    symbol: "BTCUSDT",
    inst_id: "BTC-USDT",
    active: true,
    probe_ok: true,
  },
  venue_positions_count: 0,
  last_exchange_order_status: "filled",
  last_demo_mirror_result: "created",
  last_demo_mirror_error_code: null,
  last_demo_mirror_error_message: null,
  last_cancel_status: null,
  readiness: "ready",
  warnings: [],
  generated_at: "2026-06-28T12:00:00Z",
};

describe("Exchange diagnostics route (/exchange) — FP2-129", () => {
  beforeEach(() => {
    asyncState = { data: null, loading: true, error: null };
    mockReload.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders loading without fabricated diagnostics", () => {
    render(<ExchangeDiagnosticsPage />);
    expect(screen.getByTestId("loading-state")).toBeInTheDocument();
    expect(screen.queryByTestId("exchange-diagnostics-card")).not.toBeInTheDocument();
    expect(screen.queryByText(/Paper only/i)).not.toBeInTheDocument();
  });

  it("renders failed request with retry and no stale success body", () => {
    asyncState = { data: null, loading: false, error: "Diagnostics unavailable" };
    render(<ExchangeDiagnosticsPage />);
    expect(screen.getByTestId("error-state")).toHaveTextContent("Diagnostics unavailable");
    expect(screen.queryByTestId("exchange-diagnostics-card")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(mockReload).toHaveBeenCalledTimes(1);
  });

  it("renders honest unavailable state when load succeeds with no payload", () => {
    asyncState = { data: null, loading: false, error: null };
    render(<ExchangeDiagnosticsPage />);
    expect(screen.getByTestId("error-state")).toHaveTextContent(/Exchange diagnostics unavailable/i);
    expect(screen.queryByTestId("exchange-diagnostics-card")).not.toBeInTheDocument();
  });

  it("renders successful diagnostics with paper / non-live posture", () => {
    asyncState = { data: readySummary, loading: false, error: null };
    render(<ExchangeDiagnosticsPage />);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Exchange diagnostics");
    expect(screen.getByTestId("exchange-diagnostics-card")).toBeInTheDocument();
    expect(screen.getByText("Paper only")).toBeInTheDocument();
    expect(screen.getByText("Real trading disabled")).toBeInTheDocument();
    expect(screen.getByText(/read-only, no orders placed/i)).toBeInTheDocument();
    expect(screen.getByTestId("blofin-sync-panel")).toBeInTheDocument();
    expect(screen.getByTestId("exchange-demo-sync")).toBeInTheDocument();
  });
});
