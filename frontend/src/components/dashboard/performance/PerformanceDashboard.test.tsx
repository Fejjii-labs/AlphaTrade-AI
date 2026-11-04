import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { JournalPerformanceDashboardResponse } from "@/lib/api/types";

import { PerformanceDashboard } from "./PerformanceDashboard";
import { PerformanceKpiCards } from "./PerformanceKpiCards";
import { PnlCalendar } from "./PnlCalendar";
import { zeroOffset } from "./CumulativePnlChart";
import { buildCalendarWeeks, formatCompactPnl, rangeParams } from "./performance-model";

vi.mock("@/lib/api", () => ({ api: { journal: { performanceCalendar: vi.fn() } } }));

const NOW = new Date(2026, 6, 15, 12); // 15 July 2026, local time

function response(
  overrides: Partial<JournalPerformanceDashboardResponse> = {},
): JournalPerformanceDashboardResponse {
  return {
    filters: {
      date_from: null,
      date_to: null,
      timezone: "UTC",
      source: null,
      execution_actor: null,
      excluded_sources: ["backtest"],
    },
    summary: {
      trade_count: 5,
      pnl_sample_count: 5,
      wins: 2,
      losses: 2,
      breakeven: 1,
      win_rate: 0.5,
      net_pnl: "100.00",
      gross_profit: "300.00",
      gross_loss: "-200.00",
      profit_factor: 1.5,
      average_win: "150.00",
      average_loss: "-100.00",
      win_loss_ratio: 1.5,
    },
    days: [
      { date: "2026-07-01", net_pnl: "50.00", trade_count: 2, wins: 1, losses: 1, breakeven: 0, win_rate: 0.5 },
      { date: "2026-07-02", net_pnl: "1200.00", trade_count: 1, wins: 1, losses: 0, breakeven: 0, win_rate: 1 },
      { date: "2026-07-03", net_pnl: "-150.00", trade_count: 2, wins: 0, losses: 1, breakeven: 1, win_rate: 0 },
    ],
    cumulative: [
      { date: "2026-07-01", daily_net_pnl: "50.00", cumulative_net_pnl: "50.00" },
      { date: "2026-07-02", daily_net_pnl: "1200.00", cumulative_net_pnl: "1250.00" },
      { date: "2026-07-03", daily_net_pnl: "-150.00", cumulative_net_pnl: "1100.00" },
    ],
    risk: { max_drawdown: "150.00", recovery_factor: 0.67, consistency: 0.04, winning_days: 2, losing_days: 1 },
    score: {
      overall: 42.5,
      components: [
        { key: "win_rate", label: "Win %", value: 0.5, score: 83.33, target: 0.6, note: null },
        { key: "profit_factor", label: "Profit factor", value: 1.5, score: 60, target: 2.5, note: null },
        { key: "win_loss_ratio", label: "Avg win/loss", value: 1.5, score: 60, target: 2.5, note: null },
        { key: "recovery_factor", label: "Recovery factor", value: 0.67, score: 22.2, target: 3, note: null },
        { key: "max_drawdown", label: "Max drawdown", value: 0.5, score: 0, target: 0.5, note: null },
        { key: "consistency", label: "Consistency", value: 0.04, score: 4, target: null, note: null },
      ],
      missing_components: [],
    },
    confidence: "low",
    truncated: false,
    max_rows: 5000,
    warnings: [],
    generated_at: "2026-07-15T12:00:00Z",
    ...overrides,
  };
}

const EMPTY = response({
  summary: {
    trade_count: 0,
    pnl_sample_count: 0,
    wins: 0,
    losses: 0,
    breakeven: 0,
    win_rate: null,
    net_pnl: null,
    gross_profit: null,
    gross_loss: null,
    profit_factor: null,
    average_win: null,
    average_loss: null,
    win_loss_ratio: null,
  },
  days: [],
  cumulative: [],
  risk: { max_drawdown: null, recovery_factor: null, consistency: null, winning_days: 0, losing_days: 0 },
  score: {
    overall: null,
    components: [{ key: "win_rate", label: "Win %", value: null, score: null, target: 0.6, note: null }],
    missing_components: ["win_rate"],
  },
  warnings: ["No closed trades match the filters."],
});

beforeEach(() => {
  vi.mocked(api.journal.performanceCalendar).mockReset();
});
afterEach(cleanup);

describe("PerformanceDashboard", () => {
  it("renders real numbers from the endpoint", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(response());
    render(<PerformanceDashboard now={NOW} />);
    await waitFor(() => expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("+100.00"));
    expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("5 closed trades");
    expect(screen.getByTestId("kpi-profit-factor")).toHaveTextContent("1.50");
    expect(screen.getByTestId("kpi-win-rate")).toHaveTextContent("50.0%");
    expect(within(screen.getByTestId("win-rate-counts")).getAllByText(/\d/).map((n) => n.textContent)).toEqual([
      "2",
      "1",
      "2",
    ]);
    expect(screen.getByTestId("kpi-win-loss")).toHaveTextContent("1.50");
    expect(screen.getByTestId("score-overall")).toHaveTextContent("42.5");
    expect(screen.getByTestId("cumulative-last")).toHaveTextContent("+1100.00");
    expect(screen.getByTestId("calendar-month")).toHaveTextContent("July 2026");
    expect(screen.getByTestId("calendar-day-2026-07-02")).toHaveTextContent("+1.20K");
    expect(screen.getByTestId("calendar-day-2026-07-03")).toHaveAttribute("data-tone", "loss");
    expect(screen.queryByTestId("sample-data-badge")).not.toBeInTheDocument();

    const calls = vi.mocked(api.journal.performanceCalendar).mock.calls.map(([params]) => params);
    expect(calls).toContainEqual(expect.objectContaining({ date_from: "2026-07-01", date_to: "2026-07-31" }));
    expect(calls).toContainEqual(expect.objectContaining({ date_from: "2026-04-17", date_to: "2026-07-15" }));
  });

  it("shows an explicit empty state with dashes, never zeros", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(EMPTY);
    render(<PerformanceDashboard now={NOW} />);
    expect(await screen.findByTestId("performance-empty")).toBeInTheDocument();
    expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("—");
    expect(screen.getByTestId("kpi-profit-factor")).toHaveTextContent("—");
    expect(screen.getByTestId("score-overall")).toHaveTextContent("—");
    expect(screen.getByText(/Not enough closed trades to score/)).toBeInTheDocument();
    expect(screen.getByText(/No closed trades with recorded P&L/)).toBeInTheDocument();
    expect(screen.getByTestId("calendar-month-total")).toHaveTextContent("—");
  });

  it("navigates months and refetches the calendar range", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(response());
    render(<PerformanceDashboard now={NOW} />);
    await waitFor(() => expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("+100.00"));
    fireEvent.click(screen.getByRole("button", { name: "Previous month" }));
    expect(screen.getByTestId("calendar-month")).toHaveTextContent("June 2026");
    const calls = vi.mocked(api.journal.performanceCalendar).mock.calls.map(([params]) => params);
    expect(calls).toContainEqual(expect.objectContaining({ date_from: "2026-06-01", date_to: "2026-06-30" }));
  });

  it("filters by execution actor and states that backtests are excluded", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(response());
    render(<PerformanceDashboard now={NOW} />);
    await waitFor(() => expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("+100.00"));
    expect(screen.getByTestId("performance-scope")).toHaveTextContent("Backtests are excluded");
    const initial = vi.mocked(api.journal.performanceCalendar).mock.calls.map(([params]) => params);
    expect(initial.every((params) => params?.execution_actor === undefined)).toBe(true);

    fireEvent.click(screen.getByRole("radio", { name: "System (agents)" }));
    await waitFor(() => {
      const calls = vi.mocked(api.journal.performanceCalendar).mock.calls.map(([params]) => params);
      expect(calls).toContainEqual(
        expect.objectContaining({ execution_actor: "system", date_from: "2026-07-01", date_to: "2026-07-31" }),
      );
      expect(calls).toContainEqual(
        expect.objectContaining({ execution_actor: "system", date_from: "2026-04-17" }),
      );
    });
    fireEvent.click(screen.getByRole("radio", { name: "Human" }));
    await waitFor(() =>
      expect(vi.mocked(api.journal.performanceCalendar).mock.calls.map(([p]) => p)).toContainEqual(
        expect.objectContaining({ execution_actor: "human" }),
      ),
    );
  });

  it("renders a calendar error instead of zeros when only the month request fails", async () => {
    vi.mocked(api.journal.performanceCalendar).mockImplementation((params) =>
      params?.date_from === "2026-07-01" && params?.date_to === "2026-07-31"
        ? Promise.reject(new Error("boom"))
        : Promise.resolve(response()),
    );
    render(<PerformanceDashboard now={NOW} />);
    expect(await screen.findByTestId("calendar-error")).toHaveTextContent("Calendar unavailable for July 2026");
    expect(screen.getByTestId("calendar-month-total")).toHaveTextContent("Monthly: —");
    expect(screen.getByTestId("calendar-month-total")).not.toHaveTextContent("trading day");
    expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("+100.00");
  });

  it("clears the previous range while a new one loads", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(response());
    render(<PerformanceDashboard now={NOW} />);
    await waitFor(() => expect(screen.getByTestId("score-overall")).toHaveTextContent("42.5"));
    vi.mocked(api.journal.performanceCalendar).mockReturnValue(new Promise(() => {}));
    fireEvent.click(screen.getByRole("radio", { name: "30D" }));
    await waitFor(() => expect(screen.getByTestId("score-overall")).toHaveTextContent("—"));
    expect(screen.getByTestId("cumulative-last")).toHaveTextContent("—");
    expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("—");
  });

  it("sample data is off by default and visibly badged when enabled", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(EMPTY);
    render(<PerformanceDashboard now={NOW} />);
    await screen.findByTestId("performance-empty");
    expect(screen.getByTestId("performance-dashboard")).toHaveAttribute("data-sample", "false");
    fireEvent.click(screen.getByRole("checkbox", { name: "Sample data" }));
    expect(screen.getByTestId("performance-dashboard")).toHaveAttribute("data-sample", "true");
    expect(screen.getByTestId("sample-data-badge")).toHaveTextContent("Sample data");
    expect(screen.getByText(/None of these numbers come from your journal/)).toBeInTheDocument();
    expect(screen.queryByTestId("performance-empty")).not.toBeInTheDocument();
  });

  it("shows unavailable state when the request fails", async () => {
    vi.mocked(api.journal.performanceCalendar).mockRejectedValue(new Error("boom"));
    render(<PerformanceDashboard now={NOW} />);
    expect(await screen.findByText("Journal performance unavailable")).toBeInTheDocument();
  });
});

describe("performance components", () => {
  it("KPI cards render dashes without a summary", () => {
    render(<PerformanceKpiCards summary={null} />);
    expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("—");
    expect(screen.getByTestId("kpi-win-rate")).toHaveTextContent("—");
  });

  it("calendar shows weekly totals and trade counts", () => {
    const { days, summary } = response();
    render(
      <PnlCalendar
        month={{ year: 2026, month: 6 }}
        days={days}
        summary={summary}
        onPrev={() => {}}
        onNext={() => {}}
        onToday={() => {}}
      />,
    );
    // July 2026 starts on a Wednesday → days 1-4 fall in week 1.
    expect(screen.getByTestId("calendar-week-1")).toHaveTextContent("+1.10K");
    expect(screen.getByTestId("calendar-week-1")).toHaveTextContent("3 days");
    expect(screen.getByTestId("calendar-week-2")).toHaveTextContent("—");
    expect(screen.getByTestId("calendar-day-2026-07-01")).toHaveTextContent("2 trades");
    expect(screen.getByTestId("calendar-month-total")).toHaveTextContent("+100.00");
  });
});

describe("performance model", () => {
  it("builds Sunday-first weeks", () => {
    const weeks = buildCalendarWeeks({ year: 2026, month: 6 }, []);
    expect(weeks[0].cells.slice(0, 4)).toEqual([null, null, null, "2026-07-01"]);
    expect(weeks).toHaveLength(5);
    expect(weeks[0].netPnl).toBeNull();
  });

  it("formats compact PnL and range presets", () => {
    expect(formatCompactPnl("1050")).toBe("+1.05K");
    expect(formatCompactPnl("-37.5")).toBe("−37.5");
    expect(formatCompactPnl("600")).toBe("+600");
    expect(formatCompactPnl(null)).toBe("—");
    expect(rangeParams("all", NOW)).toEqual({});
    expect(rangeParams("ytd", NOW)).toEqual({ date_from: "2026-01-01", date_to: "2026-07-15" });
    expect(rangeParams("30d", NOW)).toEqual({ date_from: "2026-06-16", date_to: "2026-07-15" });
  });

  it("splits the area gradient at zero", () => {
    expect(zeroOffset([10, 20])).toBe(1);
    expect(zeroOffset([-10, -5])).toBe(0);
    expect(zeroOffset([30, -10])).toBe(0.75);
  });
});
