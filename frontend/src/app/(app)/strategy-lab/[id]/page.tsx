"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useState } from "react";

import { BacktestPanel } from "@/components/strategy/BacktestPanel";
import { ExperimentDraftPanel } from "@/components/strategies/ExperimentDraftPanel";
import { PaperValidationPanel } from "@/components/strategy/PaperValidationPanel";
import { StrategyVersionHistory } from "@/components/strategy/StrategyVersionHistory";
import { WorkflowStepper } from "@/components/WorkflowStepper";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, LoadingState } from "@/components/states";
import { WatcherMonitoringPanel } from "@/components/WatcherMonitoringPanel";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import { strategyStatusFor } from "@/lib/strategy-status";
import { buildWorkflowSteps } from "@/lib/workflow-steps";
import { SETUP_TYPE_OPTIONS } from "@/lib/setup-types";

import {
  PAPER_SOURCE_KEYS,
  PAPER_SOURCE_LABELS,
  paperSourcePresentation,
  paperSourceTestId,
  useStrategyPaperSources,
} from "./useStrategyPaperSources";

function setupLabel(value: string) {
  return SETUP_TYPE_OPTIONS.find((o) => o.value === value)?.label ?? value;
}

export default function StrategyDetailPage() {
  const params = useParams();
  const id = String(params.id);
  const loader = useCallback(() => api.strategies.get(id), [id]);
  const { data, loading, error, reload } = useAsyncData(loader, [id]);
  const strategyReady = Boolean(data) && !loading && !error;

  const { sources, refreshAllPaperSources, retryPaperSource, anyPaperSourceLoading } =
    useStrategyPaperSources({
      strategyId: id,
      strategyReady,
    });

  const [paperBusy, setPaperBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const testabilityLoader = useCallback(() => api.strategies.testability(id), [id]);
  const { data: testabilityData } = useAsyncData(testabilityLoader, [id]);

  const versionsLoader = useCallback(() => api.strategies.listVersions(id), [id]);
  const { data: versionsData } = useAsyncData(versionsLoader, [id]);

  const card = data?.latest_card;
  const latestRunId =
    sources.summary.status === "ready" || sources.summary.status === "empty"
      ? sources.summary.data?.runs[0]?.id
      : undefined;

  const panelSummary =
    sources.summary.status === "ready" || sources.summary.status === "empty"
      ? sources.summary.data
      : null;
  const panelEligibility =
    sources.eligibility.status === "ready" ? sources.eligibility.data : null;
  const panelScheduler =
    sources.scheduler.status === "ready" ? sources.scheduler.data : null;
  const panelHistory =
    sources.history.status === "ready" || sources.history.status === "empty"
      ? (sources.history.data?.items ?? [])
      : [];
  const panelAlerts =
    sources.alerts.status === "ready" || sources.alerts.status === "empty"
      ? (sources.alerts.data?.items ?? [])
      : [];
  const panelSignals =
    sources.signals.status === "ready" || sources.signals.status === "empty"
      ? (sources.signals.data?.items ?? [])
      : [];
  const panelTrades =
    sources.trades.status === "ready" || sources.trades.status === "empty"
      ? (sources.trades.data?.items ?? [])
      : [];

  async function withPaperAction(action: () => Promise<void>) {
    setPaperBusy(true);
    setActionError(null);
    try {
      await action();
      await reload();
      await refreshAllPaperSources("refresh");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Paper validation failed");
    } finally {
      setPaperBusy(false);
    }
  }

  return (
    <div className="space-y-6" data-testid="strategy-detail-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{data?.name ?? "Strategy"}</h1>
          <p className="text-sm text-zinc-400">
            Setup: {data ? setupLabel(data.setup_type) : "—"} · v{data?.current_version ?? "—"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            href={`/agent?strategy_id=${encodeURIComponent(id)}`}
            className="inline-flex h-10 items-center rounded-lg border border-zinc-700 px-4 text-sm hover:bg-zinc-900"
          >
            Discuss changes with Agent
          </Link>
        </div>
      </div>

      <Link href="/strategies" className="inline-flex min-h-11 items-center text-accent">← Back to Strategies</Link>
      {data ? <ExperimentDraftPanel strategyId={id} name={data.latest_card?.strategy_name || data.name} /> : null}
      <WatcherMonitoringPanel compact />

      {loading ? <LoadingState label="Loading strategy…" /> : null}
      {error ? <ErrorState message={error} onRetry={() => void reload()} /> : null}
      {actionError ? <ErrorState message={actionError} /> : null}

      {data ? (
        <div className="flex flex-wrap items-center gap-2 text-sm text-zinc-300">
          <Badge variant={strategyStatusFor(data).variant} data-testid="strategy-status-badge">
            {strategyStatusFor(data).label}
          </Badge>
          <span>Backtest: {data.backtest_status ?? "not_run"}</span>
          <span>Paper: {data.paper_validation_status ?? "not_started"}</span>
          {panelEligibility ? (
            <span data-testid="strategy-paper-status">Eligibility: {panelEligibility.status}</span>
          ) : null}
        </div>
      ) : null}

      {data ? (
        <WorkflowStepper
          steps={buildWorkflowSteps({
            strategyId: id,
            hasStructuredRules: testabilityData?.has_structured_rules,
            readyForBacktest: testabilityData?.ready_for_backtest,
            backtestStatus: data.backtest_status,
            paperValidationStatus: data.paper_validation_status,
            paperEligible: panelEligibility?.paper_eligible ?? data.paper_eligible,
            unresolvedLessonCount: panelEligibility?.unresolved_lesson_candidates.length,
          })}
        />
      ) : null}

      {card ? (
        <div className="grid gap-4 md:grid-cols-2">
          {[
            ["Entry", card.entry_conditions],
            ["Confirmation", card.confirmation_conditions],
            ["Invalidation", card.invalidation],
            ["Stop loss", card.stop_loss],
            ["Take profit", card.take_profit_plan],
            ["Runner", card.runner_plan],
            ["No trade rules", card.no_trade_rules],
          ].map(([title, items]) => (
            <Card key={title as string}>
              <CardHeader>
                <CardTitle className="text-base">{title as string}</CardTitle>
              </CardHeader>
              <CardContent className="text-sm text-zinc-300">
                <ul className="list-disc space-y-1 pl-4">
                  {(items as string[]).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}

      {versionsData ? <StrategyVersionHistory versions={versionsData.items} /> : null}


      <section
        aria-labelledby="strategy-paper-sources-heading"
        className="space-y-2"
        data-testid="strategy-paper-sources"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2
            id="strategy-paper-sources-heading"
            className="text-lg font-semibold text-text-primary"
          >
            Paper validation sources
          </h2>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!strategyReady || anyPaperSourceLoading || paperBusy}
            onClick={() => void refreshAllPaperSources("refresh")}
            data-testid="strategy-paper-sources-retry"
          >
            Retry all paper sources
          </Button>
        </div>

        <ul className="grid gap-2 sm:grid-cols-2" data-testid="strategy-paper-source-list">
          {PAPER_SOURCE_KEYS.map((key) => {
            const slot = sources[key];
            const presentation = paperSourcePresentation(slot);
            const testId = paperSourceTestId(key);
            return (
              <li
                key={key}
                data-testid={testId}
                data-source-status={slot.status}
                data-source-stale={slot.stale ? "true" : "false"}
                className="rounded-control border border-border-subtle px-3 py-2 text-sm"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <p className="font-medium text-text-primary">{PAPER_SOURCE_LABELS[key]}</p>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={
                      !strategyReady ||
                      slot.status === "loading" ||
                      slot.status === "idle" ||
                      paperBusy
                    }
                    onClick={() => retryPaperSource(key)}
                    data-testid={`${testId}-retry`}
                  >
                    Retry
                  </Button>
                </div>
                <p
                  className={
                    presentation.tone === "failed"
                      ? "mt-1 text-danger"
                      : presentation.tone === "waiting"
                        ? "mt-1 text-warning"
                        : presentation.tone === "loading"
                          ? "mt-1 text-text-secondary"
                          : "mt-1 text-text-secondary"
                  }
                  role={presentation.tone === "failed" ? "alert" : "status"}
                  data-testid={`${testId}-message`}
                >
                  {presentation.message}
                </p>
                {slot.stale ? (
                  <p className="mt-1 text-xs text-warning" data-testid={`${testId}-stale`}>
                    Previous snapshot cleared while reloading.
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <BacktestPanel
          strategyId={id}
          onRun={(body) => api.strategies.requestBacktest(id, body)}
          onLoadTrades={(runId) => api.strategies.listBacktestTrades(runId)}
          onListRuns={() => api.strategies.listBacktests(id)}
        />
        <PaperValidationPanel
          summary={panelSummary}
          eligibility={panelEligibility}
          scheduler={panelScheduler}
          history={panelHistory}
          alerts={panelAlerts}
          busy={paperBusy || anyPaperSourceLoading}
          signals={panelSignals}
          trades={panelTrades}
          onStart={() =>
            void withPaperAction(async () => {
              await api.strategies.startPaperValidation(id, { runtime_mode: "scan_only" });
            })
          }
          onScan={() =>
            void withPaperAction(async () => {
              if (!latestRunId) throw new Error("Start paper validation first.");
              await api.strategies.scanPaperValidation(latestRunId);
            })
          }
          onTick={() =>
            void withPaperAction(async () => {
              if (!latestRunId) throw new Error("Start paper validation first.");
              await api.strategies.tickPaperValidation(latestRunId);
            })
          }
          onStop={() =>
            void withPaperAction(async () => {
              if (!latestRunId) throw new Error("Start paper validation first.");
              await api.strategies.stopPaperValidation(latestRunId);
            })
          }
          onSchedulerTick={() =>
            void withPaperAction(async () => {
              await api.strategies.schedulerTick();
            })
          }
          onMarkAlertRead={(alertId) =>
            void withPaperAction(async () => {
              await api.alerts.markRead(alertId);
            })
          }
        />
      </div>
    </div>
  );
}
