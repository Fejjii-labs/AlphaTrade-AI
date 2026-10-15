import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TraderDashboardView } from "./TraderDashboardView";
import { failedSource } from "@/components/workflows/sourceResult";
import { describeSafetyPosture } from "@/components/workflows/safetyPostureDisplay";

import { DailyReviewCard, DailyReviewContent } from "./DailyReviewCard";
import type { DailyReview, ReviewItem } from "@/lib/api/daily-review-types";

const dailyReview = vi.fn();
vi.mock("@/lib/api", () => ({ api: { dashboard: { dailyReview: (...args: unknown[]) => dailyReview(...args) } } }));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ user: null, organization: null }) }));

const source = {
  record_type: "journal_trades", record_id: "trade-1", occurred_at: "2026-10-01T12:00:00Z",
  version: 2, content_hash: "source-hash", upstream_system: "watcher", upstream_event_id: "event-1",
};
const item: ReviewItem = {
  topic: "setups", classification: "fact", code: "watch", text: null,
  sources: [source], candidate_id: "candidate-1", strategy_version_id: "strategy-1",
};
const review: DailyReview = {
  schema_version: "DailyReview/v1", review_id: "review-1", content_hash: "hash",
  organization_id: "org-1", user_id: "user-1", generated_at: "2026-10-01T13:00:00Z",
  window: { day: "2026-10-01", timezone: "UTC", start: "2026-10-01T00:00:00Z", end: "2026-10-02T00:00:00Z" },
  facts: [item, { ...item, topic: "blocked_candidates", code: "blocked_daily_loss" }],
  user_observations: [{ ...item, topic: "journal_entries", classification: "user_observation", text: "My reflection" }],
  system_inference: [{ ...item, topic: "missed_setups", classification: "system_inference", text: "Recorded skip" }],
  research_suggestions: [{ ...item, topic: "lessons", classification: "research_suggestion", text: "Review lesson" }],
  counts: { watcher_activity: 0, setups: 1, paper_opened: 0, paper_closed: 1, blocked_candidates: 1,
    risk_events: 0, journal_entries: 1, mistakes: 0, lessons: 1, missed_setups: 1,
    strategy_observations: 0, data_quality_limitations: 0 },
  daily_pnl: [{ cohort: "paper_execution:account-1", closed_count: 2, measured_count: 1,
    missing_pnl_count: 1, recorded_net_pnl: "3.123456789", complete: false, wins: 1, losses: 0,
    breakeven: 0, minimum_sample: 5, win_rate: null, expectancy: null, sources: [source] }],
  limitations: ["Recording coverage is not guaranteed."], live_executable: false, telegram_delivery: false,
};
function section(label: RegExp) {
  return screen.getByText(label, { selector: "summary" }).parentElement!;
}
describe("Daily Review", () => {
  beforeEach(() => dailyReview.mockReset());
  afterEach(() => cleanup());
  it.each([undefined, null, {}, failedSource("review down")])(
    "renders unavailable without metrics when no review is returned: %j",
    async (data) => {
      dailyReview.mockResolvedValue(data);
      render(<DailyReviewCard />);
      expect(await screen.findByTestId("daily-review-unavailable")).toHaveTextContent(
        "Daily Review unavailable",
      );
      expect(screen.queryByTestId("daily-review-content")).not.toBeInTheDocument();
      expect(screen.queryByText(/Recorded net PnL:/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Win rate:/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Watcher activity:/)).not.toBeInTheDocument();
    },
  );
  it.each([
    { ...review, window: undefined },
    { ...review, facts: undefined },
    { ...review, daily_pnl: undefined },
    { ...review, counts: {} },
  ])("does not render partial review payloads: %j", (data) => {
    render(<DailyReviewContent review={data as DailyReview} />);
    expect(screen.getByTestId("daily-review-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("daily-review-content")).not.toBeInTheDocument();
  });
  it("accepts missing content without dereferencing a review", () => {
    render(<DailyReviewContent />);
    expect(screen.getByTestId("daily-review-unavailable")).toBeInTheDocument();
  });
  it("hides previous review metrics while a refresh is loading", async () => {
    dailyReview.mockResolvedValueOnce(review).mockImplementationOnce(() => new Promise(() => {}));
    render(<DailyReviewCard />);
    await screen.findByTestId("daily-review-content");
    fireEvent.click(screen.getByRole("button", { name: "Review day" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Loading Daily Review");
    expect(screen.queryByTestId("daily-review-content")).not.toBeInTheDocument();
    expect(screen.queryByTestId("daily-review-unavailable")).not.toBeInTheDocument();
    expect(screen.queryByText(/Recorded net PnL:/)).not.toBeInTheDocument();
  });
  it("separates evidence classes and displays provenance, missing metrics and limitations", () => {
    render(<DailyReviewContent review={review} />);
    expect(within(section(/^Facts/)).getByText("Forming · watch")).toBeInTheDocument();
    expect(within(section(/^Facts/)).getByText("blocked_daily_loss")).toBeInTheDocument();
    expect(within(section(/^Facts/)).queryByText("My reflection")).not.toBeInTheDocument();
    expect(within(section(/^User observations/)).getByText("My reflection")).toBeInTheDocument();
    expect(within(section(/^System inference/)).getByText("Recorded skip")).toBeInTheDocument();
    expect(within(section(/^Research suggestions/)).getByText("Review lesson")).toBeInTheDocument();
    expect(within(section(/^Facts/)).getAllByText(/trade-1/)[0]).toHaveTextContent("2026-10-01T12:00:00Z");
    expect(screen.getByText("Recording coverage is not guaranteed.")).toBeInTheDocument();
    expect(screen.getByText(/Recorded net PnL/)).toHaveTextContent("3.123456789 (incomplete)");
    expect(screen.getByText(/Win rate:/)).toHaveTextContent("Win rate: Unavailable · Expectancy: Unavailable");
    expect(screen.getByText(/Statistics require/)).toHaveTextContent("at least 5 measured closes");
  });
  it("does not invent empty-day PnL or combine accounts", () => {
    const { rerender } = render(<DailyReviewContent review={{ ...review, daily_pnl: [] }} />);
    expect(screen.getByText(/Unavailable — no recorded paper closes/)).toBeInTheDocument();
    rerender(<DailyReviewContent review={{ ...review, daily_pnl: [
      { ...review.daily_pnl[0], complete: true, missing_pnl_count: 0, measured_count: 5,
        closed_count: 5, win_rate: "0.6", expectancy: "1.234", recorded_net_pnl: "6.17" },
      { ...review.daily_pnl[0], cohort: "paper_validation:account-2", recorded_net_pnl: null },
    ] }} />);
    expect(screen.getByText("paper_validation:account-2")).toBeInTheDocument();
    expect(screen.getByText("Win rate: 60.0% · Expectancy: 1.234")).toBeInTheDocument();
    expect(screen.getByText(/Recorded net PnL: Unavailable/)).toBeInTheDocument();
  });
  it("exposes Daily Review on the existing trader dashboard even when other sources fail", async () => {
    dailyReview.mockResolvedValue(review);
    render(<TraderDashboardView data={{
      portfolio: failedSource("down"),
      journal: failedSource("down"), strategyStats: failedSource("down"),
      summary: failedSource("down"), watcher: failedSource("down"),
      market: failedSource("down"), alerts: failedSource("down"),
    }} posture={describeSafetyPosture("paper", false)} />);
    expect(screen.getByTestId("dashboard-daily-review")).toBeInTheDocument();
    await screen.findByTestId("daily-review-content");
  });
  it("loads and submits an explicit date and timezone", async () => {
    dailyReview.mockResolvedValue(review);
    render(<DailyReviewCard />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading Daily Review");
    await screen.findByTestId("daily-review-content");
    fireEvent.change(screen.getByLabelText("Review date"), { target: { value: "2026-10-25" } });
    fireEvent.change(screen.getByLabelText("Review timezone"), { target: { value: "Europe/Berlin" } });
    fireEvent.click(screen.getByRole("button", { name: "Review day" }));
    await waitFor(() => expect(dailyReview).toHaveBeenLastCalledWith({ date: "2026-10-25", timezone: "Europe/Berlin" }));
  });
  it("clears stale metrics on failure and supports retry", async () => {
    dailyReview.mockResolvedValueOnce(review).mockRejectedValueOnce(new Error("Failed")).mockResolvedValueOnce(review);
    render(<DailyReviewCard />);
    await screen.findByTestId("daily-review-content");
    fireEvent.click(screen.getByRole("button", { name: "Review day" }));
    await screen.findByRole("alert");
    expect(screen.queryByTestId("daily-review-content")).not.toBeInTheDocument();
    expect(screen.queryByTestId("daily-review-unavailable")).not.toBeInTheDocument();
    expect(screen.queryByText(/Recorded net PnL:/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByTestId("daily-review-content");
  });
});
