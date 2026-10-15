"use client";

import Link from "next/link";
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";

import {
  hasPrefillContext,
  relatedPlanHref,
  relatedValidationHref,
  type JournalQueryContext,
} from "@/components/journal/journalContext";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { JournalEntry, StrategyId, Timeframe, TradeDirection } from "@/lib/api/types";
import {
  EMOTION_TAG_SUGGESTIONS,
  MISTAKE_TAG_SUGGESTIONS,
  SETUP_TYPE_OPTIONS,
} from "@/lib/setup-types";

const RESULT_OPTIONS = ["open", "win", "loss", "breakeven"] as const;
const DIRECTION_OPTIONS: TradeDirection[] = ["long", "short"];
const DEFAULT_SYMBOL = "BTCUSDT";
const DEFAULT_TIMEFRAME: Timeframe = "1h";
const DEFAULT_DIRECTION: TradeDirection = "long";

function prefillContextKey(context: JournalQueryContext): string {
  return `${context.proposalId ?? ""}|${context.positionId ?? ""}`;
}

function resetPrefillDerivedFields(setters: {
  setSymbol: (value: string) => void;
  setTimeframe: (value: Timeframe) => void;
  setDirection: (value: TradeDirection) => void;
  setStrategyId: (value: StrategyId | "") => void;
  setRationale: (value: string) => void;
}) {
  setters.setSymbol(DEFAULT_SYMBOL);
  setters.setTimeframe(DEFAULT_TIMEFRAME);
  setters.setDirection(DEFAULT_DIRECTION);
  setters.setStrategyId("");
  setters.setRationale("");
}

const TIMEFRAME_OPTIONS: Timeframe[] = [
  "1m",
  "3m",
  "5m",
  "15m",
  "30m",
  "1h",
  "2h",
  "4h",
  "6h",
  "12h",
  "1d",
  "3d",
  "1w",
];

export type JournalPrefillState =
  | { status: "idle" }
  | { status: "loading" }
  | {
      status: "ready";
      symbol: string;
      timeframe: string;
      direction: string;
      strategyId?: string | null;
      entryRationale: string;
      linkedProposalId?: string | null;
      linkedPositionId?: string | null;
      tags: string[];
    }
  | { status: "invalid"; message: string };

type JournalQuickEntryProps = {
  context: JournalQueryContext;
  prefill: JournalPrefillState;
  relatedSession:
    | { status: "idle" }
    | { status: "loading" }
    | { status: "ready"; sessionId: string }
    | { status: "invalid"; message: string };
  unsupportedTradeMessage?: string | null;
  onSaved: (entry: JournalEntry) => void;
};

type FieldErrors = Partial<Record<"symbol" | "rationale" | "stressScore" | "result", string>>;

function splitTags(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function JournalQuickEntry({
  context,
  prefill,
  relatedSession,
  unsupportedTradeMessage,
  onSaved,
}: JournalQuickEntryProps) {
  const formId = useId();
  const summaryRef = useRef<HTMLDivElement | null>(null);
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);
  const [timeframe, setTimeframe] = useState<Timeframe>(DEFAULT_TIMEFRAME);
  const [direction, setDirection] = useState<TradeDirection>(DEFAULT_DIRECTION);
  const [strategyId, setStrategyId] = useState<StrategyId | "">("");
  const [rationale, setRationale] = useState("");
  const [exitRationale, setExitRationale] = useState("");
  const [lessons, setLessons] = useState("");
  const [improvementRule, setImprovementRule] = useState("");
  const [emotions, setEmotions] = useState("");
  const [mistakes, setMistakes] = useState("");
  const [result, setResult] = useState<(typeof RESULT_OPTIONS)[number]>("open");
  const [pnl, setPnl] = useState("");
  const [stressScore, setStressScore] = useState("");
  const [linkedProposalId, setLinkedProposalId] = useState<string | undefined>();
  const [linkedPositionId, setLinkedPositionId] = useState<string | undefined>();
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedEntry, setSavedEntry] = useState<JournalEntry | null>(null);

  const contextKey = prefillContextKey(context);
  const activePrefillContext = hasPrefillContext(context);
  const relationshipsReady = prefill.status === "ready";
  const showProposalLink = relationshipsReady && Boolean(linkedProposalId);
  const showPositionLink = relationshipsReady && Boolean(linkedPositionId);
  const showValidationLink = relatedSession.status === "ready";
  const showRelatedLinks = showProposalLink || showPositionLink || showValidationLink;

  // Finish prefill resets before the new context can accept manual input.
  // A passive reset can erase input entered as the invalid warning appears.
  useLayoutEffect(() => {
    setLinkedProposalId(undefined);
    setLinkedPositionId(undefined);
    if (activePrefillContext) {
      resetPrefillDerivedFields({
        setSymbol,
        setTimeframe,
        setDirection,
        setStrategyId,
        setRationale,
      });
    }
  }, [contextKey, activePrefillContext]);

  useLayoutEffect(() => {
    if (prefill.status === "loading" || prefill.status === "invalid") {
      setLinkedProposalId(undefined);
      setLinkedPositionId(undefined);
      if (activePrefillContext) {
        resetPrefillDerivedFields({
          setSymbol,
          setTimeframe,
          setDirection,
          setStrategyId,
          setRationale,
        });
      }
      return;
    }

    if (prefill.status === "idle") {
      setLinkedProposalId(undefined);
      setLinkedPositionId(undefined);
      return;
    }

    if (prefill.status !== "ready") return;

    setSymbol(prefill.symbol);
    if (TIMEFRAME_OPTIONS.includes(prefill.timeframe as Timeframe)) {
      setTimeframe(prefill.timeframe as Timeframe);
    }
    if (DIRECTION_OPTIONS.includes(prefill.direction as TradeDirection)) {
      setDirection(prefill.direction as TradeDirection);
    }
    setStrategyId((prefill.strategyId as StrategyId) ?? "");
    setRationale(prefill.entryRationale);
    setLinkedProposalId(prefill.linkedProposalId ?? undefined);
    setLinkedPositionId(prefill.linkedPositionId ?? undefined);
    setSaveError(null);
    setSavedEntry(null);
  }, [prefill, activePrefillContext]);

  const errorSummary = useMemo(() => Object.values(fieldErrors).filter(Boolean), [fieldErrors]);

  useEffect(() => {
    if (errorSummary.length > 0) {
      summaryRef.current?.focus();
    }
  }, [errorSummary.length]);

  function validate(): FieldErrors {
    const next: FieldErrors = {};
    if (!symbol.trim()) next.symbol = "Symbol is required.";
    if (!rationale.trim()) next.rationale = "What happened versus plan is required.";
    if (stressScore.trim()) {
      const score = Number(stressScore);
      if (!Number.isInteger(score) || score < 0 || score > 10) {
        next.stressScore = "Stress/rating score must be an integer from 0 to 10.";
      }
    }
    if (!RESULT_OPTIONS.includes(result)) {
      next.result = "Choose a supported trade result.";
    }
    return next;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaveError(null);
    const nextErrors = validate();
    setFieldErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    setBusy(true);
    try {
      const entry = await api.journal.create({
        symbol: symbol.trim().toUpperCase(),
        timeframe,
        direction,
        entry_rationale: rationale.trim(),
        exit_rationale: exitRationale.trim() || undefined,
        lessons: lessons.trim() || undefined,
        improvement_rule: improvementRule.trim() || undefined,
        emotions: splitTags(emotions),
        mistakes: splitTags(mistakes),
        strategy_id: strategyId || undefined,
        linked_proposal_id: relationshipsReady ? linkedProposalId : undefined,
        linked_position_id: relationshipsReady ? linkedPositionId : undefined,
        result,
        pnl: pnl.trim() || undefined,
        stress_score: stressScore.trim() ? Number(stressScore) : undefined,
      });
      setSavedEntry(entry);
      setRationale("");
      setExitRationale("");
      setLessons("");
      setImprovementRule("");
      setEmotions("");
      setMistakes("");
      setPnl("");
      setStressScore("");
      setResult("open");
      onSaved(entry);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save journal entry");
      setSavedEntry(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="journal-quick-entry-heading"
      data-testid="journal-quick-entry"
      className="space-y-3"
    >
      <div>
        <h2 id="journal-quick-entry-heading" className="text-lg font-semibold text-text-primary">
          New journal entry
        </h2>
        <p className="mt-1 text-sm text-text-muted">
          Fast capture using existing journal fields only. Prefill comes from proposal or position
          context when valid.
        </p>
      </div>

      {unsupportedTradeMessage ? (
        <div
          role="alert"
          data-testid="journal-unsupported-trade"
          className="rounded-control border border-warning-border bg-warning-muted/40 px-3 py-2 text-sm text-warning"
        >
          {unsupportedTradeMessage}
        </div>
      ) : null}

      {prefill.status === "loading" ? (
        <p className="text-sm text-text-muted" data-testid="journal-prefill-loading">
          Loading prefill context…
        </p>
      ) : null}

      {prefill.status === "invalid" ? (
        <div
          role="alert"
          data-testid="journal-prefill-invalid"
          className="rounded-control border border-danger-border bg-danger-muted/40 px-3 py-2 text-sm text-danger"
        >
          {prefill.message}
        </div>
      ) : null}

      {relatedSession.status === "invalid" ? (
        <div
          role="alert"
          data-testid="journal-session-invalid"
          className="rounded-control border border-danger-border bg-danger-muted/40 px-3 py-2 text-sm text-danger"
        >
          {relatedSession.message}
        </div>
      ) : null}

      {showRelatedLinks ? (
        <div
          className="flex flex-wrap gap-2 text-sm"
          data-testid="journal-related-context"
        >
          {showProposalLink && linkedProposalId ? (
            <Link
              href={relatedPlanHref(linkedProposalId)}
              className="underline text-text-secondary"
              data-testid="quick-entry-related-plan"
            >
              Related plan
            </Link>
          ) : null}
          {showPositionLink && linkedPositionId ? (
            <span className="text-text-muted" data-testid="quick-entry-related-position">
              Related position {linkedPositionId.slice(0, 8)}… (stored link on save)
            </span>
          ) : null}
          {showValidationLink ? (
            <Link
              href={relatedValidationHref(relatedSession.sessionId)}
              className="underline text-text-secondary"
              data-testid="quick-entry-related-validation"
            >
              Related validation (link context only — not stored on journal entry)
            </Link>
          ) : null}
        </div>
      ) : null}

      {errorSummary.length > 0 ? (
        <div
          ref={summaryRef}
          tabIndex={-1}
          role="alert"
          data-testid="journal-entry-error-summary"
          className="rounded-control border border-danger-border bg-danger-muted/40 px-3 py-2 text-sm text-danger"
        >
          <p className="font-medium">Fix the following before saving:</p>
          <ul className="mt-1 list-disc pl-5">
            {fieldErrors.symbol ? (
              <li>
                <a href={`#${formId}-symbol`} className="underline">
                  {fieldErrors.symbol}
                </a>
              </li>
            ) : null}
            {fieldErrors.rationale ? (
              <li>
                <a href={`#${formId}-rationale`} className="underline">
                  {fieldErrors.rationale}
                </a>
              </li>
            ) : null}
            {fieldErrors.stressScore ? (
              <li>
                <a href={`#${formId}-stress`} className="underline">
                  {fieldErrors.stressScore}
                </a>
              </li>
            ) : null}
            {fieldErrors.result ? (
              <li>
                <a href={`#${formId}-result`} className="underline">
                  {fieldErrors.result}
                </a>
              </li>
            ) : null}
          </ul>
        </div>
      ) : null}

      <form
        onSubmit={(event) => void handleSubmit(event)}
        className="space-y-3 rounded-control border border-border-subtle bg-surface-1 p-4"
        noValidate
        data-testid="journal-quick-entry-form"
      >
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor={`${formId}-symbol`}>Symbol</Label>
            <Input
              id={`${formId}-symbol`}
              value={symbol}
              onChange={(event) => setSymbol(event.target.value.toUpperCase())}
              aria-invalid={Boolean(fieldErrors.symbol)}
              aria-describedby={fieldErrors.symbol ? `${formId}-symbol-error` : undefined}
            />
            {fieldErrors.symbol ? (
              <p id={`${formId}-symbol-error`} className="text-caption text-danger">
                {fieldErrors.symbol}
              </p>
            ) : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${formId}-setup`}>Setup type</Label>
            <select
              id={`${formId}-setup`}
              className="h-10 w-full rounded-control border border-border bg-surface-0 px-3 text-sm text-text-primary"
              value={strategyId}
              onChange={(event) => setStrategyId(event.target.value as StrategyId | "")}
            >
              <option value="">Select setup…</option>
              {SETUP_TYPE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${formId}-direction`}>Direction</Label>
            <select
              id={`${formId}-direction`}
              className="h-10 w-full rounded-control border border-border bg-surface-0 px-3 text-sm text-text-primary"
              value={direction}
              onChange={(event) => setDirection(event.target.value as TradeDirection)}
            >
              {DIRECTION_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${formId}-timeframe`}>Timeframe</Label>
            <select
              id={`${formId}-timeframe`}
              className="h-10 w-full rounded-control border border-border bg-surface-0 px-3 text-sm text-text-primary"
              value={timeframe}
              onChange={(event) => setTimeframe(event.target.value as Timeframe)}
            >
              {TIMEFRAME_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${formId}-result`}>Result</Label>
            <select
              id={`${formId}-result`}
              className="h-10 w-full rounded-control border border-border bg-surface-0 px-3 text-sm text-text-primary"
              value={result}
              onChange={(event) =>
                setResult(event.target.value as (typeof RESULT_OPTIONS)[number])
              }
              aria-invalid={Boolean(fieldErrors.result)}
            >
              {RESULT_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${formId}-pnl`}>P&amp;L (optional)</Label>
            <Input
              id={`${formId}-pnl`}
              value={pnl}
              onChange={(event) => setPnl(event.target.value)}
              inputMode="decimal"
              placeholder="Use realized value when known"
            />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label htmlFor={`${formId}-stress`}>Stress / rating score (0–10, optional)</Label>
            <Input
              id={`${formId}-stress`}
              value={stressScore}
              onChange={(event) => setStressScore(event.target.value)}
              inputMode="numeric"
              aria-invalid={Boolean(fieldErrors.stressScore)}
              aria-describedby={fieldErrors.stressScore ? `${formId}-stress-error` : undefined}
            />
            {fieldErrors.stressScore ? (
              <p id={`${formId}-stress-error`} className="text-caption text-danger">
                {fieldErrors.stressScore}
              </p>
            ) : null}
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor={`${formId}-rationale`}>What happened versus plan</Label>
          <Textarea
            id={`${formId}-rationale`}
            value={rationale}
            onChange={(event) => setRationale(event.target.value)}
            aria-invalid={Boolean(fieldErrors.rationale)}
            aria-describedby={fieldErrors.rationale ? `${formId}-rationale-error` : undefined}
          />
          {fieldErrors.rationale ? (
            <p id={`${formId}-rationale-error`} className="text-caption text-danger">
              {fieldErrors.rationale}
            </p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label htmlFor={`${formId}-exit`}>Exit notes (optional)</Label>
          <Textarea
            id={`${formId}-exit`}
            value={exitRationale}
            onChange={(event) => setExitRationale(event.target.value)}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor={`${formId}-lessons`}>Notes / lessons learned</Label>
          <Textarea
            id={`${formId}-lessons`}
            value={lessons}
            onChange={(event) => setLessons(event.target.value)}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor={`${formId}-improvement`}>Discipline / improvement rule</Label>
          <Textarea
            id={`${formId}-improvement`}
            value={improvementRule}
            onChange={(event) => setImprovementRule(event.target.value)}
            placeholder="e.g. No entries until 15m close confirms bias"
          />
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor={`${formId}-emotions`}>Emotion tags (comma-separated)</Label>
            <Input
              id={`${formId}-emotions`}
              value={emotions}
              onChange={(event) => setEmotions(event.target.value)}
            />
            <p className="text-caption text-text-muted">{EMOTION_TAG_SUGGESTIONS.join(", ")}</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${formId}-mistakes`}>Mistake / rule-compliance tags</Label>
            <Input
              id={`${formId}-mistakes`}
              value={mistakes}
              onChange={(event) => setMistakes(event.target.value)}
            />
            <p className="text-caption text-text-muted">{MISTAKE_TAG_SUGGESTIONS.join(", ")}</p>
          </div>
        </div>

        <div className="sticky bottom-20 z-10 flex flex-wrap gap-2 bg-surface-1/95 py-2 md:static md:bottom-auto md:bg-transparent md:py-0">
          <Button type="submit" disabled={busy || prefill.status === "loading"}>
            {busy ? "Saving…" : "Save journal entry"}
          </Button>
        </div>

        {saveError ? (
          <p className="text-sm text-danger" role="alert" data-testid="journal-save-error">
            {saveError}
          </p>
        ) : null}
      </form>

      {savedEntry ? (
        <div
          role="status"
          data-testid="journal-save-success"
          className="space-y-2 rounded-control border border-success-border bg-success-muted/30 px-3 py-3 text-sm"
        >
          <p className="font-medium text-text-primary">
            Saved {savedEntry.symbol} · {savedEntry.direction.toUpperCase()} · {savedEntry.result}
          </p>
          <p className="text-text-secondary">
            Next safe actions: review the saved entry, run discipline analysis when ready, or open
            Lessons to continue the learning loop. AI lesson extraction is not implemented here.
          </p>
          <div className="flex flex-wrap gap-2">
            <a
              href={`#journal-entry-${savedEntry.id}`}
              className="inline-flex h-10 items-center rounded-control border border-border px-3 text-sm underline"
            >
              View saved entry
            </a>
            <Link
              href="/lessons"
              className="inline-flex h-10 items-center rounded-control border border-border px-3 text-sm underline"
            >
              Review lessons
            </Link>
            {!savedEntry.linked_proposal_id ? null : (
              <Link
                href={relatedPlanHref(savedEntry.linked_proposal_id)}
                className="inline-flex h-10 items-center rounded-control border border-border px-3 text-sm underline"
              >
                Related plan
              </Link>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
