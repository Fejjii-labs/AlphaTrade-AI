import { ChevronLeft, ChevronRight } from "lucide-react";

import type { JournalDashboardDay, JournalDashboardSummary } from "@/lib/api/types";
import { formatCount, formatMonetary, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  buildCalendarWeeks,
  formatCompactPnl,
  monthLabel,
  pnlTone,
  type MonthRef,
} from "./performance-model";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const cellTone = {
  profit: "border-success-border bg-success-muted",
  loss: "border-danger-border bg-danger-muted",
  flat: "border-border bg-surface-2",
  none: "border-border bg-surface-2",
} as const;

const valueTone = {
  profit: "text-success",
  loss: "text-danger",
  flat: "text-text-primary",
  none: "text-text-muted",
} as const;

function DayCell({ date, day }: { date: string; day: JournalDashboardDay | undefined }) {
  const dayNumber = Number(date.slice(8));
  if (!day) {
    return (
      <div className="min-h-[64px] rounded-control border border-transparent p-1.5 sm:min-h-[84px]">
        <span className="block text-right text-[11px] text-text-muted">{dayNumber}</span>
      </div>
    );
  }
  const tone = pnlTone(day.net_pnl);
  const label = `${date}: ${formatMonetary(day.net_pnl)} net, ${day.trade_count} trade${
    day.trade_count === 1 ? "" : "s"
  }, win ${formatPercent(day.win_rate, 0)}`;
  return (
    <div
      className={cn(
        "flex min-h-[64px] min-w-0 flex-col overflow-hidden rounded-control border p-1 text-center sm:min-h-[84px] sm:p-1.5",
        cellTone[tone],
      )}
      title={label}
      aria-label={label}
      data-testid={`calendar-day-${date}`}
      data-tone={tone}
    >
      <span className="block text-right text-[11px] text-text-secondary">{dayNumber}</span>
      <span className={cn("truncate font-data text-[10px] font-semibold sm:text-sm", valueTone[tone])}>
        {formatCompactPnl(day.net_pnl)}
      </span>
      <span className="hidden text-[11px] text-text-secondary sm:block">
        {day.trade_count} trade{day.trade_count === 1 ? "" : "s"}
      </span>
      <span className="hidden text-[11px] text-text-muted sm:block">
        {formatPercent(day.win_rate, 0)}
      </span>
    </div>
  );
}

export function PnlCalendar({
  month,
  days,
  summary,
  loading = false,
  error = false,
  onPrev,
  onNext,
  onToday,
}: {
  month: MonthRef;
  days: JournalDashboardDay[];
  summary: JournalDashboardSummary | null;
  loading?: boolean;
  error?: boolean;
  onPrev: () => void;
  onNext: () => void;
  onToday: () => void;
}) {
  const byDate = new Map(days.map((day) => [day.date, day]));
  const weeks = buildCalendarWeeks(month, days);
  const monthTone = pnlTone(summary?.net_pnl);
  return (
    <section
      className="rounded-card border border-border-subtle bg-surface-1 p-4"
      aria-label="Monthly P&L calendar"
      data-testid="pnl-calendar"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onPrev}
            aria-label="Previous month"
            className="inline-flex h-9 w-9 items-center justify-center rounded-control text-text-secondary hover:bg-surface-2"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <h3 className="min-w-[8.5rem] text-center text-sm font-semibold text-text-primary" data-testid="calendar-month">
            {monthLabel(month)}
          </h3>
          <button
            type="button"
            onClick={onNext}
            aria-label="Next month"
            className="inline-flex h-9 w-9 items-center justify-center rounded-control text-text-secondary hover:bg-surface-2"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onToday}
            className="ml-1 h-9 whitespace-nowrap rounded-control border border-border-subtle px-3 text-xs text-text-secondary hover:bg-surface-2"
          >
            This month
          </button>
        </div>
        <p className="text-xs text-text-secondary" data-testid="calendar-month-total">
          Monthly:{" "}
          <span className={cn("font-data font-semibold", valueTone[monthTone])}>
            {loading ? "…" : formatMonetary(error ? null : summary?.net_pnl)}
          </span>
          {loading || error ? null : (
            <>
              {" "}
              · <span className="font-data">{formatCount(days.length)}</span> trading day
              {days.length === 1 ? "" : "s"}
            </>
          )}
        </p>
      </div>
      {error ? (
        <p
          role="alert"
          className="mb-3 rounded-control border border-danger-border bg-danger-muted px-3 py-2 text-xs text-danger"
          data-testid="calendar-error"
        >
          Calendar unavailable for {monthLabel(month)}. Daily figures for this month could not be
          loaded; this is not a zero-P&amp;L month.
        </p>
      ) : null}

      <div className="grid grid-cols-7 gap-1 md:grid-cols-[repeat(7,minmax(0,1fr))_minmax(84px,0.9fr)]">
        {WEEKDAYS.map((name) => (
          <div key={name} className="pb-1 text-center text-[11px] font-medium text-text-muted">
            {name}
          </div>
        ))}
        <div className="hidden pb-1 text-center text-[11px] font-medium text-text-muted md:block">
          Week
        </div>
        {weeks.map((week) => (
          <div key={week.index} className="contents">
            {week.cells.map((date, index) =>
              date ? (
                <DayCell key={date} date={date} day={byDate.get(date)} />
              ) : (
                <div key={`blank-${week.index}-${index}`} aria-hidden="true" />
              ),
            )}
            <div
              className="hidden flex-col justify-center rounded-control bg-surface-2 px-2 py-1.5 md:flex"
              data-testid={`calendar-week-${week.index}`}
            >
              <span className="text-[11px] text-text-muted">Week {week.index}</span>
              <span
                className={cn("font-data text-sm font-semibold", valueTone[pnlTone(week.netPnl)])}
              >
                {formatCompactPnl(week.netPnl)}
              </span>
              <span className="text-[11px] text-text-secondary">
                {week.tradingDays} day{week.tradingDays === 1 ? "" : "s"}
              </span>
            </div>
          </div>
        ))}
      </div>
      <ul className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-3 md:hidden" aria-label="Weekly totals">
        {weeks.map((week) => (
          <li key={week.index} className="rounded-control bg-surface-2 px-2 py-1.5">
            Week {week.index}:{" "}
            <span className={cn("font-data font-semibold", valueTone[pnlTone(week.netPnl)])}>
              {formatCompactPnl(week.netPnl)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
