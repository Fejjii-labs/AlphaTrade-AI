import type {
  JournalDashboardDay,
  JournalDashboardScope,
  JournalPerformanceDashboardResponse,
} from "@/lib/api/types";
import { UNAVAILABLE } from "@/lib/format";

/** Decimal string → finite number, else null (never a fabricated 0). */
export function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

/** Signed, compact PnL ("+1.05K", "−37.5", "+600"); no currency symbol. */
export function formatCompactPnl(value: string | number | null | undefined): string {
  const numeric = toNumber(value);
  if (numeric === null) return UNAVAILABLE;
  const sign = numeric >= 0 ? "+" : "−";
  const magnitude = Math.abs(numeric);
  if (magnitude >= 1_000_000) return `${sign}${(magnitude / 1_000_000).toFixed(2)}M`;
  if (magnitude >= 1_000) return `${sign}${(magnitude / 1_000).toFixed(2)}K`;
  return `${sign}${Number(magnitude.toFixed(2))}`;
}

export function pnlTone(value: string | number | null | undefined): "profit" | "loss" | "flat" | "none" {
  const numeric = toNumber(value);
  if (numeric === null) return "none";
  if (numeric > 0) return "profit";
  if (numeric < 0) return "loss";
  return "flat";
}

export function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

export function isoDate(year: number, month: number, day: number): string {
  return `${year}-${pad2(month + 1)}-${pad2(day)}`;
}

export function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
}

export type MonthRef = { year: number; month: number }; // month 0-11

export function shiftMonth({ year, month }: MonthRef, delta: number): MonthRef {
  const total = year * 12 + month + delta;
  return { year: Math.floor(total / 12), month: ((total % 12) + 12) % 12 };
}

export function monthRange({ year, month }: MonthRef): { date_from: string; date_to: string } {
  return {
    date_from: isoDate(year, month, 1),
    date_to: isoDate(year, month, daysInMonth(year, month)),
  };
}

export function monthLabel({ year, month }: MonthRef): string {
  return new Date(Date.UTC(year, month, 1)).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

export type CalendarWeek = {
  index: number;
  /** ISO dates for Sun..Sat; null outside the month. */
  cells: Array<string | null>;
  netPnl: number | null;
  tradingDays: number;
};

/** Sunday-first weeks for a month with weekly totals from the day buckets. */
export function buildCalendarWeeks(
  ref: MonthRef,
  days: JournalDashboardDay[],
): CalendarWeek[] {
  const byDate = new Map(days.map((day) => [day.date, day]));
  const firstWeekday = new Date(Date.UTC(ref.year, ref.month, 1)).getUTCDay();
  const total = daysInMonth(ref.year, ref.month);
  const weeks: CalendarWeek[] = [];
  let cells: Array<string | null> = Array.from({ length: firstWeekday }, () => null);
  for (let day = 1; day <= total; day += 1) {
    cells.push(isoDate(ref.year, ref.month, day));
    if (cells.length === 7 || day === total) {
      while (cells.length < 7) cells.push(null);
      const inWeek = cells
        .map((date) => (date ? byDate.get(date) : undefined))
        .filter((entry): entry is JournalDashboardDay => entry !== undefined);
      const pnls = inWeek
        .map((entry) => toNumber(entry.net_pnl))
        .filter((value): value is number => value !== null);
      weeks.push({
        index: weeks.length + 1,
        cells,
        netPnl: pnls.length ? pnls.reduce((sum, value) => sum + value, 0) : null,
        tradingDays: inWeek.length,
      });
      cells = [];
    }
  }
  return weeks;
}

export type RangePreset = "30d" | "90d" | "ytd" | "all";

/** One account scope per view; the server never mixes scopes. Default: Demo. */
export const DEFAULT_SCOPE: JournalDashboardScope = "demo";

export const SCOPE_OPTIONS: Array<{
  value: JournalDashboardScope;
  label: string;
  tone?: "paper" | "demo";
}> = [
  { value: "demo", label: "Demo", tone: "demo" },
  { value: "paper", label: "Paper", tone: "paper" },
  { value: "real", label: "Real" },
];

/** Opt-in scopes, never part of the account views above. */
export const EXTRA_SCOPE_OPTIONS: Array<{ value: JournalDashboardScope; label: string }> = [
  { value: "backtest", label: "Backtest" },
  { value: "imported", label: "Imported" },
  { value: "system", label: "System" },
];

/** Fallback label while loading; the server echoes the authoritative one. */
export const SCOPE_LABELS: Record<JournalDashboardScope, string> = {
  demo: "Demo trades (BloFin demo)",
  paper: "Paper trades (paper execution and validation)",
  real: "Real trades (manually logged)",
  backtest: "Backtest trades (simulated history)",
  imported: "Imported trades",
  system: "System trades",
};

export const RANGE_OPTIONS: Array<{ value: RangePreset; label: string }> = [
  { value: "30d", label: "30D" },
  { value: "90d", label: "90D" },
  { value: "ytd", label: "YTD" },
  { value: "all", label: "All" },
];

function localIso(date: Date): string {
  return isoDate(date.getFullYear(), date.getMonth(), date.getDate());
}

export function rangeParams(
  preset: RangePreset,
  today: Date,
): { date_from?: string; date_to?: string } {
  if (preset === "all") return {};
  const to = localIso(today);
  if (preset === "ytd") return { date_from: isoDate(today.getFullYear(), 0, 1), date_to: to };
  const from = new Date(today);
  from.setDate(from.getDate() - (preset === "30d" ? 29 : 89));
  return { date_from: localIso(from), date_to: to };
}

export function browserTimezone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

// --------------------------------------------------------------------------
// Sample data — ONLY behind the explicit "Sample data" toggle, always badged.
// Deterministic so screenshots and tests are stable. Never mixed with real data.
// --------------------------------------------------------------------------

const SAMPLE_PATTERN = [120, 0, -45, 260, 75, -180, 95, 140, 0, 310, -60];

export function buildSampleDashboard(ref: MonthRef): JournalPerformanceDashboardResponse {
  const total = daysInMonth(ref.year, ref.month);
  const days: JournalDashboardDay[] = [];
  for (let day = 1; day <= total; day += 1) {
    const weekday = new Date(Date.UTC(ref.year, ref.month, day)).getUTCDay();
    const pnl = SAMPLE_PATTERN[(day - 1) % SAMPLE_PATTERN.length];
    if (weekday === 0 || weekday === 6 || pnl === 0) continue;
    const trades = (day % 3) + 1;
    const wins = pnl > 0 ? Math.max(1, trades - 1) : trades > 1 ? 1 : 0;
    const losses = trades - wins;
    days.push({
      date: isoDate(ref.year, ref.month, day),
      net_pnl: pnl.toFixed(2),
      trade_count: trades,
      wins,
      losses,
      breakeven: 0,
      win_rate: wins / trades,
    });
  }
  let running = 0;
  let peak = 0;
  let maxDrawdown = 0;
  const cumulative = days.map((day) => {
    running += Number(day.net_pnl);
    peak = Math.max(peak, running);
    maxDrawdown = Math.max(maxDrawdown, peak - running);
    return { date: day.date, daily_net_pnl: day.net_pnl ?? "0", cumulative_net_pnl: running.toFixed(2) };
  });
  const pnls = days.map((day) => Number(day.net_pnl));
  const profits = pnls.filter((value) => value > 0);
  const lossesList = pnls.filter((value) => value < 0);
  const grossProfit = profits.reduce((a, b) => a + b, 0);
  const grossLoss = lossesList.reduce((a, b) => a + b, 0);
  const wins = days.reduce((a, d) => a + d.wins, 0);
  const losses = days.reduce((a, d) => a + d.losses, 0);
  const avgWin = profits.length ? grossProfit / profits.length : null;
  const avgLoss = lossesList.length ? grossLoss / lossesList.length : null;
  const profitFactor = grossLoss < 0 ? grossProfit / -grossLoss : null;
  const ratio = avgWin !== null && avgLoss !== null ? avgWin / -avgLoss : null;
  const recovery = maxDrawdown > 0 ? running / maxDrawdown : null;
  const consistency = profits.length >= 2 ? 1 - Math.max(...profits) / grossProfit : null;
  const winRate = wins + losses ? wins / (wins + losses) : null;
  const clamp = (value: number) => Math.round(Math.max(0, Math.min(1, value)) * 10000) / 100;
  const components = [
    { key: "win_rate", label: "Win %", value: winRate, score: winRate === null ? null : clamp(winRate / 0.6), target: 0.6, note: null },
    { key: "profit_factor", label: "Profit factor", value: profitFactor, score: profitFactor === null ? null : clamp(profitFactor / 2.5), target: 2.5, note: null },
    { key: "win_loss_ratio", label: "Avg win/loss", value: ratio, score: ratio === null ? null : clamp(ratio / 2.5), target: 2.5, note: null },
    { key: "recovery_factor", label: "Recovery factor", value: recovery, score: recovery === null ? null : clamp(recovery / 3), target: 3, note: null },
    { key: "max_drawdown", label: "Max drawdown", value: grossProfit ? maxDrawdown / grossProfit : null, score: grossProfit ? clamp(1 - maxDrawdown / grossProfit / 0.5) : null, target: 0.5, note: null },
    { key: "consistency", label: "Consistency", value: consistency, score: consistency === null ? null : clamp(consistency), target: null, note: null },
  ];
  const scored = components.map((c) => c.score).filter((s): s is number => s !== null);
  const tradeCount = wins + losses;
  return {
    filters: {
      ...monthRange(ref),
      scope: DEFAULT_SCOPE,
      scope_label: SCOPE_LABELS[DEFAULT_SCOPE],
      timezone: "UTC",
      source: null,
      included_sources: [],
    },
    summary: {
      trade_count: tradeCount,
      pnl_sample_count: tradeCount,
      wins,
      losses,
      breakeven: 0,
      win_rate: winRate,
      net_pnl: running.toFixed(2),
      gross_profit: grossProfit.toFixed(2),
      gross_loss: grossLoss.toFixed(2),
      profit_factor: profitFactor,
      average_win: avgWin === null ? null : avgWin.toFixed(2),
      average_loss: avgLoss === null ? null : avgLoss.toFixed(2),
      win_loss_ratio: ratio,
      expectancy: tradeCount ? (running / tradeCount).toFixed(2) : null,
    },
    days,
    cumulative,
    risk: {
      max_drawdown: maxDrawdown.toFixed(2),
      recovery_factor: recovery,
      consistency,
      winning_days: profits.length,
      losing_days: lossesList.length,
    },
    score: {
      overall: scored.length ? Math.round((scored.reduce((a, b) => a + b, 0) / scored.length) * 100) / 100 : null,
      components,
      missing_components: components.filter((c) => c.score === null).map((c) => c.key),
    },
    confidence: "low",
    truncated: false,
    max_rows: 0,
    oldest_covered_date: null,
    open_trades_excluded: 0,
    warnings: ["Sample data — illustrative only, not your trades."],
    generated_at: new Date(Date.UTC(ref.year, ref.month, 1)).toISOString(),
  };
}
