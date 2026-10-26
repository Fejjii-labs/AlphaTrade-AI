"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import { reviewStatusLabel, setupConditionLabel } from "@/lib/alert-display";
import type { PaperValidationDraftItem, SetupAlertReviewItem, SetupAlertReviewStatus } from "@/lib/api/types";
import { formatDateTime, formatPercent, formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

const CREATE_PAPER_VALIDATION_DRAFT = "CREATE_PAPER_VALIDATION_DRAFT";

const REVIEW_STATUSES: SetupAlertReviewStatus[] = [
  "unreviewed",
  "watching",
  "ignored",
  "important",
];

type AlertCardProps = {
  alert: SetupAlertReviewItem;
  busy: boolean;
  highlighted?: boolean;
  cardRef?: (node: HTMLElement | null) => void;
  onSave: (alertId: string, status: SetupAlertReviewStatus, notes: string) => Promise<void>;
  onQuickAction: (alertId: string, status: SetupAlertReviewStatus) => Promise<void>;
  onCreateDraft: (
    alertId: string,
    confirm: string,
    notes: string,
    riskMode: string,
  ) => Promise<PaperValidationDraftItem | null>;
};

function canCreateDraft(status: SetupAlertReviewStatus): boolean {
  return status === "watching" || status === "important";
}

function SetupAlertReviewCard({
  alert,
  busy,
  highlighted = false,
  cardRef,
  onSave,
  onQuickAction,
  onCreateDraft,
}: AlertCardProps) {
  const [status, setStatus] = useState<SetupAlertReviewStatus>(alert.review_status);
  const [notes, setNotes] = useState(alert.review_notes ?? "");
  const [showDraftForm, setShowDraftForm] = useState(false);
  const [draftConfirm, setDraftConfirm] = useState("");
  const [draftNotes, setDraftNotes] = useState("");
  const [draftRiskMode, setDraftRiskMode] = useState("conservative");
  const [createdDraftId, setCreatedDraftId] = useState<string | null>(null);
  const draftEligible = canCreateDraft(alert.review_status);

  return (
    <article
      ref={cardRef}
      tabIndex={highlighted ? -1 : undefined}
      aria-current={highlighted ? "true" : undefined}
      className={cn(
        "rounded-lg border p-4 space-y-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
        highlighted
          ? "border-info-border bg-info-muted/30"
          : "border-zinc-800",
      )}
      data-testid={`setup-alert-${alert.alert_id}`}
      data-highlighted={highlighted ? "true" : "false"}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="info" data-testid="setup-alert-condition">
              {setupConditionLabel(alert.condition ?? "unknown")}
            </Badge>
            <span className="text-sm font-medium text-zinc-100">
              {alert.symbol ?? "—"} · {alert.timeframe ?? "—"}
            </span>
            <Badge variant="muted">{alert.direction ?? "—"}</Badge>
          </div>
          <p className="text-xs text-zinc-500">
            Created {formatDateTime(alert.created_at)}
          </p>
        </div>
        <Badge
          variant={alert.review_status === "important" ? "warning" : "muted"}
          data-testid="setup-alert-confidence"
        >
          {formatPercent(alert.confidence, 0)}
        </Badge>
      </div>

      <p className="text-sm text-zinc-300" data-testid="setup-alert-reason">
        {alert.reason ?? "No reason provided."}
      </p>

      <div className="grid gap-2 text-xs text-zinc-400 sm:grid-cols-3">
        <p data-testid="setup-alert-trigger">
          Trigger: <span className="text-zinc-200">{formatPrice(alert.trigger_level)}</span>
        </p>
        <p data-testid="setup-alert-invalidation">
          Invalidation:{" "}
          <span className="text-zinc-200">{formatPrice(alert.invalidation_level)}</span>
        </p>
        <p data-testid="setup-alert-latest-price">
          Latest: <span className="text-zinc-200">{formatPrice(alert.latest_price)}</span>
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          disabled={busy}
          data-testid="quick-action-watching"
          onClick={() => void onQuickAction(alert.alert_id, "watching")}
        >
          Mark watching
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          disabled={busy}
          data-testid="quick-action-important"
          onClick={() => void onQuickAction(alert.alert_id, "important")}
        >
          Mark important
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={busy}
          data-testid="quick-action-ignore"
          onClick={() => void onQuickAction(alert.alert_id, "ignored")}
        >
          Ignore
        </Button>
      </div>

      <div className="grid gap-2 sm:grid-cols-[160px_1fr_auto] sm:items-end">
        <label className="space-y-1 text-xs text-zinc-400">
          Review status
          <select
            className="block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100"
            value={status}
            data-testid="setup-alert-review-status"
            onChange={(event) => setStatus(event.target.value as SetupAlertReviewStatus)}
          >
            {REVIEW_STATUSES.map((item) => (
              <option key={item} value={item}>
                {reviewStatusLabel(item)}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs text-zinc-400">
          Notes
          <textarea
            className="block min-h-[72px] w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100"
            value={notes}
            data-testid="setup-alert-notes"
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Optional review notes"
          />
        </label>
        <Button
          type="button"
          size="sm"
          disabled={busy}
          data-testid="setup-alert-save"
          onClick={() => void onSave(alert.alert_id, status, notes)}
        >
          Save
        </Button>
      </div>

      {draftEligible ? (
        <div className="space-y-2 rounded border border-zinc-800/80 bg-zinc-950/40 p-3">
          {!showDraftForm && !createdDraftId ? (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={busy}
              data-testid="setup-alert-create-draft"
              onClick={() => setShowDraftForm(true)}
            >
              Create paper draft
            </Button>
          ) : null}

          {showDraftForm && !createdDraftId ? (
            <div className="space-y-2" data-testid="setup-alert-draft-form">
              <p className="text-xs text-amber-200" data-testid="setup-alert-draft-warning">
                Draft only. No order. No Telegram. No execution.
              </p>
              <label className="block space-y-1 text-xs text-zinc-400">
                Type{" "}
                <span className="font-mono text-zinc-200">{CREATE_PAPER_VALIDATION_DRAFT}</span> to
                confirm
                <input
                  className="block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100"
                  value={draftConfirm}
                  data-testid="setup-alert-draft-confirm"
                  onChange={(event) => setDraftConfirm(event.target.value)}
                  placeholder={CREATE_PAPER_VALIDATION_DRAFT}
                />
              </label>
              <label className="block space-y-1 text-xs text-zinc-400">
                Draft notes (optional)
                <textarea
                  className="block min-h-[56px] w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100"
                  value={draftNotes}
                  data-testid="setup-alert-draft-notes"
                  onChange={(event) => setDraftNotes(event.target.value)}
                />
              </label>
              <label className="block space-y-1 text-xs text-zinc-400">
                Risk mode
                <select
                  className="block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100"
                  value={draftRiskMode}
                  data-testid="setup-alert-draft-risk-mode"
                  onChange={(event) => setDraftRiskMode(event.target.value)}
                >
                  <option value="conservative">Conservative</option>
                  <option value="moderate">Moderate</option>
                  <option value="aggressive">Aggressive</option>
                </select>
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  disabled={busy || draftConfirm.trim() !== CREATE_PAPER_VALIDATION_DRAFT}
                  data-testid="setup-alert-draft-submit"
                  onClick={() =>
                    void onCreateDraft(
                      alert.alert_id,
                      draftConfirm.trim(),
                      draftNotes,
                      draftRiskMode,
                    ).then((draft) => {
                      if (draft) {
                        setCreatedDraftId(draft.draft_id);
                        setShowDraftForm(false);
                      }
                    })
                  }
                >
                  Create draft
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => setShowDraftForm(false)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : null}

          {createdDraftId ? (
            <p className="text-xs text-zinc-300" data-testid="setup-alert-draft-link">
              Paper draft created.{" "}
              <Link href={`/paper-validation/drafts/${createdDraftId}`} className="underline">
                View draft
              </Link>
            </p>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

export default function SetupAlertReviewPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedAlertId = (searchParams.get("alert") ?? "").trim();
  const [filterSymbol, setFilterSymbol] = useState("");
  const [filterCondition, setFilterCondition] = useState("");
  const [filterTimeframe, setFilterTimeframe] = useState("");
  const [filterDirection, setFilterDirection] = useState("");
  const [filterReviewStatus, setFilterReviewStatus] = useState("");
  const [filterMinConfidence, setFilterMinConfidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [deepLinkNotice, setDeepLinkNotice] = useState<string | null>(null);
  const highlightedRef = useRef<HTMLElement | null>(null);

  const listLoader = useCallback(
    () =>
      api.alerts.setupReview({
        symbol: filterSymbol || undefined,
        condition: filterCondition || undefined,
        timeframe: filterTimeframe || undefined,
        direction: filterDirection || undefined,
        status: filterReviewStatus || undefined,
        min_confidence: filterMinConfidence ? Number(filterMinConfidence) : undefined,
        limit: 50,
      }),
    [
      filterSymbol,
      filterCondition,
      filterTimeframe,
      filterDirection,
      filterReviewStatus,
      filterMinConfidence,
    ],
  );
  const summaryLoader = useCallback(() => api.alerts.setupReviewSummary(), []);

  const { data, loading, error, reload } = useAsyncData(listLoader, [
    filterSymbol,
    filterCondition,
    filterTimeframe,
    filterDirection,
    filterReviewStatus,
    filterMinConfidence,
  ]);
  const { data: summary, reload: reloadSummary } = useAsyncData(summaryLoader, []);

  const conditionOptions = useMemo(() => {
    const keys = Object.keys(summary?.by_condition ?? {});
    return keys.sort();
  }, [summary?.by_condition]);

  const matchedAlert = useMemo(() => {
    if (!requestedAlertId || !data?.items) return null;
    return data.items.find((item) => item.alert_id === requestedAlertId) ?? null;
  }, [data?.items, requestedAlertId]);

  const clearAlertQuery = useCallback(
    (message?: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.delete("alert");
      const query = params.toString();
      router.replace(query ? `/alerts/review?${query}` : "/alerts/review");
      if (message) setDeepLinkNotice(message);
    },
    [router, searchParams],
  );

  useEffect(() => {
    if (!requestedAlertId || loading) return;
    if (!data) return;
    if (matchedAlert) {
      setDeepLinkNotice(null);
      const node = highlightedRef.current;
      if (node) {
        if (typeof node.scrollIntoView === "function") {
          node.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
        if (typeof node.focus === "function") {
          node.focus({ preventScroll: true });
        }
      }
      return;
    }
    setDeepLinkNotice("Requested source alert was not found or is no longer available.");
  }, [data, loading, matchedAlert, requestedAlertId]);

  async function persistReview(
    alertId: string,
    reviewStatus: SetupAlertReviewStatus,
    reviewNotes?: string,
  ) {
    setBusy(true);
    setActionMessage(null);
    try {
      await api.alerts.updateSetupReview(alertId, {
        review_status: reviewStatus,
        review_notes: reviewNotes?.trim() ? reviewNotes.trim() : null,
      });
      setActionMessage("Review saved. No Telegram messages or orders were sent.");
      await Promise.all([reload(), reloadSummary()]);
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : "Review update failed.");
    } finally {
      setBusy(false);
    }
  }

  async function createPaperDraft(
    alertId: string,
    confirm: string,
    notes: string,
    riskMode: string,
  ) {
    setBusy(true);
    setActionMessage(null);
    try {
      const result = await api.alerts.createSetupDraft(alertId, {
        confirm,
        notes: notes.trim() ? notes.trim() : null,
        risk_mode: riskMode,
      });
      setActionMessage(
        result.already_exists
          ? "Existing paper draft returned. No order, Telegram, or execution occurred."
          : "Paper draft created. No order, Telegram, or execution occurred.",
      );
      return result.draft;
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : "Draft creation failed.");
      return null;
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) return <LoadingState label="Loading setup alerts…" />;
  if (error) return <ErrorState message={error} onRetry={() => void reload()} />;

  return (
    <div className="space-y-6" data-testid="setup-alert-review-page">
      <div>
        <h1 className="text-2xl font-semibold">Setup Alert Review</h1>
        <p className="text-sm text-zinc-400">
          Review scanner-created setup alerts from the market watcher. This page never sends
          Telegram messages or places orders.
        </p>
      </div>

      <Card data-testid="setup-alert-review-summary">
        <CardHeader>
          <CardTitle className="text-base">Review summary</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm text-zinc-300 sm:grid-cols-4">
          <p>
            Unreviewed:{" "}
            <span className="font-semibold text-zinc-100">{summary?.total_unreviewed ?? 0}</span>
          </p>
          <p>
            Watching:{" "}
            <span className="font-semibold text-zinc-100">{summary?.total_watching ?? 0}</span>
          </p>
          <p>
            Important:{" "}
            <span className="font-semibold text-zinc-100">{summary?.total_important ?? 0}</span>
          </p>
          <p>
            Ignored:{" "}
            <span className="font-semibold text-zinc-100">{summary?.total_ignored ?? 0}</span>
          </p>
        </CardContent>
      </Card>

      {deepLinkNotice || (requestedAlertId && !matchedAlert && data) ? (
        <div
          role="status"
          data-testid="setup-alert-deep-link-missing"
          className="rounded-control border border-warning-border bg-warning-muted/40 px-3 py-2 text-sm text-warning"
        >
          <p>
            {deepLinkNotice ??
              "Requested source alert was not found or is no longer available."}
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="mt-2"
            data-testid="setup-alert-clear-deep-link"
            onClick={() => clearAlertQuery("Cleared stale source-alert query.")}
          >
            Clear alert query
          </Button>
        </div>
      ) : null}

      {requestedAlertId && matchedAlert ? (
        <p
          className="text-sm text-text-secondary"
          data-testid="setup-alert-deep-link-active"
          role="status"
        >
          Highlighting the source alert from Validate. No
          review status was changed.
        </p>
      ) : null}

      <div
        className="flex flex-wrap gap-2 text-sm"
        data-testid="setup-alert-review-filters"
      >
        <select
          className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1"
          value={filterSymbol}
          onChange={(event) => {
            setFilterSymbol(event.target.value);
            if (requestedAlertId) {
              clearAlertQuery("Cleared source-alert query because filters changed.");
            }
          }}
          aria-label="Filter by symbol"
        >
          <option value="">All symbols</option>
          {Object.keys(summary?.by_symbol ?? {})
            .sort()
            .map((symbol) => (
              <option key={symbol} value={symbol}>
                {symbol}
              </option>
            ))}
        </select>
        <select
          className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1"
          value={filterCondition}
          onChange={(event) => {
            setFilterCondition(event.target.value);
            if (requestedAlertId) {
              clearAlertQuery("Cleared source-alert query because filters changed.");
            }
          }}
          aria-label="Filter by condition"
        >
          <option value="">All conditions</option>
          {conditionOptions.map((condition) => (
            <option key={condition} value={condition}>
              {setupConditionLabel(condition)}
            </option>
          ))}
        </select>
        <select
          className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1"
          value={filterTimeframe}
          onChange={(event) => {
            setFilterTimeframe(event.target.value);
            if (requestedAlertId) {
              clearAlertQuery("Cleared source-alert query because filters changed.");
            }
          }}
          aria-label="Filter by timeframe"
        >
          <option value="">All timeframes</option>
          <option value="15m">15m</option>
          <option value="1h">1h</option>
          <option value="4h">4h</option>
        </select>
        <select
          className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1"
          value={filterDirection}
          onChange={(event) => {
            setFilterDirection(event.target.value);
            if (requestedAlertId) {
              clearAlertQuery("Cleared source-alert query because filters changed.");
            }
          }}
          aria-label="Filter by direction"
        >
          <option value="">All directions</option>
          <option value="long">Long</option>
          <option value="short">Short</option>
        </select>
        <select
          className="rounded border border-zinc-700 bg-zinc-950 px-2 py-1"
          value={filterReviewStatus}
          onChange={(event) => {
            setFilterReviewStatus(event.target.value);
            if (requestedAlertId) {
              clearAlertQuery("Cleared source-alert query because filters changed.");
            }
          }}
          aria-label="Filter by review status"
        >
          <option value="">All review statuses</option>
          {REVIEW_STATUSES.map((status) => (
            <option key={status} value={status}>
              {reviewStatusLabel(status)}
            </option>
          ))}
        </select>
        <input
          type="number"
          min={0}
          max={1}
          step={0.05}
          placeholder="Min confidence"
          className="w-36 rounded border border-zinc-700 bg-zinc-950 px-2 py-1"
          value={filterMinConfidence}
          onChange={(event) => {
            setFilterMinConfidence(event.target.value);
            if (requestedAlertId) {
              clearAlertQuery("Cleared source-alert query because filters changed.");
            }
          }}
          aria-label="Minimum confidence"
        />
      </div>

      {actionMessage ? (
        <p className="text-sm text-zinc-400" data-testid="setup-alert-action-message">
          {actionMessage}
        </p>
      ) : null}

      {data?.items.length ? (
        <div className="space-y-3" data-testid="setup-alert-review-list">
          {data.items.map((alert) => {
            const highlighted = Boolean(
              requestedAlertId && alert.alert_id === requestedAlertId,
            );
            return (
              <SetupAlertReviewCard
                key={alert.alert_id}
                alert={alert}
                busy={busy}
                highlighted={highlighted}
                cardRef={
                  highlighted
                    ? (node) => {
                        highlightedRef.current = node;
                      }
                    : undefined
                }
                onSave={persistReview}
                onQuickAction={(alertId, reviewStatus) => persistReview(alertId, reviewStatus)}
                onCreateDraft={createPaperDraft}
              />
            );
          })}
        </div>
      ) : (
        <EmptyState
          title="No setup alerts to review"
          description="Run a market watcher scan to create in-app setup alerts."
        />
      )}
    </div>
  );
}
