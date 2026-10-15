"use client";

import { useMemo } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import type { SourceResult } from "@/components/workflows";
import type { JournalStatsResponse, SampleConfidence } from "@/lib/api/types";

import { ChartFrame } from "./ChartFrame";
import { JournalStatisticsLink } from "./JournalStatisticsLink";
import { SETUP_BUCKET_PAGE_SIZE, type SetupGroupBy } from "./filterValidation";
import { formatMonetary, formatPercent } from "./format";
import { buildSetupBucketLinks } from "./setupBucketLinks";
import { buildSetupBucketRows, type SetupBucketRow } from "./setupBucketTransforms";
import { setupGroupCopy } from "./setupGroupCopy";

const CONFIDENCE_TONE: Record<SampleConfidence, "ok" | "warn" | "critical"> = {
  high: "ok",
  moderate: "ok",
  low: "warn",
  insufficient: "critical",
};

export type SetupBucketTableProps = {
  source: SourceResult<JournalStatsResponse> | null;
  loading?: boolean;
  onRetry?: () => void;
  filtersSummary?: string;
  staleWholeTab?: boolean;
  groupBy: SetupGroupBy;
  bucketOffset: number;
  onPageChange: (offset: number) => void;
};

export function SetupBucketTable({
  source,
  loading = false,
  onRetry,
  filtersSummary,
  staleWholeTab = false,
  groupBy,
  bucketOffset,
  onPageChange,
}: SetupBucketTableProps) {
  const copy = setupGroupCopy(groupBy);

  const derived = useMemo(() => {
    if (!source?.available || !source.data) {
      return {
        rows: [] as SetupBucketRow[],
        sampleSize: 0,
        empty: true,
        truncated: null as { maxRows: number } | null,
        generatedAt: null as string | null,
        totalBuckets: 0,
        limit: SETUP_BUCKET_PAGE_SIZE,
        offset: 0,
      };
    }
    const rows = buildSetupBucketRows(source.data.buckets ?? []);
    const sampleSize = source.data.overall.trade_count;
    return {
      rows,
      sampleSize,
      empty: sampleSize === 0 || rows.length === 0,
      truncated: source.data.truncated ? { maxRows: source.data.max_rows } : null,
      generatedAt: source.data.generated_at,
      totalBuckets: source.data.total_buckets,
      limit: source.data.limit || SETUP_BUCKET_PAGE_SIZE,
      offset: source.data.offset,
    };
  }, [source]);

  const pageStart = derived.totalBuckets === 0 ? 0 : derived.offset + 1;
  const pageEnd = Math.min(derived.offset + derived.limit, derived.totalBuckets);
  const canPrev = bucketOffset > 0;
  const canNext = bucketOffset + derived.limit < derived.totalBuckets;
  const showPager = derived.totalBuckets > derived.limit;

  return (
    <ChartFrame
      title={copy.bucketTableTitle}
      sourceLabel={copy.bucketTableSourceLabel}
      generatedAt={derived.generatedAt}
      filtersSummary={filtersSummary}
      sampleSize={derived.sampleSize}
      sampleLabel="closed trades"
      loading={loading}
      error={source && !source.available ? source.error ?? "Journal statistics unavailable" : null}
      onRetry={onRetry}
      empty={!loading && source?.available ? derived.empty : false}
      emptyTitle={copy.bucketTableEmptyTitle}
      emptyDescription={copy.bucketTableEmptyDescription}
      emptyAction={
        <div className="flex flex-wrap items-center gap-3">
          {bucketOffset > 0 ? <Button type="button" variant="outline"
            onClick={() => onPageChange(0)} data-testid="setup-bucket-first">First page</Button> : null}
          <JournalStatisticsLink data-testid="setup-bucket-table-empty-journal-link" />
        </div>
      }
      truncated={derived.truncated}
      staleWholeTab={staleWholeTab}
      data-testid="setup-bucket-table"
    >
      {groupBy === "setup" ? (
        <p className="text-caption text-text-muted" data-testid="setup-group-name-note">
          Setup name grouping has no setup-definition UUID. Exact filtering requires Setup version
          grouping.
        </p>
      ) : null}

      {showPager ? (
        <div
          className="flex flex-wrap items-center gap-2 text-sm text-text-muted"
          data-testid="setup-bucket-pager"
        >
          <span>
            Showing {pageStart}–{pageEnd} of {derived.totalBuckets} buckets
            {derived.truncated ? " · truncated trade coverage" : ""}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="min-h-11"
            disabled={!canPrev}
            onClick={() => onPageChange(Math.max(0, bucketOffset - SETUP_BUCKET_PAGE_SIZE))}
            data-testid="setup-bucket-prev"
          >
            Previous
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="min-h-11"
            disabled={!canNext}
            onClick={() => onPageChange(bucketOffset + SETUP_BUCKET_PAGE_SIZE)}
            data-testid="setup-bucket-next"
          >
            Next
          </Button>
        </div>
      ) : null}

      <div className="w-full max-w-full overflow-x-auto" data-testid="setup-bucket-table-scroll">
        <table className="min-w-full text-left text-sm" data-testid="setup-bucket-data-table">
          <caption className="sr-only">{copy.bucketTableCaption}</caption>
          <thead>
            <tr className="border-b border-border-subtle text-text-muted">
              <th scope="col" className="px-2 py-2 font-medium">Label</th>
              <th scope="col" className="px-2 py-2 font-medium">Key</th>
              <th scope="col" className="px-2 py-2 font-medium">group_id</th>
              <th scope="col" className="px-2 py-2 font-medium">n</th>
              <th scope="col" className="px-2 py-2 font-medium">Win rate</th>
              <th scope="col" className="px-2 py-2 font-medium">Expectancy</th>
              <th scope="col" className="px-2 py-2 font-medium">Confidence</th>
              <th scope="col" className="px-2 py-2 font-medium">Links</th>
            </tr>
          </thead>
          <tbody>
            {derived.rows.map((row) => {
              const links = buildSetupBucketLinks(row, groupBy);
              return (
                <tr
                  key={row.key}
                  className="border-b border-border-subtle/60"
                  data-testid={`setup-bucket-row-${row.key}`}
                >
                  <td className="px-2 py-2 text-text-primary">{row.displayLabel}</td>
                  <td className="px-2 py-2 font-data text-caption text-text-muted">{row.key}</td>
                  <td className="px-2 py-2 font-data text-caption text-text-muted">
                    {row.groupId ?? "—"}
                  </td>
                  <td className="px-2 py-2 font-data">{row.tradeCount}</td>
                  <td className="px-2 py-2 font-data">{formatPercent(row.winRate)}</td>
                  <td className="px-2 py-2 font-data">
                    {row.noPnlData ? "No P&L data" : formatMonetary(row.expectancy)}
                  </td>
                  <td className="px-2 py-2">
                    <StatusBadge
                      label={
                        row.insufficient
                          ? `n=${row.tradeCount} insufficient`
                          : row.confidence
                      }
                      tone={CONFIDENCE_TONE[row.confidence]}
                    />
                  </td>
                  <td className="px-2 py-2">
                    <div className="flex flex-col gap-1">
                      <Link
                        className="text-accent underline-offset-2 hover:underline"
                        href={links.journalHref}
                        data-testid={`setup-bucket-stats-link-${row.key}`}
                      >
                        Journal statistics
                      </Link>
                      <Link
                        className="text-accent underline-offset-2 hover:underline"
                        href={links.analyticsHref}
                        data-testid={`setup-bucket-filter-link-${row.key}`}
                      >
                        Filter Setups tab
                      </Link>
                      {links.exactFilterNote ? (
                        <span
                          className="text-caption text-text-muted"
                          data-testid={`setup-bucket-exact-note-${row.key}`}
                        >
                          {links.exactFilterNote}
                        </span>
                      ) : null}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </ChartFrame>
  );
}
