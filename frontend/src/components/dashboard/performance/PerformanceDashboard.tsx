"use client";

import { RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { UnavailableState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { api } from "@/lib/api";
import type {
  JournalDashboardScope,
  JournalPerformanceDashboardParams,
  JournalPerformanceDashboardResponse,
  JournalTradeSource,
} from "@/lib/api/types";
import { humanizeToken } from "@/lib/format";

import { CumulativePnlChart } from "./CumulativePnlChart";
import { PerformanceKpiCards } from "./PerformanceKpiCards";
import { PnlCalendar } from "./PnlCalendar";
import { ScoreRadar } from "./ScoreRadar";
import {
  DEFAULT_SCOPE,
  EXTRA_SCOPE_OPTIONS,
  RANGE_OPTIONS,
  SCOPE_LABELS,
  SCOPE_OPTIONS,
  SOURCE_LABELS,
  browserTimezone,
  buildSampleDashboard,
  customRangeError,
  monthRange,
  rangeParams,
  shiftMonth,
  type MonthRef,
  type RangePreset,
} from "./performance-model";

type Loadable = {
  data: JournalPerformanceDashboardResponse | null;
  loading: boolean;
  error: boolean;
};

const FIELD =
  "min-h-9 rounded-control border border-border bg-surface-2 px-2 text-xs text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus aria-[invalid=true]:border-danger";

const IDLE: Loadable = { data: null, loading: true, error: false };

function useDashboardRequest(
  params: JournalPerformanceDashboardParams,
  enabled: boolean,
  reloadKey: number,
): Loadable {
  const [state, setState] = useState<Loadable>(IDLE);
  const key = JSON.stringify(params);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    // Drop the previous response so no panel shows another range/month as current.
    setState(IDLE);
    api.journal
      .performanceCalendar(JSON.parse(key) as JournalPerformanceDashboardParams, {
        signal: controller.signal,
      })
      .then((data) => setState({ data, loading: false, error: false }))
      .catch(() => {
        if (!controller.signal.aborted) setState({ data: null, loading: false, error: true });
      });
    return () => controller.abort();
  }, [key, enabled, reloadKey]);
  return state;
}

export function PerformanceDashboard({
  now,
  refreshKey = 0,
}: {
  /** Injected for deterministic tests; defaults to the current time. */
  now?: Date;
  refreshKey?: number;
}) {
  const today = useMemo(() => now ?? new Date(), [now]);
  const thisMonth: MonthRef = { year: today.getFullYear(), month: today.getMonth() };
  const [preset, setPreset] = useState<RangePreset>("90d");
  const [scope, setScope] = useState<JournalDashboardScope>(DEFAULT_SCOPE);
  const [month, setMonth] = useState<MonthRef>(thisMonth);
  const [sample, setSample] = useState(false);
  const [source, setSource] = useState<JournalTradeSource | "">("");
  const [sourceOptions, setSourceOptions] = useState<JournalTradeSource[]>([]);
  const defaultCustom = useMemo(() => rangeParams("30d", today), [today]);
  const [customFrom, setCustomFrom] = useState(defaultCustom.date_from ?? "");
  const [customTo, setCustomTo] = useState(defaultCustom.date_to ?? "");
  const tz = useMemo(() => browserTimezone(), []);

  const customError = preset === "custom" ? customRangeError(customFrom, customTo) : null;
  const rangeBlocked = !sample && customError !== null;
  const rangeQuery = useMemo(
    () => ({
      ...(preset === "custom" ? { date_from: customFrom, date_to: customTo } : rangeParams(preset, today)),
      scope,
      tz,
      ...(source ? { source } : {}),
    }),
    [preset, customFrom, customTo, scope, source, today, tz],
  );
  const monthQuery = useMemo(
    () => ({ ...monthRange(month), scope, tz, ...(source ? { source } : {}) }),
    [month, scope, source, tz],
  );
  const range = useDashboardRequest(rangeQuery, !sample && !rangeBlocked, refreshKey);
  const calendar = useDashboardRequest(monthQuery, !sample, refreshKey);

  // Remember the scope's sources so the picker survives the next reload.
  const loadedFilters = range.data?.filters;
  useEffect(() => {
    if (loadedFilters && loadedFilters.scope === scope) setSourceOptions(loadedFilters.included_sources);
  }, [loadedFilters, scope]);

  const changeScope = (next: JournalDashboardScope) => {
    setScope(next);
    setSource("");
    setSourceOptions([]);
  };
  const filtersChanged = preset !== "90d" || scope !== DEFAULT_SCOPE || source !== "";
  const resetFilters = () => {
    setPreset("90d");
    changeScope(DEFAULT_SCOPE);
  };

  const sampleData = useMemo(() => (sample ? buildSampleDashboard(month) : null), [sample, month]);
  const rangeData = sample ? sampleData : rangeBlocked ? null : range.data;
  const rangeLoading = !sample && !rangeBlocked && range.loading;
  const monthData = sample ? sampleData : calendar.data;
  const empty = !sample && !rangeLoading && rangeData?.summary.trade_count === 0;
  const echoed = rangeData?.filters;
  const rangeText = echoed
    ? echoed.date_from || echoed.date_to
      ? `${echoed.date_from ?? "start"} → ${echoed.date_to ?? "today"}`
      : "all time"
    : null;
  const scopeLabel = range.data?.filters.scope_label ?? SCOPE_LABELS[scope];
  const extraScope = EXTRA_SCOPE_OPTIONS.some((option) => option.value === scope);

  return (
    <div className="space-y-3" data-testid="performance-dashboard" data-sample={sample ? "true" : "false"}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-section">Performance</h2>
          {sample ? (
            <Badge variant="warning" data-testid="sample-data-badge">
              Sample data · not your trades
            </Badge>
          ) : rangeData ? (
            <Badge variant="muted" title="Sample-size confidence">
              {humanizeToken(rangeData.confidence)} confidence
            </Badge>
          ) : null}
          {!sample && rangeData?.truncated ? (
            <Badge variant="warning" data-testid="performance-truncated">
              Newest {rangeData.max_rows} trades
              {rangeData.oldest_covered_date ? ` since ${rangeData.oldest_covered_date}` : ""}
            </Badge>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <SegmentedControl
            aria-label="Account scope"
            value={extraScope ? ("" as JournalDashboardScope) : scope}
            onChange={changeScope}
            options={SCOPE_OPTIONS.map((option) => ({ ...option, disabled: sample }))}
          />
          <label className="inline-flex items-center gap-1.5 text-xs text-text-secondary">
            <span className="sr-only sm:not-sr-only">Other</span>
            <select
              aria-label="Other trade scopes"
              value={extraScope ? scope : ""}
              disabled={sample}
              onChange={(event) =>
                changeScope((event.target.value || DEFAULT_SCOPE) as JournalDashboardScope)
              }
              className="min-h-9 rounded-control border border-border bg-surface-1 px-2 text-xs text-text-primary"
            >
              <option value="">—</option>
              {EXTRA_SCOPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <SegmentedControl
            aria-label="Performance range"
            value={preset}
            onChange={setPreset}
            options={RANGE_OPTIONS.map((option) => ({ ...option, disabled: sample }))}
          />
          <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 text-xs text-text-secondary">
            <input
              type="checkbox"
              checked={sample}
              onChange={(event) => setSample(event.target.checked)}
              className="h-4 w-4 accent-[var(--color-warning)]"
            />
            Sample data
          </label>
        </div>
      </div>

      {!sample && (preset === "custom" || sourceOptions.length > 1 || filtersChanged) ? (
        <div
          className="flex flex-wrap items-end gap-3 rounded-control border border-border-subtle bg-surface-1 px-3 py-2"
          data-testid="performance-filters"
        >
          {preset === "custom" ? (
            <fieldset className="flex flex-wrap items-end gap-2">
              <legend className="sr-only">Custom date range</legend>
              <label className="flex flex-col gap-1 text-xs text-text-secondary">
                From
                <input
                  type="date"
                  value={customFrom}
                  max={customTo || undefined}
                  onChange={(event) => setCustomFrom(event.target.value)}
                  aria-invalid={customError ? true : undefined}
                  aria-describedby={customError ? "performance-range-error" : undefined}
                  className={FIELD}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-text-secondary">
                To
                <input
                  type="date"
                  value={customTo}
                  min={customFrom || undefined}
                  onChange={(event) => setCustomTo(event.target.value)}
                  aria-invalid={customError ? true : undefined}
                  aria-describedby={customError ? "performance-range-error" : undefined}
                  className={FIELD}
                />
              </label>
            </fieldset>
          ) : null}
          {sourceOptions.length > 1 || source ? (
            <label className="flex flex-col gap-1 text-xs text-text-secondary">
              Source
              <select
                value={source}
                onChange={(event) => setSource(event.target.value as JournalTradeSource | "")}
                className={FIELD}
              >
                <option value="">All sources in this scope</option>
                {sourceOptions.map((option) => (
                  <option key={option} value={option}>
                    {SOURCE_LABELS[option] ?? humanizeToken(option)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {filtersChanged ? (
            <button
              type="button"
              onClick={resetFilters}
              className="inline-flex min-h-9 items-center gap-1.5 rounded-control px-2 text-xs text-text-secondary hover:bg-surface-2 hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              Reset filters
            </button>
          ) : null}
          {customError ? (
            <p id="performance-range-error" role="alert" className="basis-full text-xs text-danger">
              {customError}
            </p>
          ) : null}
        </div>
      ) : null}

      {!sample ? (
        <p className="text-sm font-medium text-text-primary" data-testid="performance-scope">
          Showing: {scopeLabel}
          {source ? ` · ${SOURCE_LABELS[source] ?? humanizeToken(source)} only` : ""}
          {rangeText ? <span className="font-data text-xs font-normal text-text-secondary"> · {rangeText}</span> : null}
          <span className="ml-2 text-xs font-normal text-text-muted">
            Closed trades only; scopes are never mixed. Partial exits on open trades count only
            once the trade closes.
          </span>
          {rangeData?.open_trades_excluded ? (
            <span className="ml-2 text-xs font-normal text-text-muted" data-testid="performance-open-excluded">
              ({rangeData.open_trades_excluded} open trade{rangeData.open_trades_excluded === 1 ? "" : "s"} not
              counted)
            </span>
          ) : null}
        </p>
      ) : null}

      {sample ? (
        <p
          role="status"
          className="rounded-control border border-warning-border bg-warning-muted px-3 py-2 text-xs text-badge-warning"
        >
          Showing illustrative sample data. None of these numbers come from your journal.
          Turn off “Sample data” to see your closed trades.
        </p>
      ) : null}

      {!sample && range.error ? (
        <UnavailableState message="Journal performance unavailable" className="py-4" />
      ) : (
        <>
          {empty ? (
            <p
              role="status"
              className="rounded-control border border-dashed border-border bg-surface-1 px-3 py-2 text-sm text-text-muted"
              data-testid="performance-empty"
            >
              No closed journal trades in this range yet. Figures appear once trades close.
            </p>
          ) : null}
          {rangeData?.warnings.length && !sample && !empty ? (
            <ul className="space-y-0.5 text-xs text-text-muted" data-testid="performance-warnings">
              {rangeData.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          ) : null}
          <PerformanceKpiCards
            summary={rangeData?.summary ?? null}
            risk={rangeData?.risk ?? null}
            loading={rangeLoading}
          />
          <div className="grid gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <PnlCalendar
              month={month}
              days={monthData?.days ?? []}
              summary={monthData?.summary ?? null}
              loading={!sample && calendar.loading}
              error={!sample && calendar.error}
              onPrev={() => setMonth((current) => shiftMonth(current, -1))}
              onNext={() => setMonth((current) => shiftMonth(current, 1))}
              onToday={() => setMonth(thisMonth)}
            />
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-1">
              <ScoreRadar
                overall={rangeData?.score.overall ?? null}
                components={rangeData?.score.components ?? []}
                confidence={rangeData?.confidence ?? null}
              />
              <CumulativePnlChart points={rangeData?.cumulative ?? []} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
