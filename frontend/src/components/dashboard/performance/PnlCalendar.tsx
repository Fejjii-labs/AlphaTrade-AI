import { ChevronLeft, ChevronRight, RotateCcw } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";

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

const NAV_BUTTON =
  "inline-flex h-9 items-center justify-center rounded-control text-text-secondary hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus";

function DayCell({
  date,
  day,
  isToday,
  loading,
}: {
  date: string;
  day: JournalDashboardDay | undefined;
  isToday: boolean;
  loading: boolean;
}) {
  const dayNumber = Number(date.slice(8));
  const todayRing = isToday ? "ring-2 ring-accent ring-offset-1 ring-offset-surface-1" : "";
  const todaySuffix = isToday ? " (today)" : "";
  if (loading) {
    return (
      <div role="cell" className="min-h-[64px] p-0.5 sm:min-h-[84px]">
        <Skeleton className="h-full min-h-[60px] w-full sm:min-h-[80px]" />
      </div>
    );
  }
  if (!day) {
    return (
      <div
        role="cell"
        aria-label={`${date}${todaySuffix}: no closed trades`}
        aria-current={isToday ? "date" : undefined}
        className={cn(
          "min-h-[64px] rounded-control border border-transparent p-1.5 sm:min-h-[84px]",
          todayRing,
        )}
      >
        <span className={cn("block text-right text-[11px]", isToday ? "font-semibold text-accent" : "text-text-muted")}>
          {dayNumber}
        </span>
      </div>
    );
  }
  const tone = pnlTone(day.net_pnl);
  const label = `${date}${todaySuffix}: ${formatMonetary(day.net_pnl)} net, ${day.trade_count} trade${
    day.trade_count === 1 ? "" : "s"
  }, win ${formatPercent(day.win_rate, 0)}`;
  return (
    <div
      role="cell"
      className={cn(
        "flex min-h-[64px] min-w-0 flex-col overflow-hidden rounded-control border p-1 text-center sm:min-h-[84px] sm:p-1.5",
        cellTone[tone],
        todayRing,
      )}
      title={label}
      aria-label={label}
      aria-current={isToday ? "date" : undefined}
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
  todayIso = null,
  onPrev,
  onNext,
  onToday,
  onRetry,
}: {
  month: MonthRef;
  days: JournalDashboardDay[];
  summary: JournalDashboardSummary | null;
  loading?: boolean;
  error?: boolean;
  /** Local ISO date to outline; null hides the marker. */
  todayIso?: string | null;
  onPrev: () => void;
  onNext: () => void;
  onToday: () => void;
  onRetry?: () => void;
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
            className={cn(NAV_BUTTON, "w-9")}
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <h3
            className="min-w-[8.5rem] text-center text-sm font-semibold text-text-primary"
            data-testid="calendar-month"
            aria-live="polite"
          >
            {monthLabel(month)}
          </h3>
          <button
            type="button"
            onClick={onNext}
            aria-label="Next month"
            className={cn(NAV_BUTTON, "w-9")}
          >
            <ChevronRight className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onToday}
            className={cn(NAV_BUTTON, "ml-1 whitespace-nowrap border border-border-subtle px-3 text-xs")}
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
          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="ml-2 inline-flex items-center gap-1 font-medium underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
            >
              <RotateCcw className="h-3 w-3" aria-hidden="true" />
              Retry
            </button>
          ) : null}
        </p>
      ) : null}

      <div
        role="table"
        aria-label={`Daily net P&L, ${monthLabel(month)}`}
        aria-busy={loading || undefined}
        className="grid grid-cols-7 gap-1 md:grid-cols-[repeat(7,minmax(0,1fr))_minmax(84px,0.9fr)]"
      >
        <div role="row" className="contents">
          {WEEKDAYS.map((name) => (
            <div
              key={name}
              role="columnheader"
              className="pb-1 text-center text-[11px] font-medium text-text-muted"
            >
              {name}
            </div>
          ))}
          <div
            role="columnheader"
            className="hidden pb-1 text-center text-[11px] font-medium text-text-muted md:block"
          >
            Week
          </div>
        </div>
        {weeks.map((week) => (
          <div key={week.index} role="row" className="contents">
            {week.cells.map((date, index) =>
              date ? (
                <DayCell
                  key={date}
                  date={date}
                  day={byDate.get(date)}
                  isToday={date === todayIso}
                  loading={loading}
                />
              ) : (
                <div key={`blank-${week.index}-${index}`} role="cell" />
              ),
            )}
            <div
              role="cell"
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
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-secondary" aria-hidden="true">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-3 w-3 rounded-sm border border-success-border bg-success-muted" /> Net profit day
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-3 w-3 rounded-sm border border-danger-border bg-danger-muted" /> Net loss day
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-3 w-3 rounded-sm border border-border bg-surface-2" /> Flat day
        </span>
        {todayIso ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="h-3 w-3 rounded-sm ring-2 ring-accent" /> Today
          </span>
        ) : null}
      </div>
      {loading ? null : (
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
      )}
    </section>
  );
}
