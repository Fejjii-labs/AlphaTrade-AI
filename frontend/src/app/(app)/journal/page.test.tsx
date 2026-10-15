import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SourceResult } from "@/components/workflows/sourceResult";
import type { JournalEntry, PaginatedJournalEntries, PaginatedPositions, Position } from "@/lib/api/types";

import JournalPage from "./page";

const safetyPosture = {
  executionMode: "paper" as string | null,
  realTradingEnabled: false as boolean | null,
  providerMode: "fallback",
};

const search = new URLSearchParams();

vi.mock("@/components/journal/JournalTradeScreen", () => ({ JournalTradeScreen: ({ tradeId }: { tradeId: string }) => <div data-testid="exact-trade-view">{tradeId}</div> }));

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "fixture-user" }, organization: { id: "fixture-org" } }) }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/journal",
  useSearchParams: () => search,
}));

vi.mock("@/contexts/AppContext", () => ({
  useAppContext: () => ({
    killSwitchActive: false,
    killSwitchBusy: false,
    killSwitchError: null,
    setKillSwitchActive: vi.fn(),
  }),
  useSafetyPosture: () => safetyPosture,
}));

vi.mock("@/contexts/ShellFreshnessContext", () => ({
  useShellFreshness: () => ({
    freshness: { state: null },
    setFreshness: vi.fn(),
    clearFreshness: vi.fn(),
  }),
}));

function ok<T>(data: T): SourceResult<T> {
  return { data, available: true, error: null, fallbackUsed: false };
}

function failed<T>(error = "down"): SourceResult<T> {
  return { data: null, available: false, error, fallbackUsed: false };
}

function makePosition(overrides: Partial<Position> & { id: string; symbol: string }): Position {
  return {
    organization_id: "org",
    user_id: "user",
    direction: "long",
    size: "1",
    entry_price: "100",
    leverage: "1",
    take_profits: [],
    unrealized_pnl: "0",
    realized_pnl: "25",
    risk_state: {},
    status: "closed",
    opened_at: "2026-07-20T10:00:00.000Z",
    closed_at: "2026-07-21T10:00:00.000Z",
    ...overrides,
  };
}

function makeEntry(overrides: Partial<JournalEntry> & { id: string; symbol: string }): JournalEntry {
  return {
    organization_id: "org",
    user_id: "user",
    timeframe: "1h",
    direction: "long",
    entry_rationale: "Followed the plan with patience.",
    emotions: ["calm"],
    mistakes: [],
    result: "win",
    tags: [],
    screenshot_refs: [],
    created_at: "2026-07-21T12:00:00.000Z",
    ...overrides,
  };
}

const closedPosition = makePosition({ id: "pos-needs", symbol: "BTCUSDT" });
const journaledPosition = makePosition({
  id: "pos-done",
  symbol: "ETHUSDT",
  closed_at: "2026-07-20T12:00:00.000Z",
});
const recentEntry = makeEntry({
  id: "entry-1",
  symbol: "ETHUSDT",
  linked_position_id: "pos-done",
  linked_proposal_id: "prop-1",
});

let asyncState = {
  data: {
    entries: ok<PaginatedJournalEntries>({
      items: [recentEntry],
      total: 1,
      limit: 50,
      offset: 0,
    }),
    closedPositions: ok<PaginatedPositions>({
      items: [closedPosition, journaledPosition],
      total: 2,
      limit: 50,
      offset: 0,
    }),
  } as {
    entries: SourceResult<PaginatedJournalEntries>;
    closedPositions: SourceResult<PaginatedPositions>;
  } | null,
  loading: false,
  error: null as string | null,
  reload: vi.fn(),
};

const prefillMock = vi.fn();
const createMock = vi.fn();
const getRunSessionMock = vi.fn();
const analyzeMock = vi.fn();
const deleteMock = vi.fn();
const createLessonMock = vi.fn();

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: () => asyncState,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      journal: {
        ...actual.api.journal,
        prefill: (...args: unknown[]) => prefillMock(...args),
        create: (...args: unknown[]) => createMock(...args),
        delete: (...args: unknown[]) => deleteMock(...args),
      },
      journalDiscipline: {
        analyze: (...args: unknown[]) => analyzeMock(...args),
      },
      lessons: {
        ...actual.api.lessons,
        createCandidate: (...args: unknown[]) => createLessonMock(...args),
      },
      strategies: {
        ...actual.api.strategies,
        getRunSession: (...args: unknown[]) => getRunSessionMock(...args),
      },
    },
  };
});

function resetAsyncState() {
  asyncState = {
    data: {
      entries: ok({
        items: [recentEntry],
        total: 1,
        limit: 50,
        offset: 0,
      }),
      closedPositions: ok({
        items: [closedPosition, journaledPosition],
        total: 2,
        limit: 50,
        offset: 0,
      }),
    },
    loading: false,
    error: null,
    reload: vi.fn(),
  };
}

beforeEach(() => {
  resetAsyncState();
  search.set("view", "record");
  safetyPosture.executionMode = "paper";
  safetyPosture.realTradingEnabled = false;
  for (const key of [
    "proposal_id",
    "position_id",
    "entry",
    "trade_id",
    "session_id",
    "run_session_id",
  ]) {
    search.delete(key);
  }
  prefillMock.mockReset();
  createMock.mockReset();
  getRunSessionMock.mockReset();
  analyzeMock.mockReset();
  deleteMock.mockReset();
  createLessonMock.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("Journal hub Phase C3A", () => {
  it("renders journal hub loading state", () => {
    asyncState = { data: null, loading: true, error: null, reload: vi.fn() };
    render(<JournalPage />);
    expect(screen.getByText(/Loading Journal hub/i)).toBeInTheDocument();
  });

  it("renders hub sections with confirmed PAPER posture", () => {
    render(<JournalPage />);
    expect(screen.getByTestId("journal-hub-page")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Journal" })).toBeInTheDocument();
    expect(screen.getByTestId("needs-journaling-queue")).toBeInTheDocument();
    expect(screen.getByTestId("recent-journal-entries")).toBeInTheDocument();
    expect(screen.getByTestId("journal-quick-entry")).toBeInTheDocument();
    expect(screen.getByTestId("journal-source-availability")).toBeInTheDocument();
    expect(screen.getByTestId("paper-mode-indicator")).toHaveAttribute(
      "aria-label",
      "Paper mode active",
    );
  });

  it("shows honest empty recent entries without treating failure as empty", () => {
    asyncState.data = {
      entries: ok({ items: [], total: 0, limit: 50, offset: 0 }),
      closedPositions: ok({ items: [], total: 0, limit: 50, offset: 0 }),
    };
    render(<JournalPage />);
    expect(screen.getByTestId("recent-entries-empty")).toBeInTheDocument();
    expect(screen.getByTestId("needs-journaling-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("recent-entries-unavailable")).not.toBeInTheDocument();
  });

  it("keeps failed journal entries distinct from empty success", () => {
    asyncState.data = {
      entries: failed("entries down"),
      closedPositions: ok({ items: [closedPosition], total: 1, limit: 50, offset: 0 }),
    };
    render(<JournalPage />);
    expect(screen.getByTestId("journal-hub-partial")).toBeInTheDocument();
    expect(screen.getByTestId("recent-entries-unavailable")).toHaveTextContent(/not an empty journal/i);
    expect(screen.getByTestId("needs-journaling-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("needs-journaling-count-unavailable")).toHaveTextContent(
      /count unavailable/i,
    );
    expect(screen.queryByTestId("needs-journaling-count")).not.toBeInTheDocument();
    expect(screen.queryByTestId("recent-entries-empty")).not.toBeInTheDocument();
  });

  it("renders needs-journaling queue from closed positions without linked entries", () => {
    render(<JournalPage />);
    expect(screen.getByTestId("needs-journaling-count")).toHaveTextContent("1 need journaling");
    expect(screen.getByTestId("needs-journaling-item-pos-needs")).toHaveTextContent("BTCUSDT");
    expect(screen.getByRole("link", { name: /Journal this trade/i })).toHaveAttribute(
      "href",
      "/journal?position_id=pos-needs",
    );
  });

  it("renders recent entries with related plan only when identifier exists", () => {
    render(<JournalPage />);
    expect(screen.getByTestId("recent-entry-entry-1")).toHaveTextContent("ETHUSDT");
    expect(screen.getByTestId("related-plan-entry-1")).toHaveAttribute("href", "/proposals?id=prop-1");
    expect(screen.getByTestId("related-position-entry-1")).toBeInTheDocument();
    expect(screen.queryByTestId("quick-entry-related-validation")).not.toBeInTheDocument();
  });

  it("shows unverified queue when journal entries are truncated", () => {
    asyncState.data = {
      entries: ok({
        items: [recentEntry],
        total: 120,
        limit: 50,
        offset: 0,
      }),
      closedPositions: ok({
        items: [closedPosition, journaledPosition],
        total: 2,
        limit: 50,
        offset: 0,
      }),
    };
    render(<JournalPage />);
    expect(screen.getByTestId("needs-journaling-coverage")).toHaveTextContent(
      /only 1 of 120 journal entries are loaded/i,
    );
    expect(screen.queryByTestId("needs-journaling-count")).not.toBeInTheDocument();
    expect(screen.getByTestId("needs-journaling-count-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("needs-journaling-item-pos-needs")).toHaveAttribute(
      "data-verification",
      "unverified",
    );
    expect(
      screen.getByTestId("needs-journaling-unverified-label-pos-needs"),
    ).toHaveTextContent(/Possibly needs journaling/i);
    expect(screen.queryByRole("link", { name: /Journal this trade/i })).not.toBeInTheDocument();
    expect(screen.getByTestId("needs-journaling-possible-action-pos-needs")).toBeInTheDocument();
  });

  it("clears related context while prefill reloads for a new position", async () => {
    search.set("position_id", "pos-needs");
    prefillMock.mockResolvedValue({
      symbol: "BTCUSDT",
      timeframe: "1h",
      direction: "short",
      strategy_id: "htf_trend_pullback",
      entry_rationale: "Position A rationale",
      linked_proposal_id: null,
      linked_position_id: "pos-needs",
      tags: [],
    });
    const view = render(<JournalPage />);
    await waitFor(() => {
      expect(screen.getByTestId("quick-entry-related-position")).toBeInTheDocument();
    });

    view.unmount();
    search.set("position_id", "pos-other");
    prefillMock.mockImplementation(
      () =>
        new Promise(() => {
          /* keep loading */
        }),
    );
    render(<JournalPage />);

    expect(screen.getByTestId("journal-prefill-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("quick-entry-related-position")).not.toBeInTheDocument();
  });

  it("does not send stale linked IDs after invalid position prefill", async () => {
    search.set("position_id", "pos-needs");
    prefillMock.mockResolvedValueOnce({
      symbol: "BTCUSDT",
      timeframe: "1h",
      direction: "short",
      entry_rationale: "Position A rationale",
      linked_proposal_id: null,
      linked_position_id: "pos-needs",
      tags: [],
    });
    const first = render(<JournalPage />);
    await waitFor(() => {
      expect(screen.getByTestId("quick-entry-related-position")).toBeInTheDocument();
    });

    first.unmount();
    search.set("position_id", "missing-pos");
    let rejectPrefill!: (reason: Error) => void;
    prefillMock.mockReturnValueOnce(new Promise<never>((_resolve, reject) => {
      rejectPrefill = reject;
    }));
    createMock.mockResolvedValueOnce(makeEntry({
      id: "manual-after-invalid",
      symbol: "BTCUSDT",
      result: "open",
      entry_rationale: "Manual after invalid context.",
    }));
    const second = render(<JournalPage />);
    // React's passive resets may still be pending when this warning first commits.
    // Enter immediately at that boundary, rather than after waitFor drains effects.
    const observer = new MutationObserver(() => {
      if (!second.container.querySelector('[data-testid="journal-prefill-invalid"]')) return;
      observer.disconnect();
      fireEvent.change(screen.getByLabelText(/What happened versus plan/i), {
        target: { value: "Manual after invalid context." },
      });
      fireEvent.click(screen.getByRole("button", { name: /Save journal entry/i }));
    });
    observer.observe(second.container, { childList: true, subtree: true });
    try {
      rejectPrefill(new Error("Position not found"));
      await waitFor(() => {
        expect(createMock).toHaveBeenCalledTimes(1);
        expect(screen.getByTestId("journal-save-success")).toBeInTheDocument();
      });
    } finally {
      observer.disconnect();
    }
    expect(screen.getByTestId("journal-prefill-invalid")).toBeInTheDocument();
    expect(screen.queryByTestId("quick-entry-related-position")).not.toBeInTheDocument();
    const payload = createMock.mock.calls.at(-1)?.[0];
    expect(payload.entry_rationale).toBe("Manual after invalid context.");
    expect(payload.linked_position_id).toBeUndefined();
    expect(payload.linked_proposal_id).toBeUndefined();
  });

  it("prefills quick entry from valid proposal/position context", async () => {
    search.set("position_id", "pos-needs");
    prefillMock.mockResolvedValue({
      symbol: "BTCUSDT",
      timeframe: "1h",
      direction: "short",
      strategy_id: "htf_trend_pullback",
      entry_rationale: "Paper position short BTCUSDT size 1 @ 100",
      linked_proposal_id: null,
      linked_position_id: "pos-needs",
      tags: [],
    });
    render(<JournalPage />);
    await waitFor(() => {
      expect(screen.getByLabelText(/What happened versus plan/i)).toHaveValue(
        "Paper position short BTCUSDT size 1 @ 100",
      );
    });
    expect(screen.getByLabelText(/^Symbol$/i)).toHaveValue("BTCUSDT");
    expect(screen.getByLabelText(/^Direction$/i)).toHaveValue("short");
    expect(screen.getByTestId("quick-entry-related-position")).toBeInTheDocument();
    expect(prefillMock).toHaveBeenCalledWith({
      linked_proposal_id: undefined,
      linked_position_id: "pos-needs",
    });
  });

  it("surfaces invalid or stale prefill context without silent unrelated linking", async () => {
    search.set("proposal_id", "missing-prop");
    prefillMock.mockRejectedValue(new Error("Trade proposal not found"));
    render(<JournalPage />);
    await waitFor(() => {
      expect(screen.getByTestId("journal-prefill-invalid")).toHaveTextContent(/invalid or stale/i);
    });
    expect(screen.queryByTestId("quick-entry-related-plan")).not.toBeInTheDocument();
  });

  it("rejects stale entry deep links instead of opening an unrelated record", () => {
    search.set("entry", "missing-entry");
    render(<JournalPage />);
    expect(screen.getByTestId("journal-stale-entry")).toHaveTextContent(
      /was not found in the most recent 50 journal entries/i,
    );
    expect(screen.queryByTestId("recent-entry-missing-entry")).not.toBeInTheDocument();
  });

  it("requires two-step confirmation before deleting a journal entry", async () => {
    deleteMock.mockResolvedValue(undefined);
    render(<JournalPage />);
    fireEvent.click(screen.getByTestId("delete-entry-entry-1"));
    expect(deleteMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("delete-confirm-entry-1")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("cancel-delete-entry-1"));
    expect(screen.queryByTestId("delete-confirm-entry-1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("delete-entry-entry-1"));
    fireEvent.click(screen.getByTestId("confirm-delete-entry-1"));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("entry-1"));
    expect(asyncState.reload).toHaveBeenCalled();
  });

  it("surfaces delete mutation failures as a visible alert", async () => {
    deleteMock.mockRejectedValue(new Error("Delete blocked"));
    render(<JournalPage />);
    fireEvent.click(screen.getByTestId("delete-entry-entry-1"));
    fireEvent.click(screen.getByTestId("confirm-delete-entry-1"));
    await waitFor(() => {
      expect(screen.getByTestId("journal-mutation-error")).toHaveTextContent(/Delete entry failed/i);
    });
    expect(screen.getByTestId("recent-entry-entry-1")).toBeInTheDocument();
  });

  it("surfaces create-lesson mutation failures as a visible alert", async () => {
    analyzeMock.mockResolvedValue({
      comparison: {
        trade_id: "entry-1",
        plan_adherence_score: 40,
        plan_adherence: {
          entry_followed_plan: 1,
          size_respected_risk: 1,
          stop_loss_respected: 1,
          profit_taking_followed: 0,
          emotion_controlled: 1,
          journal_completed: 1,
        },
        emotion_tags: [],
        notes: [],
        missed_runner: {
          early_exit_flag: true,
          recommended_lesson: "Hold the runner longer.",
        },
      },
      lesson_candidate_ids: [],
    });
    createLessonMock.mockRejectedValue(new Error("Lesson create blocked"));
    render(<JournalPage />);
    fireEvent.click(screen.getByRole("button", { name: /Discipline analysis/i }));
    await waitFor(() => expect(screen.getByTestId("discipline-panel")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Create lesson candidate/i }));
    await waitFor(() => {
      expect(screen.getByTestId("journal-mutation-error")).toHaveTextContent(/Create lesson failed/i);
    });
  });

  it("routes trade_id to the exact canonical detail rather than the generic hub", () => {
    search.set("trade_id", "trade-abc");
    render(<JournalPage />);
    expect(screen.getByTestId("exact-trade-view")).toHaveTextContent("trade-abc");
    expect(screen.queryByTestId("quick-entry-related-validation")).not.toBeInTheDocument();
  });

  it("shows related validation only when session context verifies", async () => {
    search.set("session_id", "sess-1");
    getRunSessionMock.mockResolvedValue({ session_id: "sess-1" });
    render(<JournalPage />);
    await waitFor(() => {
      expect(screen.getByTestId("quick-entry-related-validation")).toHaveAttribute(
        "href",
        "/paper-validation/run-sessions/sess-1",
      );
    });
    expect(screen.getByTestId("quick-entry-related-validation")).toHaveTextContent(/not stored/i);
  });

  it("shows invalid session context clearly", async () => {
    search.set("run_session_id", "bad-sess");
    getRunSessionMock.mockRejectedValue(new Error("Run session not found"));
    render(<JournalPage />);
    await waitFor(() => {
      expect(screen.getByTestId("journal-session-invalid")).toHaveTextContent(/invalid or stale/i);
    });
    expect(screen.queryByTestId("quick-entry-related-validation")).not.toBeInTheDocument();
  });

  it("saves a journal entry successfully and shows next safe action", async () => {
    createMock.mockResolvedValue(
      makeEntry({
        id: "entry-new",
        symbol: "BTCUSDT",
        direction: "long",
        result: "open",
        linked_proposal_id: null,
      }),
    );
    render(<JournalPage />);
    fireEvent.change(screen.getByLabelText(/What happened versus plan/i), {
      target: { value: "Held to plan invalidation." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save journal entry/i }));
    await waitFor(() => {
      expect(screen.getByTestId("journal-save-success")).toBeInTheDocument();
    });
    expect(createMock).toHaveBeenCalledTimes(1);
    expect(createMock.mock.calls[0]?.[0]).toMatchObject({
      symbol: "BTCUSDT",
      entry_rationale: "Held to plan invalidation.",
    });
    expect(asyncState.reload).toHaveBeenCalled();
    expect(screen.getByTestId("journal-save-success")).toHaveTextContent(/Next safe actions/i);
  });

  it("surfaces failed save without claiming success", async () => {
    createMock.mockRejectedValue(new Error("Create failed"));
    render(<JournalPage />);
    fireEvent.change(screen.getByLabelText(/What happened versus plan/i), {
      target: { value: "Attempted save." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save journal entry/i }));
    await waitFor(() => {
      expect(screen.getByTestId("journal-save-error")).toHaveTextContent(/Create failed/i);
    });
    expect(screen.queryByTestId("journal-save-success")).not.toBeInTheDocument();
  });

  it("shows unverified posture without claiming PAPER only", () => {
    safetyPosture.executionMode = null;
    safetyPosture.realTradingEnabled = null;
    render(<JournalPage />);
    expect(screen.getByTestId("paper-mode-indicator")).toHaveAttribute(
      "aria-label",
      "Paper mode not confirmed",
    );
  });

  it("keeps existing journal routes reachable from hub navigation", () => {
    render(<JournalPage />);
    const nav = screen.getByRole("navigation", { name: "Journal hub sections" });
    expect(within(nav).getByRole("link", { name: "Import" })).toHaveAttribute(
      "href",
      "/journal/import",
    );
    expect(within(nav).getByRole("link", { name: "Lessons" })).toHaveAttribute("href", "/lessons");
    expect(within(nav).getByRole("link", { name: "Knowledge" })).toHaveAttribute(
      "href",
      "/knowledge",
    );
    expect(within(nav).getByRole("link", { name: "Statistics" })).toHaveAttribute(
      "href",
      "/journal/statistics",
    );
    expect(within(nav).getByRole("link", { name: "Human vs System" })).toHaveAttribute(
      "href",
      "/journal/comparison",
    );
  });

  it("uses a one-column quick-entry structure suitable for 390px widths", () => {
    const { container } = render(<JournalPage />);
    const form = screen.getByTestId("journal-quick-entry-form");
    expect(form.className).toMatch(/space-y-3/);
    expect(container.querySelector('[data-testid="journal-hub-page"]')?.className).toMatch(
      /pb-24/,
    );
    expect(screen.getByRole("button", { name: /Save journal entry/i })).toBeInTheDocument();
    // Grid collapses to one column below md; structure remains single-flow.
    expect(form.querySelector(".md\\:grid-cols-2")).toBeTruthy();
  });
});
