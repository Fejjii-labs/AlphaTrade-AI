import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SettingsExchangePage from "./page";

const mockReload = vi.fn();

let asyncState: {
  data: unknown;
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

vi.mock("@/components/ExchangeDiagnosticsCard", () => ({
  ExchangeDiagnosticsCard: () => <div data-testid="exchange-diagnostics-card">Diagnostics</div>,
}));

describe("Settings exchange shim (FP2-129)", () => {
  beforeEach(() => {
    asyncState = { data: null, loading: true, error: null };
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows loading without fabricated diagnostics content", () => {
    render(<SettingsExchangePage />);
    expect(screen.getByTestId("loading-state")).toBeInTheDocument();
    expect(screen.queryByTestId("exchange-diagnostics-card")).not.toBeInTheDocument();
  });

  it("shows failed request with retry and no diagnostics body", () => {
    asyncState = { data: null, loading: false, error: "Diagnostics failed" };
    render(<SettingsExchangePage />);
    expect(screen.getByTestId("error-state")).toHaveTextContent("Diagnostics failed");
    expect(screen.queryByTestId("exchange-diagnostics-card")).not.toBeInTheDocument();
  });

  it("renders diagnostics content on success", () => {
    asyncState = {
      data: { venue: "blofin", mode: "demo" },
      loading: false,
      error: null,
    };
    render(<SettingsExchangePage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/Exchange status/i);
    expect(screen.getByText(/provisioned by the operator/i)).toBeInTheDocument();
    expect(screen.getByTestId("exchange-diagnostics-card")).toBeInTheDocument();
    expect(screen.getByTestId("blofin-sync-panel")).toBeInTheDocument();
  });
});
