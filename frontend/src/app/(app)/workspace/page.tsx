"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import { ConfidenceBadge } from "@/components/ConfidenceBadge";
import { KillSwitchButton } from "@/components/KillSwitchButton";
import { NarrativePanel } from "@/components/NarrativePanel";
import { RiskBadge } from "@/components/RiskBadge";
import { StatusBadge } from "@/components/StatusBadge";
import { TradingAnalysisPanel } from "@/components/TradingAnalysisPanel";
import {
  PlanSummary,
  WorkflowFreshnessAdapter,
  buildPlanHierarchy,
  describeSafetyPosture,
  loadSource,
  lookupPlanSignalContext,
  type SourceResult,
} from "@/components/workflows";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { RiskBlock } from "@/components/ui/risk-block";
import { PaperModeIndicator } from "@/components/ui/paper-mode-indicator";
import { ErrorState, LoadingState } from "@/components/states";
import { useAppContext, useSafetyPosture } from "@/contexts/AppContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { AgentMessageResponse, ConversationMessageRecord } from "@/lib/api/types";

type PlanHubData = {
  proposals: SourceResult<Awaited<ReturnType<typeof api.proposals.list>>>;
  approvals: SourceResult<Awaited<ReturnType<typeof api.approvals.list>>>;
};

export default function WorkspacePage() {
  const searchParams = useSearchParams();
  const signalLookup = useMemo(
    () => lookupPlanSignalContext(searchParams),
    [searchParams],
  );
  const signalContext = signalLookup.status === "ready" ? signalLookup.context : null;
  const { killSwitchActive } = useAppContext();
  const { executionMode, realTradingEnabled, providerMode } = useSafetyPosture();
  const [message, setMessage] = useState("");
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [thread, setThread] = useState<ConversationMessageRecord[]>([]);
  const [response, setResponse] = useState<AgentMessageResponse | null>(null);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [assistOpen, setAssistOpen] = useState(false);
  const [invalidContextDismissed, setInvalidContextDismissed] = useState(false);

  const signalLookupKey = searchParams.toString();
  useEffect(() => {
    setInvalidContextDismissed(false);
  }, [signalLookupKey]);

  useEffect(() => {
    const stored = window.sessionStorage.getItem("alphatrade.workspace.conversation_id");
    if (stored) setConversationId(stored);
  }, []);

  useEffect(() => {
    if (!conversationId) return;
    window.sessionStorage.setItem("alphatrade.workspace.conversation_id", conversationId);
    void api.conversations
      .listMessages(conversationId, { limit: 100 })
      .then((page) => setThread(page.items))
      .catch(() => {
        /* Thread reload is best-effort; the latest reply still renders. */
      });
  }, [conversationId]);

  const invalidContextMessage =
    signalLookup.status === "invalid" && !invalidContextDismissed
      ? signalLookup.message
      : null;

  const loader = useCallback(async (): Promise<PlanHubData> => {
    const [proposals, approvals] = await Promise.all([
      loadSource(api.proposals.list({ limit: 50 })),
      loadSource(api.approvals.list({ limit: 50 })),
    ]);
    return { proposals, approvals };
  }, []);

  const { data, loading, error, reload } = useAsyncData(loader, []);

  const proposalsAvailable = data?.proposals.available ?? false;
  const approvalsAvailable = data?.approvals.available ?? false;
  const bothSourcesAvailable = proposalsAvailable && approvalsAvailable;
  const bothFailed = Boolean(data) && !proposalsAvailable && !approvalsAvailable;
  const partialData = Boolean(data) && !bothSourcesAvailable && !bothFailed;

  const plan = useMemo(() => {
    if (!data) return null;
    if (!proposalsAvailable && !approvalsAvailable) return null;
    return buildPlanHierarchy({
      proposals: proposalsAvailable ? data.proposals.data?.items ?? [] : [],
      approvals: approvalsAvailable ? data.approvals.data?.items ?? [] : [],
    });
  }, [data, proposalsAvailable, approvalsAvailable]);

  const pendingApprovals = approvalsAvailable
    ? (data?.approvals.data?.items.filter(
        (item) => item.status === "pending" || item.status === "needs_more_analysis",
      ).length ?? 0)
    : null;

  async function sendMessage() {
    if (!message.trim() || killSwitchActive) return;
    if (response?.risk_result?.action === "block") return;
    setChatLoading(true);
    setChatError(null);
    try {
      const result = await api.chat.message({
        message,
        conversation_id: conversationId,
        symbol,
        timeframe,
      });
      setResponse(result);
      setConversationId(result.conversation_id);
      setMessage("");
    } catch (err) {
      setChatError(err instanceof Error ? err.message : "Chat request failed");
    } finally {
      setChatLoading(false);
    }
  }

  const posture = describeSafetyPosture(executionMode, realTradingEnabled);
  const freshnessSources = [
    {
      name: "proposals",
      available: proposalsAvailable,
      required: true,
      timestamp: data?.proposals.data?.items[0]?.created_at ?? null,
    },
    {
      name: "approvals",
      available: approvalsAvailable,
      required: true,
      timestamp: data?.approvals.data?.items[0]?.created_at ?? null,
    },
  ];

  const unavailableSources = [
    !proposalsAvailable ? "Proposals" : null,
    !approvalsAvailable ? "Approvals" : null,
  ].filter((item): item is string => Boolean(item));

  return (
    <div className="space-y-section" data-testid="plan-hub-page">
      <WorkflowFreshnessAdapter sources={freshnessSources} />

      <PageHeader
        title="Plan"
        description="What trade am I preparing, and is it approved? Paper planning only."
        meta={<PaperModeIndicator active={posture.paperConfirmed} />}
      />

      <div className="flex flex-wrap items-center gap-2" data-testid="plan-hub-safety">
        <StatusBadge
          label={posture.executionLabel}
          tone={
            posture.paperConfirmed
              ? "paper"
              : posture.kind === "safety_conflict"
                ? "blocked"
                : "warn"
          }
        />
        <StatusBadge label={`providers: ${providerMode}`} tone="muted" />
        <StatusBadge
          label={posture.realTradingLabel}
          tone={
            posture.realTradingVariant === "success"
              ? "healthy"
              : posture.realTradingVariant === "danger"
                ? "blocked"
                : "warn"
          }
        />
        <StatusBadge
          label={posture.runtimeBadgeLabel}
          tone={
            posture.runtimeBadgeVariant === "paper"
              ? "paper"
              : posture.runtimeBadgeVariant === "danger"
                ? "blocked"
                : "warn"
          }
        />
        <StatusBadge
          label={
            pendingApprovals == null
              ? "Approvals unavailable"
              : `${pendingApprovals} awaiting approval`
          }
          tone={pendingApprovals != null && pendingApprovals > 0 ? "warn" : "muted"}
        />
        <KillSwitchButton />
      </div>

      {posture.conflictMessage ? (
        <p className="text-sm text-danger" role="alert" data-testid="plan-safety-conflict">
          {posture.conflictMessage}
        </p>
      ) : null}

      {killSwitchActive ? (
        <ErrorState message="Kill switch is active. Agent requests are paused until you reset it." />
      ) : null}

      {invalidContextMessage ? (
        <div
          role="alert"
          data-testid="plan-signal-context-invalid"
          className="rounded-control border border-warning-border bg-warning-muted/40 px-4 py-3 text-sm"
        >
          <p className="font-medium text-text-primary">{invalidContextMessage}</p>
          <p className="mt-1 text-text-secondary">
            Planning continues without applying the deep-link signal context.
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="mt-2"
            onClick={() => setInvalidContextDismissed(true)}
            data-testid="plan-signal-context-dismiss"
          >
            Dismiss
          </Button>
        </div>
      ) : null}

      <nav aria-label="Plan hub sections" className="flex flex-wrap gap-3 text-sm">
        <Link href="/workspace" className="font-medium text-text-primary underline">
          Plan hub
        </Link>
        <Link href="/proposals" className="text-text-secondary underline">
          Proposals
        </Link>
        <Link href="/approvals" className="text-text-secondary underline">
          Approvals
        </Link>
        <Link href="/pre-trade" className="text-text-secondary underline">
          Pre-Trade
        </Link>
        <Link href="/manual-levels" className="text-text-secondary underline">
          Manual Levels
        </Link>
        <Link href="/strategy-lab" className="text-text-secondary underline">
          Strategy Lab
        </Link>
      </nav>

      {loading ? (
        <LoadingState label="Loading plan hub…" />
      ) : (
        <PlanSummary
          plan={plan}
          error={
            bothFailed
              ? "Plan data unavailable: proposals and approvals both failed to load."
              : error
          }
          onRetry={() => void reload()}
          posture={posture}
          partialData={partialData}
          unavailableSources={unavailableSources}
          bothSourcesAvailable={bothSourcesAvailable}
          signalContext={signalContext}
        />
      )}

      <div className="flex flex-wrap gap-2">
        <Button type="button" onClick={() => setAssistOpen((open) => !open)}>
          {assistOpen ? "Hide AI assist" : "New plan / AI assist"}
        </Button>
        <Link
          href="/proposals"
          className="inline-flex h-10 items-center rounded-control border border-border bg-surface-1 px-4 text-sm font-medium text-text-primary hover:bg-surface-2"
        >
          Browse proposals
        </Link>
      </div>

      {assistOpen ? (
        <Card data-testid="plan-hub-ai-assist">
          <CardHeader>
            {/* Explicit h2 so CardTitle (h3) is not the first heading under the page h1. */}
            <h2 className="text-base font-semibold tracking-tight text-text-primary">
              AI assistance
            </h2>
            <p className="text-sm text-text-muted">
              Embedded ticket assist. Sensitive actions still require approval. No live orders.
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            {response?.risk_result?.action === "block" ? (
              <RiskBlock
                reason={
                  response.risk_result.explanation ||
                  "Risk engine BLOCKED this request. No override is available."
                }
                ruleReference={response.risk_result.triggered_rules?.[0]?.rule_id}
              />
            ) : null}
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="symbol">Symbol</Label>
                <Input
                  id="symbol"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                  disabled={response?.risk_result?.action === "block"}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="timeframe">Timeframe</Label>
                <Input
                  id="timeframe"
                  value={timeframe}
                  onChange={(e) => setTimeframe(e.target.value)}
                  disabled={response?.risk_result?.action === "block"}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="message">Message</Label>
              <Textarea
                id="message"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Ask for a disciplined trade plan review…"
                disabled={response?.risk_result?.action === "block"}
              />
            </div>
            {thread.length ? (
              <ol
                className="max-h-64 space-y-2 overflow-y-auto rounded-control border border-border p-3 text-sm"
                data-testid="workspace-conversation-thread"
              >
                {thread.map((item) => (
                  <li key={item.id}>
                    <p className="text-xs uppercase text-text-muted">{item.role}</p>
                    <p className="whitespace-pre-wrap text-text-secondary">{item.content}</p>
                  </li>
                ))}
              </ol>
            ) : null}
            {response?.pending_proposal ? (
              <p className="text-sm text-text-muted" data-testid="workspace-pending-proposal">
                Structured proposal {response.pending_proposal.status}. Drafts do not change
                strategy versions until explicit confirmation.
              </p>
            ) : null}
            <Button
              disabled={
                chatLoading ||
                killSwitchActive ||
                response?.risk_result?.action === "block"
              }
              onClick={() => void sendMessage()}
            >
              {chatLoading ? "Sending…" : "Send message"}
            </Button>
            {chatLoading ? <LoadingState label="Waiting for agent response…" /> : null}
            {chatError ? <ErrorState message={chatError} /> : null}
          </CardContent>
        </Card>
      ) : null}

      {response ? (
        <div className="space-y-4">
          {response.analysis ? <TradingAnalysisPanel analysis={response.analysis} /> : null}
          {response.narrative ? (
            <NarrativePanel
              narrative={response.narrative}
              narrativeMeta={response.narrative_meta}
              analysis={response.analysis}
            />
          ) : null}

          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle>Combined reply</CardTitle>
                <RiskBadge level={response.risk_level} />
                <ConfidenceBadge value={response.confidence} />
                <StatusBadge label={response.approval_status} tone="pending" />
                {response.approval_required ? (
                  <StatusBadge label="Approval required" tone="warn" />
                ) : null}
              </div>
            </CardHeader>
            <CardContent className="space-y-4 text-sm text-text-secondary">
              <p className="whitespace-pre-wrap">{response.reply}</p>
              {(response.limitations ?? []).length ? (
                <ul className="list-disc space-y-1 pl-5 text-text-muted">
                  {(response.limitations ?? []).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
            </CardContent>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
