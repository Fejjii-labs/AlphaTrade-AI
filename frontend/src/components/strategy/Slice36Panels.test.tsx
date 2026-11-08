import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DisciplineAnalysisPanel } from "@/components/journal/DisciplineAnalysisPanel";

describe("DisciplineAnalysisPanel", () => {
  it("renders human vs system discipline panel", () => {
    render(
      <DisciplineAnalysisPanel
        comparison={{
          trade_id: "t1",
          plan_adherence_score: 72,
          plan_adherence: {
            entry_followed_plan: 18,
            size_respected_risk: 20,
            stop_loss_respected: 15,
            profit_taking_followed: 10,
            emotion_controlled: 9,
            journal_completed: 0,
          },
          emotion_tags: [],
          notes: [],
          early_exit_flag: true,
          missed_runner: {
            early_exit_flag: true,
            recommended_lesson: "Review runner rules before entry.",
            confidence: "low",
          },
          stop_loss_analysis: {
            stop_violation_flag: false,
            lesson: "Stop discipline appears aligned.",
          },
          system_would_have_done: "System planned long BTCUSDT.",
        }}
      />,
    );
    expect(screen.getByTestId("discipline-panel")).toBeInTheDocument();
    expect(screen.getByTestId("early-exit-analysis")).toHaveTextContent("Early exit");
    expect(screen.getByTestId("stop-loss-analysis")).toHaveTextContent("Stop loss discipline");
  });

  it("links to lesson candidates", () => {
    render(
      <DisciplineAnalysisPanel
        comparison={{
          trade_id: "t1",
          plan_adherence_score: 72,
          plan_adherence: {
            entry_followed_plan: 18,
            size_respected_risk: 20,
            stop_loss_respected: 15,
            profit_taking_followed: 10,
            emotion_controlled: 9,
            journal_completed: 0,
          },
          emotion_tags: [],
          notes: [],
        }}
        lessonCandidateIds={["lesson-abc"]}
      />,
    );
    expect(screen.getByText("View lesson candidate")).toBeInTheDocument();
  });
});
