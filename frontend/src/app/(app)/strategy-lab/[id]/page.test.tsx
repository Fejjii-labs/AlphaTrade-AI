import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import StrategyDetailPage from "./page";

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "user-1" }, organization: { id: "org-1" } }) }));
vi.mock("@/lib/api/generated/client", async original => ({
  ...await original<typeof import("@/lib/api/generated/client")>(),
  paperAccount: vi.fn(async () => ({ account: null, can_register: false })),
  strategyVersions: vi.fn(async () => ({ items: [], total: 0, limit: 100, offset: 0 })),
}));

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: Error) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const {
  strategy,
  strategyGetDeferred,
  paperValidationMock,
  paperEligibilityMock,
  schedulerStatusMock,
  schedulerHistoryMock,
  paperSignalsMock,
  paperTradesMock,
  alertsListMock,
  useParamsMock,
  asyncDataState,
} = vi.hoisted(() => {
  const strategy = {
    id: "strat-1",
    name: "HTF Pullback",
    setup_type: "htf_trend_pullback",
    current_version: 2,
    enabled: true,
    validation_status: "in_review",
    backtest_status: "completed",
    paper_validation_status: "in_progress",
    paper_eligible: true,
    latest_card: {
      strategy_name: "HTF Pullback",
      market_type: "crypto_perp",
      asset_universe: ["BTCUSDT"],
      timeframes: ["4h"],
      entry_conditions: ["entry"],
      confirmation_conditions: ["confirm"],
      invalidation: ["invalid"],
      stop_loss: ["stop"],
      take_profit_plan: ["tp"],
      runner_plan: ["runner"],
      position_sizing: ["size"],
      add_rules: [],
      no_trade_rules: ["no"],
      backtest_rules: [],
      success_criteria: [],
      validation_status: "in_review",
    },
    created_at: "2026-07-20T10:00:00.000Z",
    updated_at: "2026-07-21T10:00:00.000Z",
  };

  return {
    strategy,
    strategyGetDeferred: deferred<typeof strategy>(),
    paperValidationMock: vi.fn(),
    paperEligibilityMock: vi.fn(),
    schedulerStatusMock: vi.fn(),
    schedulerHistoryMock: vi.fn(),
    paperSignalsMock: vi.fn(),
    paperTradesMock: vi.fn(),
    alertsListMock: vi.fn(),
    useParamsMock: vi.fn(() => ({ id: "strat-1" })),
    asyncDataState: {
      strategy: {
        data: null as typeof strategy | null,
        loading: true,
        error: null as string | null,
        reload: vi.fn(),
      },
      testability: {
        data: {
          has_structured_rules: true,
          ready_for_backtest: true,
          structured_rules: null,
        },
        loading: false,
        error: null,
        reload: vi.fn(),
      },
      versions: {
        data: { items: [], total: 0, limit: 50, offset: 0 },
        loading: false,
        error: null,
        reload: vi.fn(),
      },
    },
  };
});

vi.mock("next/navigation", () => ({
  useParams: () => useParamsMock(),
}));

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: (loader: () => Promise<unknown>) => {
    const key = String(loader);
    if (key.includes("testability")) return asyncDataState.testability;
    if (key.includes("listVersions")) return asyncDataState.versions;
    return asyncDataState.strategy;
  },
}));

vi.mock("@/hooks/useWatcherMonitoring", async () => {
  const { makeWatcherMonitoringSnapshot } = await import("@/lib/watcher-monitoring-fixtures");
  return {
    useWatcherMonitoring: () => ({
      data: makeWatcherMonitoringSnapshot(),
      loading: false,
      error: null,
      reload: vi.fn(),
    }),
  };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      strategies: {
        ...actual.api.strategies,
        get: vi.fn(() => strategyGetDeferred.promise),
        paperValidation: (...args: unknown[]) => paperValidationMock(...args),
        paperEligibility: (...args: unknown[]) => paperEligibilityMock(...args),
        schedulerStatus: (...args: unknown[]) => schedulerStatusMock(...args),
        schedulerHistory: (...args: unknown[]) => schedulerHistoryMock(...args),
        paperValidationSignals: (...args: unknown[]) => paperSignalsMock(...args),
        paperValidationTrades: (...args: unknown[]) => paperTradesMock(...args),
      },
      alerts: {
        ...actual.api.alerts,
        list: (...args: unknown[]) => alertsListMock(...args),
      },
      conversations: {
        list: vi.fn(async () => ({ items: [], total: 0, limit: 50, offset: 0 })),
        listMessages: vi.fn(async () => ({ items: [], total: 0, limit: 100, offset: 0 })),
        listProposals: vi.fn(async () => ({ items: [], total: 0, limit: 10, offset: 0 })),
        confirmProposal: vi.fn(),
        rejectProposal: vi.fn(),
      },
      chat: {
        ...actual.api.chat,
        message: vi.fn(),
      },
    },
  };
});

function resolveStrategyLoaded() {
  asyncDataState.strategy = {
    ...asyncDataState.strategy,
    data: strategy,
    loading: false,
    error: null,
  };
  strategyGetDeferred.resolve(strategy);
}

function defaultPaperMocks() {
  paperValidationMock.mockImplementation(() =>
    Promise.resolve({
      strategy_id: "strat-1",
      paper_eligible: true,
      runs: [{ id: "run-1", status: "in_progress", metrics: null }],
      total: 1,
    }),
  );
  paperEligibilityMock.mockResolvedValue({
    strategy_id: "strat-1",
    status: "paper_eligible",
    paper_eligible: true,
    recommendation: "continue",
    testability_score: 80,
    limitations: [],
    eligibility_reasons: [],
    blockers: [],
    real_trading_enabled: false,
    accepted_lessons: [],
    unresolved_lesson_candidates: [],
  });
  schedulerStatusMock.mockResolvedValue({
    env_enabled: false,
    tenant_enabled: false,
    effective_enabled: false,
    config: {
      enabled: false,
      interval_seconds: 300,
      max_runs_per_cycle: 5,
      max_scans_per_minute: 10,
    },
    real_trading_enabled: false,
    limitation: "Paper only",
  });
  schedulerHistoryMock.mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0 });
  paperSignalsMock.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  paperTradesMock.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  alertsListMock.mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0 });
}

beforeEach(() => {
  useParamsMock.mockReturnValue({ id: "strat-1" });
  asyncDataState.strategy = {
    data: null,
    loading: true,
    error: null,
    reload: vi.fn(),
  };
  Object.assign(strategyGetDeferred, deferred<typeof strategy>());
  defaultPaperMocks();
  paperValidationMock.mockClear();
  paperEligibilityMock.mockClear();
  schedulerStatusMock.mockClear();
  schedulerHistoryMock.mockClear();
  paperSignalsMock.mockClear();
  paperTradesMock.mockClear();
  alertsListMock.mockClear();
});

afterEach(() => {
  cleanup();
});

describe("StrategyDetailPage paper-source honesty", () => {
  it("waits for strategy load and calls each paper endpoint once on initial load", async () => {
    const { rerender } = render(<StrategyDetailPage />);

    expect(paperValidationMock).not.toHaveBeenCalled();
    expect(paperEligibilityMock).not.toHaveBeenCalled();

    resolveStrategyLoaded();
    rerender(<StrategyDetailPage />);

    await waitFor(() => {
      expect(paperValidationMock).toHaveBeenCalledTimes(1);
      expect(paperEligibilityMock).toHaveBeenCalledTimes(1);
      expect(schedulerStatusMock).toHaveBeenCalledTimes(1);
      expect(alertsListMock).toHaveBeenCalledTimes(1);
      expect(paperSignalsMock).toHaveBeenCalledTimes(1);
      expect(paperTradesMock).toHaveBeenCalledTimes(1);
      expect(schedulerHistoryMock).toHaveBeenCalledTimes(1);
    });

    rerender(<StrategyDetailPage />);
    expect(paperValidationMock).toHaveBeenCalledTimes(1);
    expect(paperEligibilityMock).toHaveBeenCalledTimes(1);
  });

  it("does not block sibling sources while one endpoint is slow", async () => {
    const slowSummary = deferred<Awaited<ReturnType<typeof paperValidationMock>>>();
    paperValidationMock.mockImplementation(() => slowSummary.promise);
    paperEligibilityMock.mockResolvedValue({
      strategy_id: "strat-1",
      status: "paper_eligible",
      paper_eligible: true,
      recommendation: "continue",
      testability_score: 80,
      limitations: [],
      eligibility_reasons: [],
      blockers: [],
      real_trading_enabled: false,
      accepted_lessons: [],
      unresolved_lesson_candidates: [],
    });

    const { rerender } = render(<StrategyDetailPage />);
    resolveStrategyLoaded();
    rerender(<StrategyDetailPage />);

    const eligibilityCard = await screen.findByTestId("strategy-paper-source-eligibility");
    await waitFor(() => {
      expect(within(eligibilityCard).getByTestId("strategy-paper-source-eligibility-message")).toHaveTextContent(
        /Loaded/i,
      );
    });

    const summaryCard = screen.getByTestId("strategy-paper-source-summary");
    expect(within(summaryCard).getByTestId("strategy-paper-source-summary-message")).toHaveTextContent(
      /Loading/i,
    );

    slowSummary.resolve({
      strategy_id: "strat-1",
      paper_eligible: true,
      runs: [{ id: "run-1", status: "in_progress", metrics: null }],
      total: 1,
    });

    await waitFor(() => {
      expect(within(summaryCard).getByTestId("strategy-paper-source-summary-message")).toHaveTextContent(
        /Loaded/i,
      );
    });
  });

  it("shows failed summary without hiding successful siblings", async () => {
    paperValidationMock.mockRejectedValue(new Error("summary down"));

    const { rerender } = render(<StrategyDetailPage />);
    resolveStrategyLoaded();
    rerender(<StrategyDetailPage />);

    await waitFor(() => {
      expect(screen.getByTestId("strategy-paper-source-summary")).toHaveAttribute(
        "data-source-status",
        "failed",
      );
      expect(screen.getByTestId("strategy-paper-source-eligibility")).toHaveAttribute(
        "data-source-status",
        "ready",
      );
    });

    expect(
      within(screen.getByTestId("strategy-paper-source-summary")).getByTestId(
        "strategy-paper-source-summary-message",
      ),
    ).toHaveTextContent(/summary down/i);
  });

  it("exposes empty states after successful initial load", async () => {
    const { rerender } = render(<StrategyDetailPage />);
    resolveStrategyLoaded();
    rerender(<StrategyDetailPage />);

    await waitFor(() => {
      expect(screen.getByTestId("strategy-paper-source-trades")).toHaveAttribute(
        "data-source-status",
        "empty",
      );
    });
  });

  it("retries only the selected source", async () => {
    const { rerender } = render(<StrategyDetailPage />);
    resolveStrategyLoaded();
    rerender(<StrategyDetailPage />);

    await waitFor(() => {
      expect(screen.getByTestId("strategy-paper-source-eligibility")).toHaveAttribute(
        "data-source-status",
        "ready",
      );
      expect(paperEligibilityMock).toHaveBeenCalledTimes(1);
    });

    paperEligibilityMock.mockRejectedValueOnce(new Error("eligibility down"));
    fireEvent.click(screen.getByTestId("strategy-paper-source-eligibility-retry"));

    await waitFor(
      () => {
        expect(paperEligibilityMock).toHaveBeenCalledTimes(2);
        expect(paperValidationMock).toHaveBeenCalledTimes(1);
      },
      { timeout: 5000 },
    );

    // The mock call count increments before the rejection flushes to state,
    // so the failed status must also be awaited (flaked on slow CI runners).
    await waitFor(
      () => {
        expect(screen.getByTestId("strategy-paper-source-eligibility")).toHaveAttribute(
          "data-source-status",
          "failed",
        );
      },
      { timeout: 5000 },
    );
    expect(screen.getByTestId("strategy-paper-source-summary")).toHaveAttribute(
      "data-source-status",
      "ready",
    );
  });

  it("clears stale snapshots during source-specific retry", async () => {
    const { rerender } = render(<StrategyDetailPage />);
    resolveStrategyLoaded();
    rerender(<StrategyDetailPage />);

    await waitFor(() => {
      expect(screen.getByTestId("strategy-paper-source-eligibility")).toHaveAttribute(
        "data-source-status",
        "ready",
      );
      expect(paperEligibilityMock).toHaveBeenCalledTimes(1);
    });

    const retryDeferred = deferred<Awaited<ReturnType<typeof paperEligibilityMock>>>();
    paperEligibilityMock.mockImplementationOnce(() => retryDeferred.promise);

    fireEvent.click(screen.getByTestId("strategy-paper-source-eligibility-retry"));

    await waitFor(
      () => {
        expect(paperEligibilityMock).toHaveBeenCalledTimes(2);
        expect(screen.getByTestId("strategy-paper-source-eligibility")).toHaveAttribute(
          "data-source-status",
          "loading",
        );
        expect(
          screen.getByTestId("strategy-paper-source-eligibility-stale"),
        ).toBeInTheDocument();
      },
      { timeout: 5000 },
    );

    retryDeferred.resolve({
      strategy_id: "strat-1",
      status: "paper_eligible",
      paper_eligible: true,
      recommendation: "continue",
      testability_score: 80,
      limitations: [],
      eligibility_reasons: [],
      blockers: [],
      real_trading_enabled: false,
      accepted_lessons: [],
      unresolved_lesson_candidates: [],
    });

    await waitFor(
      () => {
        expect(screen.getByTestId("strategy-paper-source-eligibility")).toHaveAttribute(
          "data-source-status",
          "ready",
        );
        expect(
          screen.queryByTestId("strategy-paper-source-eligibility-stale"),
        ).not.toBeInTheDocument();
      },
      { timeout: 5000 },
    );
  });

  it("shows waiting state for dependent sources until summary resolves", async () => {
    const slowSummary = deferred<Awaited<ReturnType<typeof paperValidationMock>>>();
    paperValidationMock.mockImplementation(() => slowSummary.promise);

    const { rerender } = render(<StrategyDetailPage />);
    resolveStrategyLoaded();
    rerender(<StrategyDetailPage />);

    const signalsCard = await screen.findByTestId("strategy-paper-source-signals");
    expect(within(signalsCard).getByTestId("strategy-paper-source-signals-message")).toHaveTextContent(
      /Waiting for paper validation summary/i,
    );

    slowSummary.resolve({
      strategy_id: "strat-1",
      paper_eligible: true,
      runs: [{ id: "run-1", status: "in_progress", metrics: null }],
      total: 1,
    });

    await waitFor(() => {
      expect(screen.getByTestId("strategy-paper-source-signals")).toHaveAttribute(
        "data-source-status",
        "empty",
      );
    });
  });

  it("ignores stale responses after route id changes", async () => {
    const slowSummary = deferred<Awaited<ReturnType<typeof paperValidationMock>>>();
    paperValidationMock.mockImplementation(() => slowSummary.promise);

    const { rerender } = render(<StrategyDetailPage />);
    resolveStrategyLoaded();
    rerender(<StrategyDetailPage />);

    await screen.findByTestId("strategy-paper-source-summary");

    useParamsMock.mockReturnValue({ id: "strat-2" });
    asyncDataState.strategy = {
      data: { ...strategy, id: "strat-2", name: "Other Strategy" },
      loading: false,
      error: null,
      reload: vi.fn(),
    };
    paperValidationMock.mockResolvedValue({
      strategy_id: "strat-2",
      paper_eligible: true,
      runs: [{ id: "run-2", status: "in_progress", metrics: null }],
      total: 1,
    });

    rerender(<StrategyDetailPage />);

    await waitFor(() => {
      expect(paperValidationMock).toHaveBeenCalledTimes(2);
    });

    slowSummary.resolve({
      strategy_id: "strat-1",
      paper_eligible: true,
      runs: [{ id: "run-stale", status: "in_progress", metrics: null }],
      total: 1,
    });

    await waitFor(() => {
      expect(screen.getByTestId("strategy-paper-source-summary")).toHaveAttribute(
        "data-source-status",
        "ready",
      );
    });

    expect(
      within(screen.getByTestId("strategy-paper-source-summary")).getByTestId(
        "strategy-paper-source-summary-message",
      ),
    ).not.toHaveTextContent(/run-stale/i);
  });
});
