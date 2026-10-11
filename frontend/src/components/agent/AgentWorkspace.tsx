"use client";

import Link from "next/link";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { FileUp, Send } from "lucide-react";
import { SavedReceipt } from "@/components/agent/SavedReceipt";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { AGENT_TURN_TIMEOUT_MS, waitForAgent } from "@/lib/agent-turn-budget";
import type { VoiceTurn, VoiceTurnResult } from "@/lib/voice/conversation-types";
import { createAgentVoiceTransport } from "./agent-voice-transport";
import { ApiError } from "@/lib/api/client";
import { agentTurnRequest } from "@/lib/api/generated/validators";
import { clearPendingTurns, pendingFor, persistPending, persistTypedDraft, typedDraftFor, recordTerminal, subscribeRecovery, recoveryDetails, removePending, type PendingTurn, type TurnBody } from "./turn-recovery";
import { StrategyDraftReview } from "./StrategyDraftReview";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { acknowledgedMessages, mergeProposals, proposalsFromMessages, reconcileMessages } from "./acknowledged-turn";
import { AgentMessageContent } from "./AgentMessageContent";
import { VoiceConversationControls } from "./VoiceConversationControls";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select, Textarea } from "@/components/ui/input";
import { formatDateTime } from "@/lib/format";
import { api } from "@/lib/api";
import type {
  AgentStructuredProposal,
  AgentTurnResult,
  ConversationMessageRecord,
  ConversationSummary,
} from "@/lib/api/types";
import { cn } from "@/lib/utils";

type LoadState<T> = {
  items: T[];
  error: string | null;
  loading: boolean;
};

const emptyLoad = { items: [], error: null, loading: true };
export { AGENT_TURN_TIMEOUT_MS } from "@/lib/agent-turn-budget";

function messageLabel(role: ConversationMessageRecord["role"]): string {
  if (role === "user") return "You";
  if (role === "assistant") return "Agent";
  return "System";
}

function visibleReply(reply: string | null): string | null {
  return (
    reply?.split("\n\nRecorded facts (not a confirmation):\n", 1)[0] ?? null
  );
}

function sourceDocumentFor(messages: ConversationMessageRecord[], messageId?: string | null): string | undefined {
  const message = messages.find(item => item.id === messageId && item.role === "user");
  const payload = message?.payload?.interactive_agent as Record<string, unknown> | undefined;
  return typeof payload?.source_document_id === "string" ? payload.source_document_id : undefined;
}

export function AgentWorkspace() {
  const { user, organization } = useAuth();
  const [sessionLive, setSessionLive] = useState(true);
  const recoveryScope = user && organization ? `${organization.id}:${user.id}` : null;
  const currentScope = useRef(recoveryScope);
  currentScope.current = recoveryScope;
  const params = useSearchParams();
  const strategyId = params.get("strategy_id");
  const [strategyContext, setStrategyContext] = useState<{ id: string; name: string } | null>(null);
  const [strategyError, setStrategyError] = useState<string | null>(null);
  const historyRef = useRef<HTMLDivElement>(null);
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(max-width: 1023px)");
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const [historyOpen, setHistoryOpen] = useState(false);
  const closeHistory = useCallback(() => setHistoryOpen(false), []);
  useFocusTrap(historyRef, historyOpen && mobile, closeHistory);
  const [attached, setAttached] = useState<File | null>(null);
  const [attachmentPreview, setAttachmentPreview] = useState<Awaited<
    ReturnType<typeof api.knowledge.previewFile>
  > | null>(null);
  const [sourceDocumentId, setSourceDocumentId] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const sendInFlight = useRef(false);
  const turnAbort = useRef<AbortController | null>(null);
  const turnConversation = useRef<string | null>(null);
  const contextGeneration = useRef(0);
  const activeConversation = useRef<string | null>(params.get("conversation"));
  const retainedMessages = useRef(new Map<string, ConversationMessageRecord[]>());
  const historyAbort = useRef<AbortController | null>(null);
  const decisionAbort = useRef<AbortController | null>(null);
  const strategyAbort = useRef<AbortController | null>(null);
  const historyGeneration = useRef(0);
  const mounted = useRef(true);
  const conversationListGeneration = useRef(0);
  const [voiceContextKey, setVoiceContextKey] = useState(0);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesRetry, setMessagesRetry] = useState(0);
  const [conversations, setConversations] =
    useState<LoadState<ConversationSummary>>(emptyLoad);
  const [conversationId, setConversationId] = useState<string | null>(
    params.get("conversation"),
  );
  const [messages, setMessages] = useState<ConversationMessageRecord[]>([]);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const typedDrafts = useRef(new Map<string | null, string>());
  const draftScope = useRef<string | null>(null);
  const [setupType, setSetupType] = useState("htf_trend_pullback");
  const [importOpen, setImportOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendOrigin, setSendOrigin] = useState<"text" | "voice" | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [latest, setLatest] = useState<AgentTurnResult | null>(null);
  const [proposals, setProposals] = useState<AgentStructuredProposal[]>([]);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const pendingRef = useRef<PendingTurn | null>(null);

  const rememberPending = useCallback((value: PendingTurn | null) => {
    if (recoveryScope) {
      if (value) persistPending(recoveryScope, value);
      else if (pendingRef.current) removePending(recoveryScope, pendingRef.current.key);
    }
    pendingRef.current = value;
    setPending(value);
  }, [recoveryScope]);

  useEffect(() => {
    if (!sessionLive || !recoveryScope) { draftScope.current = null; return; }
    if (draftScope.current !== recoveryScope) {
      draftScope.current = recoveryScope;
      setDraft(typedDraftFor(recoveryScope, conversationId));
      return;
    }
    persistTypedDraft(recoveryScope, conversationId, draft);
  }, [draft, conversationId, recoveryScope, sessionLive]);

  useEffect(() => {
    const value = recoveryScope ? pendingFor(recoveryScope, conversationId) : null;
    pendingRef.current = value;
    setPending(value);
  }, [recoveryScope, conversationId]);

  useEffect(() => {
    contextGeneration.current++;
    turnAbort.current?.abort();
    typedDrafts.current.clear();
  }, [recoveryScope]);

  useEffect(() => {
    setSessionLive(Boolean(recoveryScope));
    if (!recoveryScope) return;
    return subscribeRecovery(recoveryScope, () => {
      const value = pendingFor(recoveryScope, activeConversation.current);
      pendingRef.current = value;
      if (mounted.current) setPending(value);
    });
  }, [recoveryScope]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      turnAbort.current?.abort();
      historyAbort.current?.abort();
      decisionAbort.current?.abort();
      strategyAbort.current?.abort();
    };
  }, []);

  const changeConversation = useCallback((id: string | null, preserveDraft = true) => {
    if (preserveDraft) {
      typedDrafts.current.set(activeConversation.current, draftRef.current);
      if (recoveryScope && sessionLive) persistTypedDraft(recoveryScope, activeConversation.current, draftRef.current);
    }
    setVoiceContextKey(value => value + 1);
    contextGeneration.current++;
    activeConversation.current = id;
    const address = new URL(window.location.href);
    if (id) address.searchParams.set("conversation", id);
    else address.searchParams.delete("conversation");
    window.history.replaceState(null, "", address.pathname + address.search);
    turnAbort.current?.abort();
    historyAbort.current?.abort();
    decisionAbort.current?.abort();
    setConversationId(id);
    setMessages(id ? retainedMessages.current.get(id) ?? [] : []);
    setLatest(null);
    setProposals(id ? proposalsFromMessages(retainedMessages.current.get(id) ?? []) : []);
    setDecidingId(null);
    setDraft(typedDrafts.current.get(id) ?? (recoveryScope ? typedDraftFor(recoveryScope, id) : ""));
    setAttached(null);
    setAttachmentPreview(null);
    setSourceDocumentId(null);
    setSendError(null);
    setStrategyContext(null);
    setStrategyError(null);
  }, [recoveryScope, sessionLive]);

  useEffect(() => {
    const controller = new AbortController();
    strategyAbort.current = controller;
    setStrategyContext(null);
    setStrategyError(null);
    async function loadBinding() {
      try {
        // Persisted conversation binding takes precedence over the entry URL.
        const binding = conversationId
          ? (await api.conversations.get(conversationId)).strategy_id
          : strategyId;
        if (controller.signal.aborted || !binding) return;
        const strategy = await api.strategies.get(binding, { signal: controller.signal });
        if (!controller.signal.aborted && strategy.id === binding)
          setStrategyContext({ id: strategy.id, name: strategy.name });
      } catch (error) {
        if (!controller.signal.aborted)
          setStrategyError(error instanceof Error ? error.message : "Strategy binding unavailable");
      }
    }
    void loadBinding();
    return () => controller.abort();
  }, [strategyId, conversationId]);

  const urlConversation = params.get("conversation");
  useEffect(() => {
    if (urlConversation !== activeConversation.current) changeConversation(urlConversation);
  }, [urlConversation, changeConversation]);

  const reconcileHistory = useCallback(async (id: string) => {
    historyAbort.current?.abort();
    const controller = new AbortController();
    historyAbort.current = controller;
    const generation = ++historyGeneration.current;
    const isCurrent = () => mounted.current && activeConversation.current === id &&
      generation === historyGeneration.current && !controller.signal.aborted;
    setMessagesError(null);
    setMessagesLoading(true);
    try {
      const page = await api.conversations.listMessages(id, { limit: 100 }, { signal: controller.signal });
      if (!isCurrent()) return;
      const merged = reconcileMessages(page.items, retainedMessages.current.get(id) ?? []);
      retainedMessages.current.set(id, merged);
      setMessages(merged);
      setProposals(current => mergeProposals(current, proposalsFromMessages(merged)));
    } catch (error) {
      if (isCurrent()) setMessagesError(error instanceof Error ? error.message : "Messages unavailable");
    } finally {
      if (isCurrent()) setMessagesLoading(false);
    }
  }, []);

  useEffect(() => onSessionCleared(() => {
    setSessionLive(false);
    clearPendingTurns();
    typedDrafts.current.clear();
    pendingRef.current = null;
    setPending(null);
    retainedMessages.current.clear();
    strategyAbort.current?.abort();
    setStrategyContext(null);
    changeConversation(null, false);
    typedDrafts.current.clear();
    setDraft("");
    setConversations({ items: [], error: null, loading: false });
  }), [changeConversation]);

  const refreshConversations = useCallback(async () => {
    const generation = ++conversationListGeneration.current;
    const session = sessionGeneration();
    const isCurrent = () => mounted.current && generation === conversationListGeneration.current && session === sessionGeneration();
    setConversations((current) => ({ ...current, loading: true, error: null }));
    try {
      const page = await api.conversations.list({ limit: 30 });
      if (!isCurrent()) return;
      setConversations({ items: page.items, error: null, loading: false });
    } catch (error) {
      if (!isCurrent()) return;
      setConversations({
        items: [],
        error:
          error instanceof Error ? error.message : "Conversations unavailable",
        loading: false,
      });
    }
  }, []);

  useEffect(() => {
    void refreshConversations();
  }, [refreshConversations]);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      setMessagesError(null);
      setMessagesLoading(false);
      return;
    }
    // The turn loads its own messages; avoid a competing history read on creation.
    if (turnConversation.current === conversationId) {
      turnConversation.current = null;
      return;
    }
    setMessages(retainedMessages.current.get(conversationId) ?? []);
    void reconcileHistory(conversationId);
  }, [conversationId, messagesRetry, reconcileHistory]);

  async function runTurn(
    message = draft,
    source: "text" | "voice" = "text",
    recovery?: PendingTurn,
    strategyDraft = false,
    voice?: VoiceTurn & { signal: AbortSignal },
  ): Promise<VoiceTurnResult> {
    const text = recovery?.body.message ?? (message.trim() || (attached ? "Please organize this document." : ""));
    if (!text || !sessionLive || !recoveryScope || sendInFlight.current ||
        (!recovery && pendingRef.current) || (!recovery && !conversationId && strategyId && !strategyContext) ||
        (messagesLoading && messages.length === 0) || (voice && voice.conversationId !== activeConversation.current))
      return { outcome: "rejected" };
    const generation = contextGeneration.current;
    const session = sessionGeneration();
    const scope = recoveryScope;
    const isSameSession = () => session === sessionGeneration() && currentScope.current === scope;
    const isCurrent = () => mounted.current && isSameSession() && generation === contextGeneration.current;
    source = recovery?.origin ?? source;
    sendInFlight.current = true;
    const controller = new AbortController();
    const abort = () => controller.abort();
    voice?.signal.addEventListener("abort", abort, { once: true });
    if (voice?.signal.aborted) controller.abort();
    turnAbort.current = controller;
    const timeout = setTimeout(abort, AGENT_TURN_TIMEOUT_MS);
    setSending(true);
    setSendOrigin(source);
    setSendError(null);
    let request: PendingTurn | null = null;
    try {
      let documentId = sourceDocumentId;
      if (!recovery && attached && !documentId) {
        if (!attachmentPreview) throw new Error("Preview the attachment before sending.");
        const imported = await waitForAgent(api.knowledge.importFile(
          attached, attached.name.replace(/\.[^.]+$/, ""), "general_note", attachmentPreview.preview_receipt,
        ), controller.signal);
        documentId = imported.document_id;
        if (!isCurrent()) return { outcome: "rejected" };
        setSourceDocumentId(documentId);
      }
      const body: TurnBody = recovery?.body ?? {
        message: text, strategy_id: strategyContext?.id,
        conversation_id: voice?.conversationId ?? conversationId ?? undefined,
        source_document_id: strategyDraft ? undefined : documentId ?? undefined,
        ...(strategyDraft ? { action: {
          name: strategyContext ? "strategy.refinement" : "strategy.create",
          arguments: { text: (attachmentPreview?.extracted_text ?? text).slice(0, 4000),
            ...(documentId ? { evidence_document_ids: [documentId] } : {}),
            ...(strategyContext ? { strategy_id: strategyContext.id } : { setup_type: setupType }) },
        } } : {}),
      };
      // A locally rejected complete body never becomes an ambiguous saved request.
      if (!agentTurnRequest(body)) throw new ApiError("Invalid API request. Check the supplied fields.", 0, null);
      controller.signal.throwIfAborted();
      request = recovery ?? { key: voice?.turnKey ?? crypto.randomUUID(), body,
        conversationId: voice?.conversationId ?? conversationId, state: "uncertain", createdAt: new Date().toISOString(), origin: source,
        ...(voice ? { voice: { turnKey: voice.turnKey, transcript: voice.transcript,
          conversationId: voice.conversationId, origin: "voice" as const } } : {}) };
      rememberPending(request);
      const original = request;
      const publish = (result: AgentTurnResult): VoiceTurnResult => {
        if (typeof result?.reply !== "string" || typeof result.conversation_id !== "string" ||
            (original.body.conversation_id && result.conversation_id !== original.body.conversation_id))
          throw new ApiError("API returned an invalid response.", 200, null);
        if (!isSameSession()) return { outcome: "uncertain" };
        const retained = reconcileMessages(retainedMessages.current.get(result.conversation_id) ?? [],
          acknowledgedMessages(text, result, original.body.source_document_id ?? undefined));
        retainedMessages.current.set(result.conversation_id, retained);
        const capturePending = result.capture_status === "unavailable" && Boolean(result.capture_error?.includes("pending"));
        if (capturePending) persistPending(scope, { ...original, conversationId: result.conversation_id, state: "turn_capture" });
        else recordTerminal(scope, { turnKey: original.key, conversationId: result.conversation_id,
          outcome: "acknowledged", origin: original.origin });
        // Keep replies in their own conversation; a late A response cannot mutate B.
        if (mounted.current && (isCurrent() || activeConversation.current === result.conversation_id)) {
          if (isCurrent()) {
            setAttached(null); setAttachmentPreview(null); setSourceDocumentId(null);
            if (source === "text") setDraft(current => current.trim() === text ? "" : current);
          }
          setLatest(result);
          setProposals(current => mergeProposals(current, result.proposals ?? []));
          if (activeConversation.current !== result.conversation_id) {
            if (activeConversation.current === null) {
              typedDrafts.current.delete(null);
              persistTypedDraft(scope, null, "");
            }
            turnConversation.current = result.conversation_id;
            activeConversation.current = result.conversation_id;
            const address = new URL(window.location.href);
            address.searchParams.set("conversation", result.conversation_id);
            window.history.replaceState(null, "", address.pathname + address.search);
            setConversationId(result.conversation_id);
          }
          setMessages(retained); setMessagesError(null);
          // Publish the acknowledgment before awaiting or fetching history.
          void reconcileHistory(result.conversation_id);
          void refreshConversations();
        }
        return capturePending ? { outcome: "uncertain" }
          : { outcome: "acknowledged", reply: visibleReply(result.reply) ?? result.reply };
      };
      // Observe proven late acknowledgments even when the caller stopped waiting.
      const response = api.agent.turn(original.body,
        { signal: controller.signal, headers: { "Idempotency-Key": original.key } }).then(publish);
      return await waitForAgent(response, controller.signal);
    } catch (error) {
      if (!isSameSession()) return { outcome: "uncertain" };
      const conflict = error instanceof ApiError && error.status === 409 ? recoveryDetails(error.body) : null;
      if (conflict && request) persistPending(scope, { ...request, state: conflict.reason, conversationId: conflict.conversation_id });
      if (isCurrent()) setSendError(controller.signal.aborted
        ? "Agent response timed out or was interrupted. Keep this request and recover its saved result."
        : error instanceof Error ? error.message : "Message failed");
      return { outcome: request ? "uncertain" : "rejected" };
    } finally {
      clearTimeout(timeout);
      voice?.signal.removeEventListener("abort", abort);
      if (turnAbort.current === controller) {
        turnAbort.current = null;
        sendInFlight.current = false;
        if (mounted.current) { setSending(false); setSendOrigin(null); }
      }
    }
  }

  async function sendMessage(message = draft, source: "text" | "voice" = "text", recovery?: PendingTurn, strategyDraft = false): Promise<boolean> {
    return (await runTurn(message, source, recovery, strategyDraft)).outcome === "acknowledged";
  }

  const voiceRun = useRef(runTurn);
  voiceRun.current = runTurn;
  const voiceTransport = useMemo(() => createAgentVoiceTransport(recoveryScope ?? "", (request, recovery) =>
    voiceRun.current(request.transcript, "voice", recovery, false, request)), [recoveryScope]);

  async function ensureConversation(signal: AbortSignal): Promise<string | null> {
    if (activeConversation.current) return activeConversation.current;
    if (!sessionLive || !recoveryScope || sendInFlight.current || pendingRef.current || (strategyId && !strategyContext)) return null;
    const generation = contextGeneration.current;
    const session = sessionGeneration();
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) controller.abort();
    sendInFlight.current = true;
    turnAbort.current = controller;
    setSending(true); setSendOrigin("voice");
    const timer = setTimeout(abort, AGENT_TURN_TIMEOUT_MS);
    try {
      const conversation = await waitForAgent(api.conversations.create(strategyContext ? { strategy_id: strategyContext.id } : {}), controller.signal);
      if (!mounted.current || generation !== contextGeneration.current || session !== sessionGeneration()) return null;
      typedDrafts.current.delete(null);
      persistTypedDraft(recoveryScope, null, "");
      activeConversation.current = conversation.id;
      turnConversation.current = conversation.id;
      setConversationId(conversation.id);
      const address = new URL(window.location.href);
      address.searchParams.set("conversation", conversation.id);
      window.history.replaceState(null, "", address.pathname + address.search);
      void refreshConversations();
      return conversation.id;
    } finally {
      clearTimeout(timer); signal.removeEventListener("abort", abort);
      if (turnAbort.current === controller) {
        turnAbort.current = null; sendInFlight.current = false;
        if (mounted.current) { setSending(false); setSendOrigin(null); }
      }
    }
  }

  async function decideProposal(
    proposal: AgentStructuredProposal,
    statement: "I confirm" | "I reject",
  ) {
    if (decidingId) return;
    const generation = contextGeneration.current;
    const controller = new AbortController();
    decisionAbort.current = controller;
    const isCurrent = () => mounted.current && generation === contextGeneration.current && !controller.signal.aborted;
    setDecidingId(proposal.proposal_id);
    setSendError(null);
    try {
      const request = {
        conversation_id: proposal.conversation_id,
        expected_content_hash: proposal.content_hash,
        statement,
      };
      const updated =
        statement === "I confirm"
          ? await api.agent.confirmProposal(proposal.proposal_id, request, { signal: controller.signal })
          : await api.agent.rejectProposal(proposal.proposal_id, request, { signal: controller.signal });
      if (!isCurrent()) return;
      const retained = retainedMessages.current.get(updated.conversation_id) ?? [];
      const next = retained.map(message => {
        const values = proposalsFromMessages([message]);
        if (!values.some(item => item.proposal_id === updated.proposal_id)) return message;
        return { ...message, payload: { ...message.payload, interactive_agent: {
          ...(message.payload?.interactive_agent as Record<string, unknown>),
          proposals: values.map(item => item.proposal_id === updated.proposal_id ? updated : item),
        } } };
      });
      retainedMessages.current.set(updated.conversation_id, next);
      setProposals((current) =>
        current.map((item) =>
          item.proposal_id === updated.proposal_id ? updated : item,
        ),
      );
    } catch (error) {
      if (!isCurrent()) return;
      setSendError(
        error instanceof Error ? error.message : "Proposal decision failed",
      );
    } finally {
      if (isCurrent()) setDecidingId(null);
    }
  }

  return (
    <div
      className="space-y-4 [&_input]:text-base [&_select]:text-base [&_textarea]:text-base lg:[&_input]:text-sm lg:[&_select]:text-sm lg:[&_textarea]:text-sm"
      data-testid="agent-workspace"
    >
      <h1 className="sr-only">Agent</h1>
      {strategyContext || strategyError || (strategyId && !conversationId) ? <div className="flex flex-wrap gap-3 text-sm" data-testid="agent-strategy-context">
        {strategyContext ? <><span>Strategy: {strategyContext.name}</span>
          <Link href={`/strategy-lab/${encodeURIComponent(strategyContext.id)}`} className="underline">Back to strategy</Link></> :
          strategyError ? <p role="alert">Strategy unavailable: {strategyError}</p> : <p role="status">Loading strategy context…</p>}
      </div> : null}
      <div className="flex flex-wrap gap-2" aria-label="Conversation controls">
        <Button
          type="button"
          variant="secondary"
          className="min-h-11"
          onClick={() => {
            changeConversation(null);
            setMessages([]);
            setLatest(null);
            setProposals([]);
            setSendError(null);
            setAttached(null);
            setAttachmentPreview(null);
            setSourceDocumentId(null);
          }}
        >
          New conversation
        </Button>
        <Button
          variant="outline"
          aria-expanded={historyOpen}
          onClick={() => setHistoryOpen((v) => !v)}
        >
          History
        </Button>
      </div>

      <div
        className={
          historyOpen
            ? "grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]"
            : "grid gap-4"
        }
      >
        {historyOpen && (
          <>
            <button
              className="fixed inset-0 z-50 bg-black/60 lg:hidden"
              aria-label="Close history"
              onClick={() => setHistoryOpen(false)}
            />
            <div
              ref={historyRef}
              role={mobile ? "dialog" : "complementary"}
              aria-modal={mobile ? true : undefined}
              aria-label="Conversation history"
              data-testid="agent-history"
              className="fixed inset-y-0 left-0 z-[60] w-[min(20rem,calc(100vw-2rem))] overflow-y-auto rounded-none bg-surface-0 lg:relative lg:inset-auto lg:z-auto lg:w-auto lg:self-start lg:overflow-visible lg:rounded-card"
            >
              <CardHeader>
                <CardTitle>History</CardTitle>
                <Button variant="ghost" onClick={() => setHistoryOpen(false)}>
                  Close history
                </Button>
              </CardHeader>
              <CardContent className="space-y-2">
                {conversations.loading ? (
                  <p className="text-caption text-text-muted">Loading…</p>
                ) : null}
                {conversations.error ? (
                  <p
                    className="text-sm text-danger"
                    data-testid="agent-history-error"
                  >
                    {conversations.error}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="mt-2"
                      onClick={() => void refreshConversations()}
                    >
                      Retry history
                    </Button>
                  </p>
                ) : null}
                {!conversations.loading &&
                conversations.items.length === 0 &&
                !conversations.error ? (
                  <p className="text-sm text-text-muted">
                    No conversations yet.
                  </p>
                ) : null}
                <ul className="space-y-1">
                  {conversations.items.map((item) => (
                    <li key={item.id}>
                      <button
                        type="button"
                        className={cn(
                          "min-h-11 w-full break-words rounded-control border border-transparent px-3 py-3 text-left text-sm",
                          item.id === conversationId
                            ? "border-accent-border bg-accent-muted text-text-primary"
                            : "text-text-secondary hover:bg-surface-1",
                        )}
                        aria-current={
                          item.id === conversationId ? "true" : undefined
                        }
                        onClick={() => {
                          if (item.id === conversationId) return;
                          setLatest(null);
                          setProposals([]);
                          setSendError(null);
                          changeConversation(item.id);
                          setHistoryOpen(false);
                        }}
                      >
                        {item.title?.trim() || "Untitled"}
                      </button>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </div>
          </>
        )}

        <div className="order-1 flex min-w-0 flex-col gap-4 lg:order-2">
          <Card className="order-1 border-border">
            <CardContent className="space-y-4 pt-4 lg:pt-6">
              {messagesError ? (
                <div
                  role="alert"
                  className="rounded-control border border-danger-border bg-danger-muted p-3 text-sm text-danger"
                >
                  <p>Conversation unavailable: {messagesError}</p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => setMessagesRetry((value) => value + 1)}
                  >
                    Retry conversation
                  </Button>
                </div>
              ) : null}
              <div
                aria-label="Conversation messages"
                aria-busy={messagesLoading}
                className="flex min-h-48 flex-col gap-4 lg:min-h-64"
                data-testid="agent-thread"
              >
                {messagesLoading && messages.length === 0 ? (
                  <p role="status" className="py-6 text-sm text-text-secondary">
                    Loading conversation…
                  </p>
                ) : messages.length === 0 ? (
                  <p className="py-4 text-sm text-text-muted">
                    Start a conversation.
                  </p>
                ) : (
                  messages.map((message) => (
                    <div
                      key={message.id}
                      data-testid="agent-message"
                      data-message-id={message.id}
                      data-role={message.role}
                      className={cn(
                        "min-w-0 max-w-[95%] rounded-card border px-4 py-3 text-sm leading-relaxed sm:max-w-[85%]",
                        message.role === "user"
                          ? "ml-auto border-border bg-surface-2 text-text-primary"
                          : "mr-auto border-border-subtle bg-surface-0 text-text-secondary",
                      )}
                    >
                      <p className="mb-2 text-xs font-medium text-text-secondary">
                        {messageLabel(message.role)} ·{" "}
                        {formatDateTime(message.created_at)}
                      </p>
                      <AgentMessageContent message={message} />
                    </div>
                  ))
                )}
              </div>

              {sending ? (
                <p
                  role="status"
                  className="rounded-control border border-border-subtle bg-surface-0/40 p-3 text-sm text-text-secondary"
                >
                  Agent is reviewing your message…
                </p>
              ) : null}
              {pending ? <div role="status" data-testid="agent-turn-recovery" className="space-y-2 rounded-control border border-warning-border p-3 text-sm">
                <p>{pending.state === "turn_running" || pending.state === "turn_capture" || pending.state === "conversation_turn_in_progress"
                  ? "The request is still processing. Recovery uses its original message and key."
                  : pending.state === "uncertain" ? "The response is uncertain. Recover the original request before starting another."
                    : `Request state: ${pending.state}. Review history before deliberately starting a new turn.`}</p>
                {pending.origin !== "voice" ? <Button disabled={sending} onClick={() => void sendMessage(pending.body.message, "text", pending)}>Recover original request</Button> : null}
                {pending.conversationId ? <Button variant="outline" onClick={() => {
                  changeConversation(pending.conversationId);
                  void reconcileHistory(pending.conversationId!);
                }}>Check saved history</Button> : null}
                {!['uncertain', 'turn_running', 'turn_capture', 'conversation_turn_in_progress'].includes(pending.state)
                  ? <Button variant="outline" onClick={() => rememberPending(null)}>Start a new turn</Button> : null}
              </div> : null}
              {proposals.length > 0 ? (
                <ul className="space-y-2" data-testid="agent-proposals">
                  {proposals.map((proposal) => (
                    <li
                      key={proposal.proposal_id}
                      className="rounded-control border border-warning-border bg-warning-muted/30 p-4"
                      data-proposal-status={proposal.status}
                    >
                      <p className="text-sm text-text-primary">
                        {proposal.summary}
                      </p>
                      <p className="text-caption text-text-muted">
                        Status {proposal.status}. The reply did not confirm this
                        proposal.
                      </p>
                      {proposal.kind === "propose_strategy" ? (
                        <p className="text-caption text-text-muted">
                          Confirm records the request only. The strategy preview
                          stays a draft.
                        </p>
                      ) : null}
                      {proposal.linked_strategy_proposal_id ? <StrategyDraftReview
                        key={proposal.linked_strategy_proposal_id}
                        conversationId={proposal.conversation_id}
                        proposalId={proposal.linked_strategy_proposal_id}
                      /> : null}
                      {proposal.kind === "propose_journal_entry" ? (
                        <p className="text-caption text-text-muted">
                          Confirm writes one journal entry. Sending a message
                          does not.
                        </p>
                      ) : null}
                      {proposal.status === "proposed" ? (
                        <div className="mt-2 flex flex-wrap gap-2">
                          <Button
                            type="button"
                            onClick={() =>
                              void decideProposal(proposal, "I confirm")
                            }
                            disabled={decidingId !== null}
                          >
                            Confirm proposal
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() =>
                              void decideProposal(proposal, "I reject")
                            }
                            disabled={decidingId !== null}
                          >
                            Reject proposal
                          </Button>
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
              {messages
                .filter((m) => m.role === "assistant")
                .map((message) => {
                  const payload = message.payload?.interactive_agent as
                    Record<string, unknown> | undefined;
                  const capture = payload?.capture as
                    Parameters<typeof SavedReceipt>[0]["capture"] | undefined;
                  return capture ? (
                    <SavedReceipt
                      key={`receipt:${message.id}`}
                      capture={capture}
                      conversationId={conversationId!}
                      sourceDocumentId={sourceDocumentFor(messages, capture.source_message_id)}
                    />
                  ) : null;
                })}
              {latest &&
                !messages.some(
                  (m) =>
                    m.id === latest.assistant_message_id &&
                    (
                      m.payload?.interactive_agent as
                        Record<string, unknown> | undefined
                    )?.capture,
                ) && (
                  <SavedReceipt
                    conversationId={latest.conversation_id}
                    sourceDocumentId={sourceDocumentFor(messages, latest.capture_source_message_id)}
                    capture={{
                      saved_entries: latest.saved_entries ?? [],
                      status: latest.capture_status ?? "not_needed",
                      error: latest.capture_error,
                      clarification: latest.capture_clarification,
                      source_message_id: latest.capture_source_message_id,
                    }}
                  />
                )}
              <form
                className="space-y-3 border-t border-border-subtle pt-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void sendMessage();
                }}
              >
                <Textarea
                  aria-label="Message"
                  className="min-h-20 resize-y"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder="Ask about a paper trade, a rule, or a journal note."
                />
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="submit"
                    disabled={
                      !sessionLive || sending || Boolean(pending) ||
                      Boolean(!conversationId && strategyId && !strategyContext) ||
                      (messagesLoading && messages.length === 0) ||
                      (!draft.trim() && !attached) ||
                      (Boolean(attached) && !attachmentPreview)
                    }
                  >
                    <Send className="h-4 w-4" aria-hidden="true" />
                    {sending ? "Sending…" : "Send"}
                  </Button>
                  <details className="text-sm">
                    <summary className="min-h-11 cursor-pointer content-center text-text-secondary">Strategy options</summary>
                    <div className="flex flex-wrap gap-2 py-2">
                      <Button type="button" variant="outline"
                        disabled={!sessionLive || sending || Boolean(pending) || (!draft.trim() && !attachmentPreview) || Boolean(strategyError) || Boolean(!conversationId && strategyId && !strategyContext)}
                        onClick={() => void sendMessage(draft, "text", undefined, true)}>
                        Review strategy draft
                      </Button>
                      {!strategyContext ? <Select aria-label="Strategy setup type" value={setupType}
                        onChange={event => setSetupType(event.target.value)}>
                        {['htf_trend_pullback', 'liquidity_sweep_reversal', 'countertrend_short_build', 'passive_level_order', 'profit_protection', 'green_day_guard', 'mental_capital_guard', 'nested_continuation', 'sfp', 'manual_review'].map(type =>
                          <option key={type} value={type}>{type.replaceAll('_', ' ')}</option>)}
                      </Select> : null}
                    </div>
                  </details>
                  <Button
                    type="button"
                    variant="outline"
                    aria-expanded={importOpen}
                    aria-controls="agent-document-import"
                    onClick={() => setImportOpen((open) => !open)}
                  >
                    <FileUp className="h-4 w-4" aria-hidden="true" />
                    {importOpen ? "Close attachment" : "Attach document"}
                  </Button>
                </div>
                <VoiceConversationControls
                  conversationId={conversationId}
                  sessionKey={recoveryScope}
                  authenticated={Boolean(recoveryScope) && sessionLive}
                  contextKey={voiceContextKey}
                  disabled={(sending && sendOrigin !== "voice") ||
                    Boolean(pending && pending.origin !== "voice") ||
                    Boolean(!conversationId && strategyId && !strategyContext) ||
                    (messagesLoading && messages.length === 0) || (Boolean(attached) && !attachmentPreview)}
                  typedDraft={draft}
                  onTypedDraftChange={setDraft}
                  transport={voiceTransport}
                  ensureConversation={ensureConversation}
                  showReply={false}
                />
                {sendError ? (
                  <p
                    role="alert"
                    className="rounded-control border border-danger-border bg-danger-muted p-3 text-sm text-danger"
                  >
                    {sendError} Your message draft is kept until a successful
                    send.
                  </p>
                ) : null}
              </form>
              {importOpen && (
                <section
                  id="agent-document-import"
                  className="space-y-3 border-t border-border-subtle pt-3"
                  aria-label="Attachment"
                >
                  <Input
                    aria-label="Attach document"
                    type="file"
                    accept=".txt,.md,.docx,.pdf"
                    disabled={sending || previewBusy}
                    onChange={(event) => {
                      setAttached(event.target.files?.[0] ?? null);
                      setAttachmentPreview(null);
                      setSourceDocumentId(null);
                    }}
                  />
                  {attached && (
                    <>
                      <p className="text-sm">{attached.name}</p>
                      <Button
                        type="button"
                        variant="outline"
                        disabled={sending || previewBusy}
                        onClick={async () => {
                          setPreviewBusy(true);
                          setSendError(null);
                          try {
                            setAttachmentPreview(
                              await api.knowledge.previewFile(
                                attached,
                                attached.name.replace(/\.[^.]+$/, ""),
                                "general_note",
                              ),
                            );
                          } catch (error) {
                            setSendError(
                              error instanceof Error
                                ? error.message
                                : "Attachment preview failed",
                            );
                          } finally {
                            setPreviewBusy(false);
                          }
                        }}
                      >
                        Preview attachment
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() => {
                          setAttached(null);
                          setAttachmentPreview(null);
                          setSourceDocumentId(null);
                        }}
                      >
                        Remove attachment
                      </Button>
                    </>
                  )}
                  {attachmentPreview && (
                    <details open>
                      <summary className="text-sm">
                        Review extracted content
                      </summary>
                      <Textarea
                        aria-label="Attachment preview"
                        readOnly
                        value={attachmentPreview.extracted_text}
                        rows={8}
                      />
                      {attachmentPreview.warnings.map((w) => (
                        <p key={w} className="text-sm text-warning">
                          {w}
                        </p>
                      ))}
                    </details>
                  )}
                </section>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
