"use client";

import { useCallback, useMemo, useState } from "react";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { SectionHeader } from "@/components/ui/section-header";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type {
  DailyReview,
  ReviewSource,
  ReviewTopic,
} from "@/lib/api/daily-review-types";
import { formatDateTime, humanizeToken } from "@/lib/format";
import { timeZoneOptions, zonedIsoDate } from "@/lib/timezone";

const TOPICS: Record<ReviewTopic, string> = {
  watcher_activity: "Watcher activity",
  setups: "Forming and confirmed setups",
  blocked_candidates: "Blocked candidates and reasons",
  paper_opened: "Paper trades opened",
  paper_closed: "Paper trades closed",
  risk_events: "Risk events",
  journal_entries: "Journal reflections",
  mistakes: "Mistakes",
  lessons: "Lessons",
  missed_setups: "Missed setups",
  strategy_observations: "Strategy observations",
  data_quality_limitations: "Data quality limitations",
};
const SECTIONS = [
  ["facts", "Facts"],
  ["user_observations", "User observations"],
  ["system_inference", "System inference"],
  ["research_suggestions", "Research suggestions"],
] as const;

function Sources({ sources }: { sources: ReviewSource[] }) {
  return (
    <details className="mt-2 text-xs text-text-muted">
      <summary className="cursor-pointer">Sources and timestamps</summary>
      <ul className="mt-1 space-y-1 break-words">
        {sources.map((source, index) => (
          <li key={index}>
            {humanizeToken(source.record_type)} ·{" "}
            <time dateTime={source.occurred_at}>{formatDateTime(source.occurred_at)}</time>
            {source.version !== null ? ` · version ${source.version}` : ""}
            {source.upstream_system ? ` · ${source.upstream_system}` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

function hasReviewContent(
  review: DailyReview | null | undefined,
): review is DailyReview {
  return (
    review?.schema_version === "DailyReview/v1" &&
    typeof review.window?.day === "string" &&
    typeof review.window?.timezone === "string" &&
    typeof review.generated_at === "string" &&
    review.counts != null &&
    Object.keys(TOPICS).every(
      (topic) => typeof review.counts[topic as ReviewTopic] === "number",
    ) &&
    Array.isArray(review.daily_pnl) &&
    Array.isArray(review.limitations) &&
    SECTIONS.every(([key]) => Array.isArray(review[key]))
  );
}

export function DailyReviewContent({
  review,
}: {
  review?: DailyReview | null;
}) {
  if (!hasReviewContent(review)) {
    return (
      <p
        className="text-sm text-text-muted"
        data-testid="daily-review-unavailable"
      >
        Daily Review unavailable — no review data was returned.
      </p>
    );
  }

  return (
    <div className="space-y-3 text-sm" data-testid="daily-review-content">
      <p className="text-xs text-text-muted">
        {review.window.day} · {review.window.timezone} · Generated{" "}
        {review.generated_at}
      </p>
      <p className="text-xs text-text-muted">
        Watcher and setups cover your organization; journal, risk and PnL cover
        your user. Counts describe recorded review items. Missing records do not
        prove inactivity.
      </p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {Object.entries(TOPICS).map(([topic, label]) => (
          <p key={topic} className="text-xs text-text-secondary">
            {label}: {review.counts[topic as ReviewTopic]}
          </p>
        ))}
      </div>
      <details>
        <summary className="cursor-pointer font-medium">
          Realized daily PnL and sample statistics
        </summary>
        {review.daily_pnl.length === 0 ? (
          <p className="mt-2 text-text-muted">
            Unavailable — no recorded paper closes for this day.
          </p>
        ) : (
          review.daily_pnl.map((pnl) => (
            <div
              key={pnl.cohort}
              className="mt-2 rounded border border-border-subtle p-3 break-words"
            >
              <p>{pnl.cohort}</p>
              <p>
                Recorded net PnL: {pnl.recorded_net_pnl ?? "Unavailable"}{" "}
                {pnl.complete ? "" : "(incomplete)"}
              </p>
              <p className="text-xs text-text-muted">
                {pnl.measured_count} measured / {pnl.closed_count} closes ·{" "}
                {pnl.missing_pnl_count} missing PnL · minimum sample{" "}
                {pnl.minimum_sample}
              </p>
              <p>
                Win rate:{" "}
                {pnl.win_rate === null
                  ? "Unavailable"
                  : `${(Number(pnl.win_rate) * 100).toFixed(1)}%`}{" "}
                · Expectancy: {pnl.expectancy ?? "Unavailable"}
              </p>
              {pnl.win_rate === null || pnl.expectancy === null ? (
                <p className="text-xs text-text-muted">
                  Statistics require a complete cohort with at least{" "}
                  {pnl.minimum_sample} measured closes.
                </p>
              ) : null}
              <Sources sources={pnl.sources} />
            </div>
          ))
        )}
        <p className="mt-2 text-xs text-text-muted">
          Accounting units only; currency is unavailable. Cohorts remain
          separate. Daily samples do not establish profitability.
        </p>
      </details>
      {SECTIONS.map(([key, label]) => (
        <details key={key}>
          <summary className="cursor-pointer font-medium">
            {label} ({review[key].length})
          </summary>
          {review[key].length === 0 ? (
            <p className="mt-2 text-text-muted">No recorded items.</p>
          ) : (
            <ul className="mt-2 space-y-2">
              {review[key].map((item, index) => (
                <li
                  key={index}
                  className="rounded border border-border-subtle p-3 break-words"
                >
                  <p className="font-medium">{TOPICS[item.topic]}</p>
                  <p>
                    {item.code === "watch" || item.code === "partial_match"
                      ? `Forming · ${item.code}`
                      : item.code}
                  </p>
                  {item.text ? (
                    <p className="mt-1 whitespace-pre-wrap">{item.text}</p>
                  ) : null}
                  <Sources sources={item.sources} />
                </li>
              ))}
            </ul>
          )}
        </details>
      ))}
      <details>
        <summary className="cursor-pointer font-medium">
          Coverage and data quality limitations
        </summary>
        <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-text-muted">
          {review.limitations.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

export function DailyReviewCard({
  compact = false,
  timezone: resolvedTimezone,
}: {
  compact?: boolean;
  /**
   * Shared day-bucket zone (risk settings → browser → UTC). `null` while it
   * resolves (no request yet); omitted = UTC. The trader can still pick another.
   */
  timezone?: string | null;
}) {
  const defaultTimezone = resolvedTimezone === undefined ? "UTC" : resolvedTimezone;
  // Follow the resolved zone (and its today) until the trader edits a field.
  const [timezoneEdit, setTimezoneEdit] = useState<string | null>(null);
  const [dateEdit, setDateEdit] = useState<string | null>(null);
  const timezone = timezoneEdit ?? defaultTimezone ?? "UTC";
  const date = dateEdit ?? zonedIsoDate(new Date(), timezone);
  const [submitted, setSubmitted] = useState<{ date: string; timezone: string } | null>(null);
  const query = useMemo(
    () => submitted ?? (defaultTimezone === null ? null : { date, timezone }),
    // Unsubmitted edits must not refetch; only the resolved default may.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [submitted, defaultTimezone],
  );
  const loader = useCallback(
    () =>
      query
        ? api.dashboard.dailyReview(query)
        : new Promise<DailyReview>(() => {
            // Wait for the shared timezone before the first request.
          }),
    [query],
  );
  const { data, loading, error, reload } = useAsyncData(loader, [query]);
  const zones = useMemo(() => timeZoneOptions(timezone), [timezone]);

  const content = (
    <>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (query && query.date === date && query.timezone === timezone) void reload();
          else setSubmitted({ date, timezone });
        }}
      >
        <label className="min-w-0 text-xs text-text-muted">
          Date
          <input
            aria-label="Review date"
            type="date"
            required
            value={date}
            onChange={(event) => setDateEdit(event.target.value)}
            className="mt-1 block max-w-full rounded border border-border-subtle bg-background p-2 text-sm"
          />
        </label>
        <label className="min-w-0 text-xs text-text-muted">
          Timezone
          <select
            aria-label="Review timezone"
            required
            value={timezone}
            onChange={(event) => setTimezoneEdit(event.target.value)}
            className="mt-1 block min-h-11 w-48 max-w-full rounded border border-border-subtle bg-background p-2 text-sm"
          >
            {zones.map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          disabled={loading}
          className="rounded border border-border-subtle px-3 py-2 text-sm"
        >
          Review day
        </button>
      </form>
      {loading ? (
        <p role="status" className="text-sm text-text-muted">
          Loading Daily Review…
        </p>
      ) : error ? (
        <div role="alert" className="text-sm text-text-muted">
          <p>
            Daily Review unavailable. Check the date and timezone, then
            retry.
          </p>
          <button
            type="button"
            onClick={() => void reload()}
            className="mt-1 underline"
          >
            Retry
          </button>
        </div>
      ) : (
        <DailyReviewContent review={data} />
      )}
    </>
  );
  return (
    <Card data-testid="dashboard-daily-review">
      <CardHeader className="gap-2 pb-2">
        <SectionHeader eyebrow="Discipline" title="Daily Review" />
      </CardHeader>
      <CardContent className="card-inner pt-0">
        {compact ? (
          <details>
            <summary className="min-h-11 cursor-pointer text-sm">
              Open daily review
            </summary>
            {content}
          </details>
        ) : (
          content
        )}
      </CardContent>
    </Card>
  );
}
