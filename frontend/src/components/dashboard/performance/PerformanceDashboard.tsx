"use client";

import { useEffect, useMemo, useState } from "react";

import { UnavailableState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { api } from "@/lib/api";
import type {
  JournalPerformanceDashboardParams,
  JournalPerformanceDashboardResponse,
} from "@/lib/api/types";
import { humanizeToken } from "@/lib/format";

import { CumulativePnlChart } from "./CumulativePnlChart";
import { PerformanceKpiCards } from "./PerformanceKpiCards";
import { PnlCalendar } from "./PnlCalendar";
import { ScoreRadar } from "./ScoreRadar";
import {
  RANGE_OPTIONS,
  browserTimezone,
  buildSampleDashboard,
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
    setState((previous) => ({ ...previous, loading: true, error: false }));
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
  const [month, setMonth] = useState<MonthRef>(thisMonth);
  const [sample, setSample] = useState(false);
  const tz = useMemo(() => browserTimezone(), []);

  const rangeQuery = useMemo(() => ({ ...rangeParams(preset, today), tz }), [preset, today, tz]);
  const monthQuery = useMemo(() => ({ ...monthRange(month), tz }), [month, tz]);
  const range = useDashboardRequest(rangeQuery, !sample, refreshKey);
  const calendar = useDashboardRequest(monthQuery, !sample, refreshKey);

  const sampleData = useMemo(() => (sample ? buildSampleDashboard(month) : null), [sample, month]);
  const rangeData = sample ? sampleData : range.data;
  const monthData = sample ? sampleData : calendar.data;
  const empty = !sample && !range.loading && rangeData?.summary.trade_count === 0;

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
        </div>
        <div className="flex flex-wrap items-center gap-3">
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
          <PerformanceKpiCards summary={range.loading && !sample ? null : rangeData?.summary ?? null} />
          <div className="grid gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <PnlCalendar
              month={month}
              days={monthData?.days ?? []}
              summary={monthData?.summary ?? null}
              loading={!sample && calendar.loading}
              onPrev={() => setMonth((current) => shiftMonth(current, -1))}
              onNext={() => setMonth((current) => shiftMonth(current, 1))}
              onToday={() => setMonth(thisMonth)}
            />
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-1">
              <ScoreRadar
                overall={rangeData?.score.overall ?? null}
                components={rangeData?.score.components ?? []}
              />
              <CumulativePnlChart points={rangeData?.cumulative ?? []} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
