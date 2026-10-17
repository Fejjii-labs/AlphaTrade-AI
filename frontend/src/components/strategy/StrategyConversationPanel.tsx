"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { ErrorState, LoadingState } from "@/components/states";
import { api } from "@/lib/api";
import type {
  ConversationMessageRecord,
  StrategyProposalRecord,
} from "@/lib/api/types";

type Props = {
  strategyId: string;
};

const COMPOSER_HINT =
  "Discuss this strategy. Confirm stores a draft. Compile and approve are separate. No Watcher, Telegram, or live activation.";

export function StrategyConversationPanel({ strategyId }: Props) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessageRecord[]>([]);
  const [proposal, setProposal] = useState<StrategyProposalRecord | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [compileStatus, setCompileStatus] = useState<string | null>(null);
  const [lifecycleState, setLifecycleState] = useState<string | null>(null);

  const loadThread = useCallback(
    async (id: string) => {
      const [messagePage, proposalPage] = await Promise.all([
        api.conversations.listMessages(id, { limit: 100 }),
        api.conversations.listProposals(id, { limit: 10 }),
      ]);
      setMessages(messagePage.items);
      const open =
        proposalPage.items.find((item) => item.status === "draft") ??
        proposalPage.items[0] ??
        null;
      setProposal(open);
    },
    [],
  );

  const bootstrap = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const listed = await api.conversations.list({ strategy_id: strategyId, limit: 1 });
      const existing = listed.items[0];
      if (existing) {
        setConversationId(existing.id);
        await loadThread(existing.id);
      } else {
        setConversationId(null);
        setMessages([]);
        setProposal(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Conversation failed to load");
    } finally {
      setLoading(false);
    }
  }, [loadThread, strategyId]);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  async function send() {
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await api.chat.message({
        message: text,
        conversation_id: conversationId ?? undefined,
        strategy_id: strategyId,
      });
      setConversationId(result.conversation_id);
      setDraft("");
      if (result.pending_proposal) {
        setProposal(result.pending_proposal);
      }
      await loadThread(result.conversation_id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Chat request failed");
    } finally {
      setBusy(false);
    }
  }

  async function confirmProposal() {
    if (!conversationId || !proposal || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      const confirmed = await api.conversations.confirmProposal(conversationId, proposal.id, {
        confirm: "I confirm",
        expected_content_hash: proposal.content_hash ?? "",
        expected_parent_version_id: proposal.parent_version_id ?? null,
        expected_target_strategy_id: proposal.target_strategy_id ?? null,
        expected_organization_id: proposal.organization_id,
        expected_user_id: proposal.user_id,
        expected_conversation_id: proposal.conversation_id,
      });
      setProposal(confirmed);
      await loadThread(conversationId);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Confirmation failed");
    } finally {
      setBusy(false);
    }
  }

  async function rejectProposal() {
    if (!conversationId || !proposal || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      const rejected = await api.conversations.rejectProposal(conversationId, proposal.id, {
        confirm: "I reject",
      });
      setProposal(rejected);
      await loadThread(conversationId);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Rejection failed");
    } finally {
      setBusy(false);
    }
  }

  async function compileVersion() {
    if (!proposal?.resulting_version_id || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      const compiled = await api.strategies.compileVersion(strategyId, proposal.resulting_version_id);
      setCompileStatus(compiled.status);
      if (conversationId) {
        await loadThread(conversationId);
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Compile failed");
    } finally {
      setBusy(false);
    }
  }

  async function approveVersion() {
    if (!proposal?.resulting_version_id || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      const approved = await api.strategies.approveVersion(strategyId, proposal.resulting_version_id, {
        confirm: "I confirm",
      });
      setLifecycleState(approved.new_state);
      if (conversationId) {
        await loadThread(conversationId);
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Approval failed");
    } finally {
      setBusy(false);
    }
  }

  const draftOpen = proposal?.status === "draft";
  const confirmedDraft = proposal?.status === "confirmed" && Boolean(proposal.resulting_version_id);

  return (
    <Card data-testid="strategy-conversation-panel">
      <CardHeader>
        <CardTitle className="text-base">Strategy conversation</CardTitle>
        <p className="text-sm text-zinc-400">{COMPOSER_HINT}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? <LoadingState label="Loading conversation…" /> : null}
        {error ? <ErrorState message={error} onRetry={() => void bootstrap()} /> : null}
        {actionError ? <ErrorState message={actionError} /> : null}

        <ol
          className="max-h-80 space-y-3 overflow-y-auto rounded-lg border border-zinc-800 p-3 text-sm"
          data-testid="strategy-conversation-thread"
        >
          {messages.length === 0 && !loading ? (
            <li className="text-zinc-500">No messages yet. Describe an idea to start a durable thread.</li>
          ) : null}
          {messages.map((item) => (
            <li
              key={item.id}
              data-testid={`strategy-conversation-message-${item.role}`}
              className="space-y-1"
            >
              <p className="text-xs uppercase tracking-wide text-zinc-500">{item.role}</p>
              <p className="whitespace-pre-wrap text-zinc-200">{item.content}</p>
            </li>
          ))}
        </ol>

        {proposal ? (
          <div
            className="space-y-2 rounded-lg border border-zinc-700 p-3"
            data-testid="strategy-conversation-proposal"
            data-proposal-status={proposal.status}
          >
            <p className="text-sm font-medium text-zinc-100">
              Proposal {proposal.status}
              {proposal.is_preview ? " · preview only" : ""}
            </p>
            {(proposal.challenge_notes ?? []).slice(0, 3).map((note) => (
              <p key={note} className="text-sm text-amber-300">
                Challenge: {note}
              </p>
            ))}
            {proposal.content_hash ? (
              <dl
                className="space-y-1 text-xs text-zinc-400"
                data-testid="strategy-conversation-confirmation-identity"
              >
                <div>proposal_id: {proposal.id}</div>
                <div>content_hash: {proposal.content_hash}</div>
                <div>target_strategy_id: {proposal.target_strategy_id ?? "none"}</div>
                <div>parent_version_id: {proposal.parent_version_id ?? "none"}</div>
                <div>conversation_id: {proposal.conversation_id}</div>
                <div>organization_id: {proposal.organization_id}</div>
                <div>user_id: {proposal.user_id}</div>
              </dl>
            ) : null}
            {(proposal.limitations ?? []).slice(0, 2).map((note) => (
              <p key={note} className="text-xs text-zinc-400">
                {note}
              </p>
            ))}
            {proposal.resulting_version_id ? (
              <p className="text-sm text-zinc-300" data-testid="strategy-conversation-version">
                Stored version: {proposal.resulting_version_id}
              </p>
            ) : null}
            {compileStatus ? (
              <p className="text-sm text-zinc-300" data-testid="strategy-conversation-compile-status">
                Compile: {compileStatus}
              </p>
            ) : null}
            {lifecycleState ? (
              <p className="text-sm text-zinc-300" data-testid="strategy-conversation-lifecycle">
                Lifecycle: {lifecycleState}
              </p>
            ) : null}
            {draftOpen ? (
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  disabled={busy || !proposal.content_hash}
                  onClick={() => void confirmProposal()}
                  data-testid="strategy-conversation-confirm"
                >
                  Confirm draft version
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => void rejectProposal()}
                  data-testid="strategy-conversation-reject"
                >
                  Reject proposal
                </Button>
              </div>
            ) : null}
            {confirmedDraft ? (
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  disabled={busy}
                  onClick={() => void compileVersion()}
                  data-testid="strategy-conversation-compile"
                >
                  Compile for review
                </Button>
                <Button
                  type="button"
                  disabled={busy || compileStatus !== "executable"}
                  onClick={() => void approveVersion()}
                  data-testid="strategy-conversation-approve"
                >
                  Approve compiled policy
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="space-y-2">
          <label htmlFor="strategy-conversation-composer" className="text-sm text-zinc-400">
            Message
          </label>
          <Textarea
            id="strategy-conversation-composer"
            data-testid="strategy-conversation-composer"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Explain, challenge, or propose a refinement…"
            disabled={busy}
          />
          <Button
            type="button"
            disabled={busy || !draft.trim()}
            onClick={() => void send()}
            data-testid="strategy-conversation-send"
          >
            {busy ? "Sending…" : "Send"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
