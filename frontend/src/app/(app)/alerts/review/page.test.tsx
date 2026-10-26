import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SetupAlertReviewPage from "./page";
import { expectNoIdentifiers } from "@/test/no-identifiers";

const navigationState = vi.hoisted(() => ({
  search: new URLSearchParams(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigationState.replace, push: vi.fn() }),
  useSearchParams: () => navigationState.search,
}));

const { sampleAlert, watchingAlert, summary } = vi.hoisted(() => ({
  sampleAlert: {
    alert_id: "alert-1",
    created_at: "2026-06-28T12:00:00Z",
    symbol: "BTCUSDT",
    timeframe: "15m",
    condition: "order_block",
    direction: "long",
    confidence: 0.88,
    reason: "Bullish order block retest.",
    trigger_level: 65000,
    invalidation_level: 64000,
    latest_price: 65100,
    delivery_channel: "in_app",
    delivery_status: "disabled",
    dedupe_key: "dedupe-1",
    review_status: "unreviewed" as const,
    review_notes: null,
    reviewed_at: null,
    reviewed_by: null,
    metadata: { source: "market_watcher" },
  },
  watchingAlert: {
    alert_id: "alert-2",
    created_at: "2026-06-28T12:00:00Z",
    symbol: "ETHUSDT",
    timeframe: "1h",
    condition: "breakout_retest",
    direction: "short",
    confidence: 0.75,
    reason: "Retest holding.",
    trigger_level: 3500,
    invalidation_level: 3600,
    latest_price: 3490,
    delivery_channel: "in_app",
    delivery_status: "disabled",
    dedupe_key: "dedupe-2",
    review_status: "watching" as const,
    review_notes: "Watching closely",
    reviewed_at: "2026-06-28T12:00:00Z",
    reviewed_by: "user-1",
    metadata: { source: "market_watcher" },
  },
  summary: {
    total_unreviewed: 1,
    total_watching: 0,
    total_important: 0,
    total_ignored: 0,
    by_condition: { order_block: 1 },
    by_symbol: { BTCUSDT: 1 },
    latest_created_at: "2026-06-28T12:00:00Z",
    highest_confidence_alerts: [
      {
        alert_id: "alert-1",
        symbol: "BTCUSDT",
        condition: "order_block",
        confidence: 0.88,
        created_at: "2026-06-28T12:00:00Z",
      },
    ],
  },
}));

let hookCall = 0;

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: () => {
    hookCall += 1;
    const index = (hookCall - 1) % 2;
    if (index === 1) {
      return {
        data: summary,
        loading: false,
        error: null,
        reload: vi.fn(),
      };
    }
    return {
      data: { items: [sampleAlert, watchingAlert], total: 2, limit: 50, offset: 0 },
      loading: false,
      error: null,
      reload: vi.fn(),
    };
  },
}));

vi.mock("@/lib/api", () => ({
  api: {
    alerts: {
      setupReview: vi.fn(),
      setupReviewSummary: vi.fn(),
      updateSetupReview: vi.fn().mockResolvedValue({
        ...sampleAlert,
        review_status: "watching",
      }),
      createSetupDraft: vi.fn().mockResolvedValue({
        already_exists: false,
        draft: {
          draft_id: "draft-1",
          source_alert_id: "alert-2",
          symbol: "ETHUSDT",
          timeframe: "1h",
          condition: "breakout_retest",
          direction: "short",
          confidence: 0.75,
          trigger_level: 3500,
          invalidation_level: 3600,
          latest_price: 3490,
          reason: "Retest holding.",
          risk_mode: "conservative",
          status: "draft",
          created_at: "2026-06-28T12:00:00Z",
          created_by: "user-1",
        },
      }),
    },
  },
}));

describe("SetupAlertReviewPage Slice 77 / Phase C2 deep links", () => {
  beforeEach(async () => {
    hookCall = 0;
    navigationState.search = new URLSearchParams();
    navigationState.replace.mockReset();
    const { api } = await import("@/lib/api");
    vi.mocked(api.alerts.updateSetupReview).mockClear();
    vi.mocked(api.alerts.createSetupDraft).mockClear();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders review page, filters, and alert card fields with no query", () => {
    render(<SetupAlertReviewPage />);
    const firstCard = screen.getByTestId("setup-alert-alert-1");

    expect(screen.getByTestId("setup-alert-review-page")).toBeInTheDocument();
    expect(screen.getByText("Setup Alert Review")).toBeInTheDocument();
    expect(screen.getByTestId("setup-alert-review-filters")).toBeInTheDocument();
    expect(within(firstCard).getByTestId("setup-alert-condition")).toHaveTextContent("Order block");
    expect(within(firstCard).getByTestId("setup-alert-confidence")).toHaveTextContent("88%");
    expect(within(firstCard).getByTestId("setup-alert-reason")).toHaveTextContent(
      "Bullish order block retest.",
    );
    expect(within(firstCard).getByTestId("setup-alert-trigger")).toHaveTextContent("65,000");
    expect(within(firstCard).getByTestId("setup-alert-invalidation")).toHaveTextContent("64,000");
    expect(within(firstCard).getByTestId("setup-alert-latest-price")).toHaveTextContent("65,100");
    expect(
      screen.getByText(/never sends Telegram messages or places orders/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/send to telegram/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/place order/i)).not.toBeInTheDocument();
  });

  it("updates review status via save action", async () => {
    const { api } = await import("@/lib/api");
    render(<SetupAlertReviewPage />);
    const firstCard = screen.getByTestId("setup-alert-alert-1");

    fireEvent.change(within(firstCard).getByTestId("setup-alert-review-status"), {
      target: { value: "watching" },
    });
    fireEvent.click(within(firstCard).getByTestId("setup-alert-save"));

    await waitFor(() => {
      expect(api.alerts.updateSetupReview).toHaveBeenCalledWith("alert-1", {
        review_status: "watching",
        review_notes: null,
      });
    });
  });

  it("supports quick review actions", async () => {
    const { api } = await import("@/lib/api");
    render(<SetupAlertReviewPage />);
    const firstCard = screen.getByTestId("setup-alert-alert-1");

    fireEvent.click(within(firstCard).getByTestId("quick-action-important"));

    await waitFor(() => {
      expect(api.alerts.updateSetupReview).toHaveBeenCalledWith("alert-1", {
        review_status: "important",
        review_notes: null,
      });
    });
  });

  it("shows create paper draft only for watching or important alerts", () => {
    render(<SetupAlertReviewPage />);

    expect(within(screen.getByTestId("setup-alert-alert-1")).queryByTestId("setup-alert-create-draft")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("setup-alert-alert-2")).getByTestId("setup-alert-create-draft")).toBeInTheDocument();
  });

  it("requires confirmation and shows draft-only warning", async () => {
    const { api } = await import("@/lib/api");
    render(<SetupAlertReviewPage />);
    const watchingCard = screen.getByTestId("setup-alert-alert-2");

    fireEvent.click(within(watchingCard).getByTestId("setup-alert-create-draft"));
    expect(within(watchingCard).getByTestId("setup-alert-draft-warning")).toHaveTextContent(
      "Draft only. No order. No Telegram. No execution.",
    );

    fireEvent.change(within(watchingCard).getByTestId("setup-alert-draft-confirm"), {
      target: { value: "CREATE_PAPER_VALIDATION_DRAFT" },
    });
    fireEvent.click(within(watchingCard).getByTestId("setup-alert-draft-submit"));

    await waitFor(() => {
      expect(api.alerts.createSetupDraft).toHaveBeenCalledWith("alert-2", {
        confirm: "CREATE_PAPER_VALIDATION_DRAFT",
        notes: null,
        risk_mode: "conservative",
      });
      expect(within(watchingCard).getByTestId("setup-alert-draft-link")).toHaveTextContent(
        "View draft",
      );
    });
  });

  it("highlights a valid source-alert query without mutating review status", async () => {
    const { api } = await import("@/lib/api");
    navigationState.search = new URLSearchParams("alert=alert-1");
    render(<SetupAlertReviewPage />);

    const card = screen.getByTestId("setup-alert-alert-1");
    expect(card).toHaveAttribute("data-highlighted", "true");
    expect(screen.getByTestId("setup-alert-deep-link-active")).toBeInTheDocument();
    expect(screen.getByTestId("setup-alert-alert-2")).toHaveAttribute("data-highlighted", "false");
    expect(api.alerts.updateSetupReview).not.toHaveBeenCalled();
    expectNoIdentifiers();
  });

  it("shows a missing-alert notice for an unavailable source-alert query", () => {
    navigationState.search = new URLSearchParams("alert=missing-alert");
    render(<SetupAlertReviewPage />);

    expect(screen.getByTestId("setup-alert-deep-link-missing")).toHaveTextContent(
      /not found or is no longer available/i,
    );
    expect(screen.getByTestId("setup-alert-clear-deep-link")).toBeInTheDocument();
    expect(screen.getByTestId("setup-alert-alert-1")).toHaveAttribute("data-highlighted", "false");
  });

  it("clears a stale source-alert query", () => {
    navigationState.search = new URLSearchParams("alert=missing-alert");
    render(<SetupAlertReviewPage />);

    fireEvent.click(screen.getByTestId("setup-alert-clear-deep-link"));
    expect(navigationState.replace).toHaveBeenCalledWith("/alerts/review");
  });

  it("keeps filters working and clears the alert query when filters change", () => {
    navigationState.search = new URLSearchParams("alert=alert-1");
    render(<SetupAlertReviewPage />);

    fireEvent.change(screen.getByLabelText("Filter by symbol"), {
      target: { value: "ETHUSDT" },
    });
    expect(navigationState.replace).toHaveBeenCalledWith("/alerts/review");
    expect(screen.getByTestId("setup-alert-review-filters")).toBeInTheDocument();
  });
});
