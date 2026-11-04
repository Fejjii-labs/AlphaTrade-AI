import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { JournalDashboardScoreComponent } from "@/lib/api/types";

import { ScoreRadar } from "./ScoreRadar";

const plotted = vi.hoisted(() => ({ data: [] as Array<{ axis: string; score: number | null }> }));

vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    RadarChart: ({ data }: { data: Array<{ axis: string; score: number | null }> }) => {
      plotted.data = data;
      return null;
    },
  };
});

afterEach(cleanup);

function component(
  key: string,
  label: string,
  score: number | null,
  note: string | null = null,
): JournalDashboardScoreComponent {
  return { key, label, value: score === null ? null : score / 100, score, target: 1, note };
}

describe("ScoreRadar", () => {
  it("leaves unscored components off the polygon and marks them n/a", () => {
    render(
      <ScoreRadar
        overall={70}
        components={[
          component("win_rate", "Win %", 80),
          component("profit_factor", "Profit factor", 60),
          component("win_loss_ratio", "Avg win/loss", 70),
          component("consistency", "Consistency", null, "Needs at least two winning days."),
        ]}
      />,
    );
    expect(screen.getByTestId("score-radar-chart")).toBeInTheDocument();
    expect(plotted.data.map((d) => d.axis)).toEqual(["Win %", "Profit factor", "Avg win/loss"]);
    expect(plotted.data.every((d) => d.score !== null && d.score !== 0)).toBe(true);
    expect(screen.getByTestId("score-radar-unscored")).toHaveTextContent(
      "Consistency: n/a · Needs at least two winning days.",
    );
  });

  it("keeps a real zero score on the polygon", () => {
    render(
      <ScoreRadar
        overall={40}
        components={[
          component("win_rate", "Win %", 80),
          component("profit_factor", "Profit factor", 40),
          component("max_drawdown", "Max drawdown", 0),
        ]}
      />,
    );
    expect(plotted.data.find((d) => d.axis === "Max drawdown")?.score).toBe(0);
    expect(screen.queryByTestId("score-radar-unscored")).not.toBeInTheDocument();
  });
});
