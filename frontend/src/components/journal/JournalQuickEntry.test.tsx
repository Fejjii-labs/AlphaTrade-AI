import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  JournalQuickEntry,
  type JournalPrefillState,
} from "@/components/journal/JournalQuickEntry";
import type { JournalQueryContext } from "@/components/journal/journalContext";

const createMock = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    journal: {
      create: (...args: unknown[]) => createMock(...args),
    },
  },
}));

const emptyContext: JournalQueryContext = {
  proposalId: null,
  positionId: null,
  entryId: null,
  tradeId: null,
  sessionId: null,
};

function readyPrefill(overrides?: Partial<Extract<JournalPrefillState, { status: "ready" }>>) {
  return {
    status: "ready" as const,
    symbol: "ETHUSDT",
    timeframe: "1h",
    direction: "short",
    strategyId: "htf_trend_pullback",
    entryRationale: "Prefilled rationale",
    linkedProposalId: "prop-a",
    linkedPositionId: "pos-a",
    tags: [],
    ...overrides,
  };
}

function renderQuickEntry(
  props: Partial<ComponentProps<typeof JournalQuickEntry>> & {
    context?: JournalQueryContext;
    prefill?: JournalPrefillState;
  } = {},
) {
  const { context = emptyContext, prefill = { status: "idle" }, ...rest } = props;
  return render(
    <JournalQuickEntry
      context={context}
      prefill={prefill}
      relatedSession={{ status: "idle" }}
      onSaved={vi.fn()}
      {...rest}
    />,
  );
}

beforeEach(() => {
  createMock.mockReset();
  createMock.mockResolvedValue({
    id: "saved-1",
    symbol: "ETHUSDT",
    direction: "short",
    result: "open",
    organization_id: "org",
    user_id: "user",
    timeframe: "1h",
    entry_rationale: "Prefilled rationale",
    emotions: [],
    mistakes: [],
    tags: [],
    screenshot_refs: [],
    created_at: "2026-07-27T00:00:00.000Z",
  });
});

afterEach(() => {
  cleanup();
});

describe("JournalQuickEntry prefill relationship integrity", () => {
  it("applies linked IDs only when prefill is ready", async () => {
    renderQuickEntry({
      context: { ...emptyContext, positionId: "pos-a" },
      prefill: readyPrefill(),
    });

    expect(screen.getByTestId("quick-entry-related-plan")).toBeInTheDocument();
    expect(screen.getByTestId("quick-entry-related-position")).toBeInTheDocument();
    expect(screen.getByLabelText(/What happened versus plan/i)).toHaveValue("Prefilled rationale");
  });

  it("clears related links while a new context is loading", () => {
    const { rerender } = renderQuickEntry({
      context: { ...emptyContext, positionId: "pos-a" },
      prefill: readyPrefill(),
    });
    expect(screen.getByTestId("journal-related-context")).toBeInTheDocument();

    rerender(
      <JournalQuickEntry
        context={{ ...emptyContext, positionId: "pos-b" }}
        prefill={{ status: "loading" }}
        relatedSession={{ status: "idle" }}
        onSaved={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("journal-related-context")).not.toBeInTheDocument();
    expect(screen.queryByTestId("quick-entry-related-position")).not.toBeInTheDocument();
  });

  it("clears linked IDs when prefill context is removed", async () => {
    const { rerender } = renderQuickEntry({
      context: { ...emptyContext, positionId: "pos-a" },
      prefill: readyPrefill(),
    });
    expect(screen.getByTestId("quick-entry-related-position")).toBeInTheDocument();

    rerender(
      <JournalQuickEntry
        context={emptyContext}
        prefill={{ status: "idle" }}
        relatedSession={{ status: "idle" }}
        onSaved={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("journal-related-context")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Notes \/ lessons learned/i), {
      target: { value: "Keep this user note" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save journal entry/i }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalled();
    });
    expect(createMock.mock.calls[0]?.[0].linked_position_id).toBeUndefined();
    expect(createMock.mock.calls[0]?.[0].linked_proposal_id).toBeUndefined();
    expect(createMock.mock.calls[0]?.[0].lessons).toBe("Keep this user note");
  });

  it("does not include old linked IDs after invalid position context", async () => {
    const { rerender } = renderQuickEntry({
      context: { ...emptyContext, positionId: "pos-a" },
      prefill: readyPrefill({ linkedPositionId: "pos-a", linkedProposalId: null }),
    });
    expect(screen.getByTestId("quick-entry-related-position")).toBeInTheDocument();

    rerender(
      <JournalQuickEntry
        context={{ ...emptyContext, positionId: "pos-b" }}
        prefill={{
          status: "invalid",
          message: "Position not found",
        }}
        relatedSession={{ status: "idle" }}
        onSaved={vi.fn()}
      />,
    );

    expect(screen.getByTestId("journal-prefill-invalid")).toBeInTheDocument();
    expect(screen.queryByTestId("quick-entry-related-position")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/What happened versus plan/i), {
      target: { value: "Manual entry after invalid context." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save journal entry/i }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalled();
    });
    const payload = createMock.mock.calls[0]?.[0];
    expect(payload.linked_position_id).toBeUndefined();
    expect(payload.linked_proposal_id).toBeUndefined();
  });

  it("replaces stale prefill fields when switching to a new valid position", async () => {
    const { rerender } = renderQuickEntry({
      context: { ...emptyContext, positionId: "pos-a" },
      prefill: readyPrefill({
        symbol: "BTCUSDT",
        direction: "long",
        entryRationale: "Position A rationale",
        linkedPositionId: "pos-a",
        linkedProposalId: null,
      }),
    });
    expect(screen.getByLabelText(/^Symbol$/i)).toHaveValue("BTCUSDT");

    rerender(
      <JournalQuickEntry
        context={{ ...emptyContext, positionId: "pos-b" }}
        prefill={{ status: "loading" }}
        relatedSession={{ status: "idle" }}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("quick-entry-related-position")).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^Symbol$/i)).toHaveValue("BTCUSDT");

    rerender(
      <JournalQuickEntry
        context={{ ...emptyContext, positionId: "pos-b" }}
        prefill={readyPrefill({
          symbol: "SOLUSDT",
          direction: "short",
          entryRationale: "Position B rationale",
          linkedPositionId: "pos-b",
          linkedProposalId: null,
        })}
        relatedSession={{ status: "idle" }}
        onSaved={vi.fn()}
      />,
    );

    expect(screen.getByLabelText(/^Symbol$/i)).toHaveValue("SOLUSDT");
    expect(screen.getByLabelText(/^Direction$/i)).toHaveValue("short");
    expect(screen.getByLabelText(/What happened versus plan/i)).toHaveValue("Position B rationale");
    expect(screen.getByTestId("quick-entry-related-position")).toHaveTextContent("Linked to an open position");
    expect(screen.getByTestId("quick-entry-related-position")).not.toHaveTextContent("pos-b");
    fireEvent.click(screen.getByRole("button", { name: /Save journal entry/i }));
    await waitFor(() => {
      expect(createMock).toHaveBeenCalled();
    });
    expect(createMock.mock.calls[0]?.[0].linked_position_id).toBe("pos-b");
  });

  it("clears proposal links when switching from valid proposal to cleared query", async () => {
    const { rerender } = renderQuickEntry({
      context: { ...emptyContext, proposalId: "prop-a" },
      prefill: readyPrefill({
        linkedProposalId: "prop-a",
        linkedPositionId: null,
      }),
    });
    expect(screen.getByTestId("quick-entry-related-plan")).toBeInTheDocument();

    rerender(
      <JournalQuickEntry
        context={emptyContext}
        prefill={{ status: "idle" }}
        relatedSession={{ status: "idle" }}
        onSaved={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("quick-entry-related-plan")).not.toBeInTheDocument();
  });
});
