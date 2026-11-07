import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { JournalPerformanceDashboardResponse } from "@/lib/api/types";

import { PerformanceDashboard } from "./PerformanceDashboard";
import { PerformanceKpiCards, dayWinRate, moneyTone, profitFactorTone } from "./PerformanceKpiCards";
import { PnlCalendar } from "./PnlCalendar";
import { CumulativePnlChart, curveSummary, zeroOffset } from "./CumulativePnlChart";
import { buildCalendarWeeks, customRangeError, formatCompactPnl, rangeParams } from "./performance-model";

vi.mock("@/lib/api", () => ({ api: { journal: { performanceCalendar: vi.fn() } } }));

const NOW = new Date(2026, 6, 15, 12); // 15 July 2026, local time

function response(
  overrides: Partial<JournalPerformanceDashboardResponse> = {},
): JournalPerformanceDashboardResponse {
  return {
    filters: {
      scope: "demo",
      scope_label: "Demo trades (BloFin demo)",
      date_from: null,
      date_to: null,
      timezone: "UTC",
      source: null,
      included_sources: ["manual_demo_test", "blofin_demo_manual", "paper_execution", "manual"],
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
      expectancy: "20.00",
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
    oldest_covered_date: null,
    open_trades_excluded: 0,
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
    expectancy: null,
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
    expect(screen.getByTestId("kpi-expectancy")).toHaveTextContent("+20.00");
    expect(screen.getByTestId("kpi-expectancy")).toHaveTextContent("Net P&L per closed trade");
    expect(screen.getByTestId("kpi-win-rate")).toHaveTextContent("50.0%");
    expect(screen.getByTestId("kpi-win-rate")).toHaveTextContent("excludes breakevens");
    expect(within(screen.getByTestId("win-rate-counts")).getAllByText(/\d/).map((n) => n.textContent)).toEqual([
      "2",
      "1",
      "2",
    ]);
    expect(screen.getByTestId("kpi-win-loss")).toHaveTextContent("1.50");
    // 2 green days, 1 red day → 66.7%.
    expect(screen.getByTestId("kpi-day-win-rate")).toHaveTextContent("66.7%");
    expect(screen.getByRole("img", { name: "2 winning, 1 losing days" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "2 winning, 1 breakeven, 2 losing trades" })).toBeInTheDocument();
    expect(screen.getByTestId("kpi-net-pnl")).toHaveAttribute("data-tone", "profit");
    expect(screen.getByTestId("kpi-profit-factor")).toHaveAttribute("data-tone", "profit");
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
    expect(screen.getByTestId("kpi-expectancy")).toHaveTextContent("—");
    expect(screen.getByTestId("kpi-day-win-rate")).toHaveTextContent("—");
    expect(screen.getByTestId("kpi-net-pnl")).toHaveAttribute("data-tone", "none");
    expect(screen.getByTestId("score-overall")).toHaveTextContent("—");
    expect(screen.getByText(/Not enough closed trades to score/)).toBeInTheDocument();
    expect(screen.getByText(/No closed trades with recorded P&L/)).toBeInTheDocument();
    expect(screen.getByTestId("calendar-month-total")).toHaveTextContent("—");
  });

  it("flags a capped newest-first window with its oldest covered date", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(
      response({
        truncated: true,
        oldest_covered_date: "2026-05-02",
        warnings: ["Capped at the 5000 most recent closed trades from 2026-05-02; older trades in range are not counted. Narrow the date range for full coverage."],
      }),
    );
    render(<PerformanceDashboard now={NOW} />);
    expect(await screen.findByTestId("performance-truncated")).toHaveTextContent("Newest 5000 trades since 2026-05-02");
    expect(screen.getByTestId("performance-warnings")).toHaveTextContent("most recent closed trades from 2026-05-02");
  });

  it("states that partial exits on open trades are not counted", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(response({ open_trades_excluded: 3 }));
    render(<PerformanceDashboard now={NOW} />);
    await waitFor(() => expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("+100.00"));
    expect(screen.getByTestId("performance-scope")).toHaveTextContent(
      "Partial exits on open trades count only once the trade closes",
    );
    expect(screen.getByTestId("performance-open-excluded")).toHaveTextContent("3 open trades not counted");
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

  it("waits for the resolved timezone and shows it in the Showing line", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(response());
    const { rerender } = render(<PerformanceDashboard now={NOW} timezone={null} />);
    expect(api.journal.performanceCalendar).not.toHaveBeenCalled();
    rerender(<PerformanceDashboard now={NOW} timezone="Asia/Tokyo" timezoneLabel="risk settings" />);
    await waitFor(() => expect(api.journal.performanceCalendar).toHaveBeenCalled());
    const calls = vi.mocked(api.journal.performanceCalendar).mock.calls.map(([params]) => params);
    expect(calls.every((params) => params?.tz === "Asia/Tokyo")).toBe(true);
    expect(screen.getByTestId("performance-timezone")).toHaveTextContent("Days in Asia/Tokyo (risk settings)");
  });

  it("follows a page-owned scope and hides its own scope picker", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(response());
    const { rerender } = render(<PerformanceDashboard now={NOW} scope="paper" />);
    await waitFor(() => expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("+100.00"));
    expect(screen.queryByRole("radiogroup", { name: "Account scope" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Other trade scopes" })).not.toBeInTheDocument();
    let calls = vi.mocked(api.journal.performanceCalendar).mock.calls.map(([params]) => params);
    expect(calls.every((params) => params?.scope === "paper")).toBe(true);
    vi.mocked(api.journal.performanceCalendar).mockClear();
    rerender(<PerformanceDashboard now={NOW} scope="demo" />);
    await waitFor(() => expect(api.journal.performanceCalendar).toHaveBeenCalled());
    calls = vi.mocked(api.journal.performanceCalendar).mock.calls.map(([params]) => params);
    expect(calls.every((params) => params?.scope === "demo")).toBe(true);
  });

  it("defaults to the Demo scope, labels it, and never mixes scopes", async () => {
    vi.mocked(api.journal.performanceCalendar).mockImplementation(async (params) => {
      const scope = params?.scope ?? "demo";
      const labels: Record<string, string> = {
        demo: "Demo trades (BloFin demo)",
        paper: "Paper trades (paper execution, validation, and manual trades without a real venue)",
        real: "Real trades (manually logged, real exchange recorded)",
        backtest: "Backtest trades (simulated history)",
      };
      const base = response();
      return { ...base, filters: { ...base.filters, scope, scope_label: labels[scope] ?? scope } };
    });
    render(<PerformanceDashboard now={NOW} />);
    await waitFor(() => expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("+100.00"));
    expect(screen.getByTestId("performance-scope")).toHaveTextContent("Showing: Demo trades (BloFin demo)");
    expect(screen.getByRole("radio", { name: "Demo" })).toHaveAttribute("aria-checked", "true");
    const initial = vi.mocked(api.journal.performanceCalendar).mock.calls.map(([params]) => params);
    expect(initial.length).toBeGreaterThan(0);
    expect(initial.every((params) => params?.scope === "demo")).toBe(true);

    fireEvent.click(screen.getByRole("radio", { name: "Paper" }));
    await waitFor(() =>
      expect(screen.getByTestId("performance-scope")).toHaveTextContent("Showing: Paper trades"),
    );
    const calls = vi.mocked(api.journal.performanceCalendar).mock.calls.map(([params]) => params);
    expect(calls).toContainEqual(
      expect.objectContaining({ scope: "paper", date_from: "2026-07-01", date_to: "2026-07-31" }),
    );
    expect(calls).toContainEqual(expect.objectContaining({ scope: "paper", date_from: "2026-04-17" }));

    fireEvent.change(screen.getByRole("combobox", { name: "Other trade scopes" }), {
      target: { value: "backtest" },
    });
    await waitFor(() =>
      expect(screen.getByTestId("performance-scope")).toHaveTextContent("Showing: Backtest trades"),
    );
    expect(screen.getByRole("radio", { name: "Demo" })).toHaveAttribute("aria-checked", "false");
    // Every request carries exactly one scope.
    const all = vi.mocked(api.journal.performanceCalendar).mock.calls.map(([params]) => params);
    expect(all.every((params) => typeof params?.scope === "string")).toBe(true);
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
    expect(screen.queryByTestId("kpi-net-pnl")).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading performance figures" })).toBeInTheDocument();
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

  it("narrows to one source inside the scope and resets with the scope", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(response());
    render(<PerformanceDashboard now={NOW} />);
    const select = await screen.findByRole("combobox", { name: "Source" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toContain("Manually logged");
    fireEvent.change(select, { target: { value: "blofin_demo_manual" } });
    await waitFor(() =>
      expect(screen.getByTestId("performance-scope")).toHaveTextContent("BloFin demo (manual) only"),
    );
    const calls = vi.mocked(api.journal.performanceCalendar).mock.calls.map(([params]) => params);
    expect(calls).toContainEqual(
      expect.objectContaining({ scope: "demo", source: "blofin_demo_manual", date_from: "2026-07-01" }),
    );
    expect(calls).toContainEqual(
      expect.objectContaining({ scope: "demo", source: "blofin_demo_manual", date_from: "2026-04-17" }),
    );
    fireEvent.click(screen.getByRole("radio", { name: "Paper" }));
    await waitFor(() =>
      expect(vi.mocked(api.journal.performanceCalendar).mock.calls.at(-1)?.[0]).toEqual(
        expect.objectContaining({ scope: "paper" }),
      ),
    );
    expect(vi.mocked(api.journal.performanceCalendar).mock.calls.at(-1)?.[0]).not.toHaveProperty("source");
    expect(screen.getByTestId("performance-scope")).not.toHaveTextContent("BloFin demo (manual) only");
  });

  it("custom range sends the picked dates and blocks an inverted range", async () => {
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(response());
    render(<PerformanceDashboard now={NOW} />);
    await waitFor(() => expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("+100.00"));
    fireEvent.click(screen.getByRole("radio", { name: "Custom" }));
    const from = screen.getByLabelText("From");
    const to = screen.getByLabelText("To");
    expect(from).toHaveValue("2026-06-16");
    fireEvent.change(from, { target: { value: "2026-05-01" } });
    fireEvent.change(to, { target: { value: "2026-05-31" } });
    await waitFor(() =>
      expect(vi.mocked(api.journal.performanceCalendar).mock.calls.map(([p]) => p)).toContainEqual(
        expect.objectContaining({ date_from: "2026-05-01", date_to: "2026-05-31" }),
      ),
    );
    const before = vi.mocked(api.journal.performanceCalendar).mock.calls.length;
    fireEvent.change(from, { target: { value: "2026-06-10" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Start date must be on or before the end date.");
    expect(from).toHaveAttribute("aria-invalid", "true");
    expect(vi.mocked(api.journal.performanceCalendar).mock.calls.length).toBe(before);
    expect(screen.getByTestId("performance-range-pending")).toBeInTheDocument();
    expect(screen.queryByTestId("kpi-net-pnl")).not.toBeInTheDocument();
    expect(screen.queryByText(/Not enough closed trades to score/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(screen.getByRole("radio", { name: "90D" })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByLabelText("From")).not.toBeInTheDocument();
  });

  it("shows unavailable state when the request fails, and Retry refetches", async () => {
    vi.mocked(api.journal.performanceCalendar).mockRejectedValue(new Error("boom"));
    render(<PerformanceDashboard now={NOW} />);
    expect(await screen.findByText("Journal performance unavailable")).toBeInTheDocument();
    vi.mocked(api.journal.performanceCalendar).mockResolvedValue(response());
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("+100.00"));
    expect(screen.getByTestId("cumulative-drawdown")).toHaveTextContent("Max drawdown −150.00");
  });

  it("shows skeletons for every panel while loading, never empty-state copy", () => {
    vi.mocked(api.journal.performanceCalendar).mockReturnValue(new Promise(() => {}));
    render(<PerformanceDashboard now={NOW} />);
    expect(screen.getByTestId("performance-kpis-loading")).toBeInTheDocument();
    expect(screen.getByTestId("score-radar-loading")).toBeInTheDocument();
    expect(screen.getByTestId("cumulative-loading")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Daily net P&L, July 2026" })).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText(/Not enough closed trades to score/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No closed trades with recorded P&L/)).not.toBeInTheDocument();
  });
});

describe("performance components", () => {
  it("KPI tones follow the sign and profit factor 1.0, never colour alone", () => {
    expect(moneyTone("-5")).toBe("loss");
    expect(moneyTone("0")).toBe("neutral");
    expect(moneyTone(null)).toBe("none");
    expect(profitFactorTone(0.8)).toBe("loss");
    expect(profitFactorTone(1)).toBe("profit");
    expect(profitFactorTone(null)).toBe("none");
    expect(dayWinRate({ max_drawdown: null, recovery_factor: null, consistency: null, winning_days: 0, losing_days: 0 })).toBeNull();
    render(<PerformanceKpiCards summary={{ ...response().summary, net_pnl: "-12.50" }} />);
    expect(screen.getByTestId("kpi-net-pnl")).toHaveAttribute("data-tone", "loss");
    expect(screen.getByTestId("kpi-net-pnl")).toHaveTextContent("−12.50");
  });

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
    const table = screen.getByRole("table", { name: "Daily net P&L, July 2026" });
    expect(within(table).getAllByRole("columnheader")[0]).toHaveTextContent("Sun");
    expect(within(table).getByRole("cell", { name: /^2026-07-02: \+1200\.00 net, 1 trade/ })).toBeInTheDocument();
  });

  it("calendar marks today and offers Retry on error", () => {
    const onRetry = vi.fn();
    render(
      <PnlCalendar
        month={{ year: 2026, month: 6 }}
        days={response().days}
        summary={null}
        error
        todayIso="2026-07-02"
        onPrev={() => {}}
        onNext={() => {}}
        onToday={() => {}}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByTestId("calendar-day-2026-07-02")).toHaveAttribute("aria-current", "date");
    expect(screen.getByRole("cell", { name: /^2026-07-02 \(today\)/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("curve has a text alternative", () => {
    const { cumulative } = response();
    render(<CumulativePnlChart points={cumulative} />);
    expect(screen.getByTestId("cumulative-summary")).toHaveTextContent(
      "Cumulative net P&L over 3 trading days, from +50.00 on 2026-07-01 to +1100.00 on 2026-07-03; high +1250.00, low +50.00.",
    );
    expect(screen.getByRole("table", { name: "Daily and cumulative net P&L" })).toBeInTheDocument();
    expect(curveSummary([])).toBe("");
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
    expect(formatCompactPnl("-140.5", { whole: true })).toBe("−141");
    expect(formatCompactPnl("1250", { whole: true })).toBe("+1.3K");
    expect(rangeParams("all", NOW)).toEqual({});
    expect(rangeParams("ytd", NOW)).toEqual({ date_from: "2026-01-01", date_to: "2026-07-15" });
    expect(rangeParams("30d", NOW)).toEqual({ date_from: "2026-06-16", date_to: "2026-07-15" });
    expect(customRangeError("2026-07-01", "2026-07-01")).toBeNull();
    expect(customRangeError("2026-07-02", "2026-07-01")).toMatch(/on or before/);
    expect(customRangeError("", "2026-07-01")).toMatch(/both/);
  });

  it("splits the area gradient at zero", () => {
    expect(zeroOffset([10, 20])).toBe(1);
    expect(zeroOffset([-10, -5])).toBe(0);
    expect(zeroOffset([30, -10])).toBe(0.75);
  });
});
