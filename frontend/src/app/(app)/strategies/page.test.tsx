import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import StrategiesPage from "./page";
import { api } from "@/lib/api";
import type { BrainOverview, BrainSetup, BrainStrategy } from "@/lib/api/brain-types";
import { sessionCleared } from "@/lib/auth/session-events";
import { expectNoIdentifiers } from "@/test/no-identifiers";

const state = vi.hoisted(() => ({
  params: new URLSearchParams(),
  identity: { user: { id: "user-a" }, organization: { id: "org-a" } } as {
    user: { id: string } | null; organization: { id: string } | null;
  },
}));
const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useSearchParams: () => state.params, useRouter: () => router }));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => state.identity }));

const strategy = (symbol: string, kind: BrainStrategy["spec"]["kind"], version: number, enabled = true) => ({
  strategy_id: `11111111-1111-4111-8111-${String(version).padStart(12, "0")}`,
  version_id: `22222222-2222-4222-8222-${String(version).padStart(12, "0")}`,
  version, name: "Nested", status: "approved", enabled, execution_permission: "paper_gates_required",
  spec: { kind, symbol, direction: "short", trigger_timeframe: "15m", parameters: {} },
}) as unknown as BrainStrategy;

const setup = (family: "nested_continuation" | "sfp", i: number) => ({
  setup_id: `33333333-3333-4333-8333-${String(i).padStart(12, "0")}`, family, symbol: "BTCUSDT", instrument: "BTCUSDT",
  direction: "long", timeframe: "15m", state: "CONFIRMED", observed_at: new Date(Date.now() - (i + 1) * 60000).toISOString(),
}) as unknown as BrainSetup;

function view(overrides: Partial<BrainOverview> = {}): BrainOverview {
  return { strategies: [], setups: [], watched_symbols: [], limitations: [], paper_only: true, ...overrides } as BrainOverview;
}

beforeEach(() => {
  state.params = new URLSearchParams();
  state.identity = { user: { id: "user-a" }, organization: { id: "org-a" } };
  window.history.replaceState(null, "", "/strategies");
  vi.spyOn(api.strategyBrain, "overview").mockResolvedValue(view());
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows confirmed / traded / won per family from its own read, without raw counts or IDs", async () => {
  const strategies = [
    strategy("ETHUSDT", "operational_nested_continuation/v1", 3),
    strategy("BTCUSDT", "operational_nested_continuation/v1", 7, false),
    strategy("ZECUSDT", "swing_failure_pattern/v1", 2, false),
  ];
  vi.mocked(api.strategyBrain.overview).mockImplementation(async (params) =>
    params?.family === "sfp"
      ? view({ strategies, setups: Array.from({ length: 20 }, (_, i) => setup("sfp", i)), setup_count: 1172, outcomes: { confirmed: 4, traded: 0, won: 0 } })
      : view({ strategies, setups: [setup("nested_continuation", 0)], setup_count: 1, outcomes: { confirmed: 12, traded: 3, won: 2 } }),
  );
  render(<StrategiesPage />);
  const [nested, sfp] = screen.getAllByTestId("strategy-family");
  await waitFor(() => expect(within(nested).getByTestId("family-outcomes")).toHaveTextContent("12 confirmed · 3 traded · 2 won"));
  expect(within(sfp).getByTestId("family-outcomes")).toHaveTextContent("4 confirmed · 0 traded · 0 won");
  expect(within(nested).getByText("BTCUSDT, ETHUSDT · 15m")).toBeInTheDocument();
  expect(within(nested).getByTestId("family-enabled")).toHaveTextContent("On");
  expect(within(sfp).getByTestId("family-enabled")).toHaveTextContent("Off");
  expect(screen.queryByText(/1172/)).not.toBeInTheDocument();
  expect(screen.queryByText(/in 7 days/)).not.toBeInTheDocument();
  expect(api.strategyBrain.overview).toHaveBeenCalledWith({ family: "nested_continuation", since: expect.any(String) });
  expect(api.strategyBrain.overview).toHaveBeenCalledWith({ family: "sfp", since: expect.any(String) });
  expectNoIdentifiers();
});

it("has no experiments, captured drafts or other configurations", async () => {
  render(<StrategiesPage />);
  await waitFor(() => expect(api.strategyBrain.overview).toHaveBeenCalled());
  expect(screen.getAllByTestId("strategy-family")).toHaveLength(3);
  expect(screen.queryByText(/Experiments/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Captured strategy drafts/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Other configurations/)).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Discuss a strategy" })).toHaveAttribute("href", "/agent?discuss=strategy");
});

it("says results are unavailable when a family read fails", async () => {
  vi.mocked(api.strategyBrain.overview).mockImplementation(async (params) => {
    if (params?.family === "sfp") throw new Error("down");
    return view({ outcomes: { confirmed: 1, traded: 0, won: 0 } });
  });
  render(<StrategiesPage />);
  const [, sfp] = screen.getAllByTestId("strategy-family");
  await waitFor(() => expect(within(sfp).getByTestId("family-outcomes")).toHaveTextContent("Results unavailable"));
});

it("forwards the old Strategies risk anchor to the bot page", async () => {
  window.history.replaceState(null, "", "/strategies#risk");
  router.replace.mockClear();
  render(<StrategiesPage />);
  expect(router.replace).toHaveBeenCalledExactlyOnceWith("/autonomous#limits");
  await waitFor(() => expect(api.strategyBrain.overview).toHaveBeenCalled());
  window.history.replaceState(null, "", "/strategies");
});

it("lists a family's strategies with plain labels and a way back", async () => {
  state.params.set("family", "nested");
  vi.mocked(api.strategyBrain.overview).mockResolvedValue(view({ strategies: [strategy("ETHUSDT", "operational_nested_continuation/v1", 3)] }));
  render(<StrategiesPage />);
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Nested Continuation");
  expect(screen.getByRole("link", { name: "← Strategies" })).toHaveAttribute("href", "/strategies");
  expect(await screen.findByRole("heading", { name: "ETHUSDT · Short · 15m" })).toBeInTheDocument();
  expect(screen.getByText("On · v3")).toBeInTheDocument();
  expectNoIdentifiers();
});

it("drops the view on session invalidation and skips unauthenticated reads", async () => {
  const { rerender } = render(<StrategiesPage />);
  await waitFor(() => expect(api.strategyBrain.overview).toHaveBeenCalledTimes(3));
  act(() => sessionCleared());
  state.identity = { user: null, organization: null };
  rerender(<StrategiesPage />);
  expect(screen.queryByTestId("trader-strategies")).not.toBeInTheDocument();
});

it("registers D-line as a separate draft research family", async () => {
  render(<StrategiesPage />);
  await waitFor(() => expect(api.strategyBrain.overview).toHaveBeenCalledWith({ family: "d_line", since: expect.any(String) }));
  expect(screen.getByText("D-line", { selector: "h2" })).toBeInTheDocument();
});
