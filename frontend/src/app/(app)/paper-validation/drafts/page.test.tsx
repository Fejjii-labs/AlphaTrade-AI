import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SourceResult } from "@/components/workflows/sourceResult";

import PaperValidationDraftsPage from "./page";
import { expectNoIdentifiers } from "@/test/no-identifiers";

vi.mock("@/contexts/AppContext", () => ({
  useAppContext: () => ({ killSwitchActive: false }),
  useSafetyPosture: () => ({
    executionMode: "paper",
    realTradingEnabled: false,
    providerMode: "fallback",
  }),
}));

vi.mock("@/contexts/ShellFreshnessContext", () => ({
  useShellFreshness: () => ({
    freshness: { state: null },
    setFreshness: vi.fn(),
    clearFreshness: vi.fn(),
  }),
}));

const sampleDraft = {
  draft_id: "draft-1",
  source_alert_id: "5f1c2d3e-4a5b-4c6d-8e7f-9a0b1c2d3e4f",
  symbol: "BTCUSDT",
  timeframe: "15m",
  condition: "order_block",
  direction: "long",
  confidence: 0.88,
  trigger_level: 65000,
  invalidation_level: 64000,
  latest_price: 65100,
  reason: "Bullish order block retest.",
  risk_mode: "conservative" as const,
  status: "draft" as const,
  created_at: "2026-06-28T12:00:00Z",
  created_by: "user-1",
  thesis: "Thesis text",
  entry_criteria: "Entry rules",
  invalidation_criteria: "Invalidation rules",
  risk_notes: "",
  prep_status: "needs_review" as const,
  checklist: {
    trend_checked: true,
    support_resistance_checked: false,
    volume_checked: false,
    risk_reward_checked: true,
    invalidation_checked: true,
    higher_timeframe_checked: true,
    news_or_funding_checked: false,
  },
  prep_completion_score: 60,
  missing_checklist_items: ["support_resistance_checked", "volume_checked", "news_or_funding_checked"],
  is_ready_for_validation: false,
};

function ok<T>(data: T): SourceResult<T> {
  return { data, available: true, error: null, fallbackUsed: false };
}

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: () => ({
    data: {
      drafts: ok({ items: [sampleDraft], total: 1, limit: 50, offset: 0 }),
    },
    loading: false,
    error: null,
    reload: vi.fn(),
  }),
}));

describe("PaperValidationDraftsPage Slice 78 / Phase C2", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders draft list without execution UI", () => {
    render(<PaperValidationDraftsPage />);

    expect(screen.getByTestId("paper-validation-drafts-page")).toBeInTheDocument();
    expect(screen.getByTestId("paper-validation-drafts-list")).toBeInTheDocument();
    expect(screen.getByTestId("paper-draft-draft-1")).toBeInTheDocument();
    expect(screen.getByText(/never place orders/i)).toBeInTheDocument();
    expect(screen.getByText(/Prep: needs_review/i)).toBeInTheDocument();
    expect(screen.getByText(/Score: 60%/i)).toBeInTheDocument();
    expect(screen.getByTestId("paper-draft-next-draft-1")).toHaveTextContent(/missing checklist/i);
    expect(screen.queryByRole("button", { name: /place order/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send to telegram/i })).not.toBeInTheDocument();
    expectNoIdentifiers();
  });
});
