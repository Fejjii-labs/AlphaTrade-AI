"use client";

import { useCallback, useMemo, useState } from "react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type {
  JournalImportBatch,
  JournalImportResult,
  JournalImportRowOutcome,
} from "@/lib/api/types";
import {
  autoDetectMapping,
  buildRows,
  IMPORT_FIELDS,
  MAX_IMPORT_ROWS,
  parseCsv,
  type ColumnMapping,
  type ImportFieldKey,
  type ParsedCsv,
} from "@/lib/journal-import";

const OUTCOME_VARIANT: Record<JournalImportRowOutcome, BadgeProps["variant"]> = {
  created: "success",
  would_create: "info",
  duplicate: "warning",
  invalid: "danger",
};

const OUTCOME_LABEL: Record<JournalImportRowOutcome, string> = {
  created: "created",
  would_create: "will create",
  duplicate: "duplicate (skipped)",
  invalid: "invalid",
};

function SummaryChips({ result }: { result: JournalImportResult }) {
  return (
    <div className="flex flex-wrap gap-2 text-sm">
      <Badge variant="muted">{result.total_rows} rows</Badge>
      <Badge variant={result.committed ? "success" : "info"}>
        {result.created_count} {result.committed ? "created" : "will create"}
      </Badge>
      <Badge variant="warning">{result.duplicate_count} duplicates</Badge>
      <Badge variant={result.invalid_count > 0 ? "danger" : "muted"}>
        {result.invalid_count} invalid
      </Badge>
    </div>
  );
}

function ResultTable({
  result,
  symbols,
}: {
  result: JournalImportResult;
  symbols: string[];
}) {
  return (
    <div className="max-h-96 overflow-auto rounded-lg border border-zinc-800">
      <table className="w-full text-left text-sm">
        <thead className="sticky top-0 bg-zinc-900 text-xs uppercase text-zinc-500">
          <tr>
            <th scope="col" className="px-3 py-2">Row</th>
            <th scope="col" className="px-3 py-2">Symbol</th>
            <th scope="col" className="px-3 py-2">Outcome</th>
            <th scope="col" className="px-3 py-2">External ref</th>
            <th scope="col" className="px-3 py-2">Details</th>
          </tr>
        </thead>
        <tbody>
          {result.results.map((row) => (
            <tr key={row.index} className="border-t border-zinc-800/60">
              <td className="px-3 py-2 text-zinc-400">{row.index + 1}</td>
              <td className="px-3 py-2">{symbols[row.index] ?? "—"}</td>
              <td className="px-3 py-2">
                <Badge variant={OUTCOME_VARIANT[row.outcome]}>
                  {OUTCOME_LABEL[row.outcome]}
                </Badge>
              </td>
              <td className="max-w-52 truncate px-3 py-2 font-mono text-xs text-zinc-400">
                {row.external_ref ?? "—"}
              </td>
              <td className="px-3 py-2 text-xs text-zinc-400">
                {row.errors.length > 0
                  ? row.errors.join("; ")
                  : row.outcome === "duplicate" && row.journal_trade_id
                    ? "Already in your journal"
                    : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BatchHistoryCard({
  batch,
  expanded,
  onToggle,
}: {
  batch: JournalImportBatch;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="space-y-1">
          <p className="text-sm text-zinc-200">
            {batch.source_label || "Untitled import"}
          </p>
          <p className="text-xs text-zinc-500">
            {new Date(batch.created_at).toLocaleString()} — {batch.created_count} created,{" "}
            {batch.duplicate_count} duplicates of {batch.total_rows} rows
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={batch.status === "committed" ? "success" : "muted"}>
            {batch.status}
          </Badge>
          <Button type="button" variant="outline" size="sm" onClick={onToggle}>
            {expanded ? "Hide report" : "View report"}
          </Button>
        </div>
      </div>
      {expanded ? (
        <div className="mt-3 max-h-64 overflow-auto rounded border border-zinc-800/60 p-2">
          <ul className="space-y-1 text-xs text-zinc-400">
            {batch.row_report.map((row) => (
              <li key={row.index} className="flex items-center gap-2">
                <span className="text-zinc-500">#{row.index + 1}</span>
                <Badge variant={OUTCOME_VARIANT[row.outcome]}>
                  {OUTCOME_LABEL[row.outcome]}
                </Badge>
                <span className="truncate font-mono">{row.external_ref ?? ""}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export default function JournalImportPage() {
  const [csvText, setCsvText] = useState("");
  const [parsed, setParsed] = useState<ParsedCsv | null>(null);
  const [mapping, setMapping] = useState<ColumnMapping>({});
  const [defaultTimeframe, setDefaultTimeframe] = useState("1h");
  const [sourceLabel, setSourceLabel] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const [dryRunResult, setDryRunResult] = useState<JournalImportResult | null>(null);
  const [commitResult, setCommitResult] = useState<JournalImportResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [expandedBatchId, setExpandedBatchId] = useState<string | null>(null);

  const historyLoader = useCallback(() => api.journal.listImports({ limit: 20 }), []);
  const history = useAsyncData(historyLoader, [historyLoader]);

  const built = useMemo(() => {
    if (parsed === null) return null;
    return buildRows(parsed, mapping, { defaultTimeframe: defaultTimeframe.trim() || "1h" });
  }, [parsed, mapping, defaultTimeframe]);

  const rowSymbols = useMemo(
    () => (built === null ? [] : built.rows.map((row) => row.symbol)),
    [built],
  );

  const mappingProblems = useMemo(() => {
    if (parsed === null) return [];
    const problems: string[] = [];
    if (mapping.symbol === undefined) problems.push("Map a column to Symbol.");
    if (mapping.direction === undefined) problems.push("Map a column to Direction.");
    if (parsed.rows.length === 0) problems.push("The CSV contains no data rows.");
    if (parsed.rows.length > MAX_IMPORT_ROWS) {
      problems.push(
        `Too many rows (${parsed.rows.length}); the limit is ${MAX_IMPORT_ROWS} per import. Split the file.`,
      );
    }
    return problems;
  }, [parsed, mapping]);

  const resetResults = () => {
    setDryRunResult(null);
    setCommitResult(null);
    setSubmitError(null);
  };

  const handleParse = (text: string) => {
    setParseError(null);
    resetResults();
    if (text.trim() === "") {
      setParsed(null);
      setMapping({});
      return;
    }
    const result = parseCsv(text);
    if (result.headers.length === 0) {
      setParsed(null);
      setMapping({});
      setParseError("Could not read a header row from the CSV.");
      return;
    }
    setParsed(result);
    setMapping(autoDetectMapping(result.headers));
  };

  const handleFile = async (file: File) => {
    const text = await file.text();
    setCsvText(text);
    if (!sourceLabel) {
      setSourceLabel(file.name);
    }
    handleParse(text);
  };

  const submit = async (mode: "dry_run" | "commit") => {
    if (built === null || mappingProblems.length > 0) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await api.journal.importTrades({
        mode,
        source_label: sourceLabel.trim() || null,
        rows: built.rows,
      });
      if (mode === "dry_run") {
        setDryRunResult(result);
        setCommitResult(null);
      } else {
        setCommitResult(result);
        if (result.committed) {
          void history.reload();
        }
      }
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Import request failed");
    } finally {
      setSubmitting(false);
    }
  };

  const commitEnabled =
    dryRunResult !== null &&
    dryRunResult.invalid_count === 0 &&
    dryRunResult.created_count > 0 &&
    !submitting;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Journal import</h1>
        <p className="text-sm text-zinc-400">
          Bulk-import historical trades into the canonical journal (source “imported”).
          Rows are deduplicated by external ref, previews are dry-run first, and commits
          are all-or-nothing — re-running a failed import is always safe.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>1. Provide CSV</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="import-file">CSV file</Label>
            <input
              id="import-file"
              type="file"
              accept=".csv,text/csv,text/plain"
              className="block w-full text-sm text-zinc-400 file:mr-3 file:rounded-md file:border-0 file:bg-zinc-800 file:px-3 file:py-2 file:text-sm file:text-zinc-200"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void handleFile(file);
              }}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="import-csv">Or paste CSV text</Label>
            <textarea
              id="import-csv"
              rows={6}
              className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 font-mono text-xs"
              placeholder={"symbol,side,entry_price,exit_price,size,pnl,trade_id\nBTCUSDT,buy,64500,65500,0.5,496.8,ex-1001"}
              value={csvText}
              onChange={(e) => {
                setCsvText(e.target.value);
                handleParse(e.target.value);
              }}
            />
          </div>
          {parseError ? <p className="text-sm text-red-300">{parseError}</p> : null}
          {parsed ? (
            <p className="text-sm text-zinc-400">
              Parsed {parsed.rows.length} data row(s) with {parsed.headers.length} column(s).
            </p>
          ) : null}
        </CardContent>
      </Card>

      {parsed ? (
        <Card>
          <CardHeader>
            <CardTitle>2. Map columns</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-4">
              {IMPORT_FIELDS.map((field) => (
                <div key={field.key} className="space-y-1">
                  <Label htmlFor={`map-${field.key}`}>
                    {field.label}
                    {field.required ? " *" : ""}
                  </Label>
                  <select
                    id={`map-${field.key}`}
                    className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
                    value={mapping[field.key] ?? ""}
                    onChange={(e) => {
                      resetResults();
                      setMapping((prev) => {
                        const next = { ...prev };
                        if (e.target.value === "") {
                          delete next[field.key as ImportFieldKey];
                        } else {
                          next[field.key as ImportFieldKey] = Number(e.target.value);
                        }
                        return next;
                      });
                    }}
                  >
                    <option value="">— not mapped —</option>
                    {parsed.headers.map((header, index) => (
                      <option key={`${header}-${index}`} value={index}>
                        {header || `column ${index + 1}`}
                      </option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="import-default-tf">Default timeframe (when not mapped)</Label>
                <Input
                  id="import-default-tf"
                  value={defaultTimeframe}
                  onChange={(e) => {
                    setDefaultTimeframe(e.target.value);
                    resetResults();
                  }}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="import-source-label">Source label</Label>
                <Input
                  id="import-source-label"
                  placeholder="e.g. exchange-export-2026-06.csv"
                  value={sourceLabel}
                  onChange={(e) => setSourceLabel(e.target.value)}
                />
              </div>
            </div>
            {mappingProblems.length > 0 ? (
              <ul className="space-y-1 text-sm text-amber-300">
                {mappingProblems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            ) : null}
            {built !== null && built.issues.length > 0 ? (
              <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-sm text-amber-200">
                <p className="font-medium">
                  {built.issues.length} row(s) have client-side issues and will be reported as
                  invalid by the preview:
                </p>
                <ul className="mt-1 max-h-32 space-y-1 overflow-auto text-xs">
                  {built.issues.slice(0, 20).map((issue) => (
                    <li key={issue.index}>
                      Row {issue.index + 1}: {issue.messages.join("; ")}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <Button
              type="button"
              disabled={mappingProblems.length > 0 || submitting}
              onClick={() => void submit("dry_run")}
            >
              {submitting && dryRunResult === null ? "Previewing…" : "Preview (dry-run)"}
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {dryRunResult ? (
        <Card>
          <CardHeader>
            <CardTitle>3. Reconcile &amp; commit</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <SummaryChips result={commitResult ?? dryRunResult} />
            <ResultTable result={commitResult ?? dryRunResult} symbols={rowSymbols} />
            {dryRunResult.invalid_count > 0 ? (
              <p className="text-sm text-amber-300">
                Fix the invalid rows above and re-parse before committing. Commits are
                all-or-nothing: nothing is written while any row is invalid.
              </p>
            ) : null}
            {commitResult?.committed ? (
              <p className="text-sm text-emerald-300">
                Import committed — created{" "}
                {commitResult.created_count} journal trade(s).
              </p>
            ) : (
              <Button
                type="button"
                disabled={!commitEnabled}
                onClick={() => void submit("commit")}
              >
                {submitting ? "Committing…" : `Commit ${dryRunResult.created_count} row(s)`}
              </Button>
            )}
            {submitError ? (
              <ErrorState
                message={`${submitError} — nothing was written. Re-running the import is safe: already-imported rows are skipped as duplicates.`}
              />
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Import history</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {history.loading ? <LoadingState label="Loading import history…" /> : null}
          {history.error ? (
            <ErrorState message={history.error} onRetry={() => void history.reload()} />
          ) : null}
          {history.data && history.data.items.length === 0 ? (
            <EmptyState
              title="No imports yet"
              description="Committed import batches appear here with their reconciliation reports."
            />
          ) : null}
          {history.data?.items.map((batch) => (
            <BatchHistoryCard
              key={batch.id}
              batch={batch}
              expanded={expandedBatchId === batch.id}
              onToggle={() =>
                setExpandedBatchId((prev) => (prev === batch.id ? null : batch.id))
              }
            />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
