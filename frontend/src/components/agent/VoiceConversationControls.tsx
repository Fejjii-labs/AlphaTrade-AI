"use client";

import { Mic, Square, Volume2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { createBrowserVoiceProvider } from "@/lib/voice/browser-voice-provider";
import { VoiceConversationController } from "@/lib/voice/conversation-controller";
import type { VoiceAgentTransport, VoiceConversationSnapshot } from "@/lib/voice/conversation-types";
import type { VoiceProviderFactory } from "@/lib/voice/types";
import { AGENT_TURN_TIMEOUT_MS } from "@/lib/agent-turn-budget";

const status: Record<VoiceConversationSnapshot["state"], string> = {
  idle: "", requesting: "Requesting microphone…", listening: "Listening",
  preparing: "Preparing transcript", waiting: "Waiting for Agent",
  speaking: "Speaking", paused: "Paused", error: "",
};

export interface VoiceConversationControlsProps {
  conversationId: string | null;
  /** Organization/user identity; changing it disposes all actor-scoped voice state. */
  sessionKey: string | null;
  authenticated: boolean;
  disabled?: boolean;
  typedDraft: string;
  onTypedDraftChange(text: string): void;
  transport: VoiceAgentTransport;
  createProvider?: VoiceProviderFactory;
  contextKey?: number;
  ensureConversation?(signal: AbortSignal): Promise<string | null>;
  showReply?: boolean;
}

/** Compact controls beside the typed composer, using its conversation and request pipeline. */
export function VoiceConversationControls({
  conversationId, sessionKey, authenticated, disabled = false, typedDraft, onTypedDraftChange,
  transport, createProvider = createBrowserVoiceProvider, contextKey = 0,
  ensureConversation, showReply = true,
}: VoiceConversationControlsProps) {
  const latest = useRef({ typedDraft, onTypedDraftChange });
  latest.current = { typedDraft, onTypedDraftChange };
  const controller = useRef<VoiceConversationController | null>(null);
  const creation = useRef<AbortController | null>(null);
  const previousContext = useRef(contextKey);
  const [creating, setCreating] = useState(false);
  const [creationError, setCreationError] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<VoiceConversationSnapshot | null>(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const instance = new VoiceConversationController({
      provider: createProvider(), transport,
      getTypedDraft: () => latest.current.typedDraft,
      setTypedDraft: text => latest.current.onTypedDraftChange(text),
      agentTimeoutMs: AGENT_TURN_TIMEOUT_MS,
    });
    controller.current = instance;
    const unsubscribe = instance.subscribe(() => setSnapshot(instance.getSnapshot()));
    const unsubscribeTransport = transport.subscribe?.(() => instance.reconcilePending());
    setSnapshot(instance.getSnapshot());
    const visibility = () => {
      if (document.hidden) creation.current?.abort();
      instance.setVisible(!document.hidden);
    };
    visibility();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      unsubscribe();
      unsubscribeTransport?.();
      creation.current?.abort();
      instance.dispose();
      controller.current = null;
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [createProvider, transport, sessionKey, authenticated]);

  useEffect(() => {
    if (previousContext.current === contextKey) return;
    previousContext.current = contextKey;
    creation.current?.abort();
    setCreating(false);
    setCreationError(null);
    controller.current?.end();
  }, [contextKey]);

  useEffect(() => {
    if (disabled || !authenticated) creation.current?.abort();
    controller.current?.setContext(conversationId, authenticated && Boolean(sessionKey) && !disabled);
  }, [conversationId, sessionKey, authenticated, disabled, createProvider, transport]);

  useEffect(() => {
    if (!snapshot?.startedAt) { setElapsed(0); return; }
    const started = snapshot.startedAt;
    const tick = () => setElapsed(Math.floor((Date.now() - started) / 1000));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [snapshot?.startedAt]);

  const blocked = !authenticated || !sessionKey || disabled || (!conversationId && !ensureConversation);
  const state = snapshot?.state ?? "idle";
  const recording = state === "requesting" || state === "listening";
  const speaking = state === "speaking";
  const run = (action: (instance: VoiceConversationController) => void) => {
    if (controller.current) action(controller.current);
  };
  const primaryLabel = speaking ? "Stop speaking" : recording ? "Stop recording"
    : state === "paused" ? "Resume voice" : snapshot?.mode === "conversation" ? "Start conversation" : "Record";
  const busy = creating || state === "waiting" || (state === "preparing" && !snapshot?.ready);
  async function start() {
    if (blocked || creating || !controller.current) return;
    const instance = controller.current;
    if (conversationId) { instance.start(); return; }
    if (!ensureConversation) return;
    const abort = new AbortController();
    creation.current = abort;
    setCreating(true);
    setCreationError(null);
    try {
      const id = await ensureConversation(abort.signal);
      if (id && !abort.signal.aborted && controller.current === instance && !document.hidden) {
        instance.setContext(id, true);
        instance.start();
      }
    } catch {
      if (!abort.signal.aborted) setCreationError("Conversation unavailable. Retry or type your message.");
    } finally {
      if (creation.current === abort) { creation.current = null; setCreating(false); }
    }
  }

  return (
    <section aria-label="Voice conversation" className="min-w-0 space-y-2" data-state={state}>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant={recording || speaking ? "warning" : "outline"}
          className="min-h-11" disabled={speaking || recording ? false : blocked || !snapshot?.capabilities.input || busy || Boolean(snapshot?.ready || snapshot?.unresolved)}
          onClick={() => speaking || recording ? run(instance => speaking ? instance.stopSpeaking() : instance.stopRecording()) : void start()}>
          {speaking ? <Volume2 className="h-4 w-4" aria-hidden="true" />
            : recording ? <Square className="h-4 w-4" aria-hidden="true" />
              : <Mic className="h-4 w-4" aria-hidden="true" />}
          {creating ? "Preparing conversation…" : primaryLabel}
        </Button>
        {(snapshot?.sessionActive || snapshot?.unresolved) && (
          <Button type="button" variant="ghost" className="min-h-11" onClick={() => run(instance => instance.end())}>
            End conversation
          </Button>
        )}
        {(creating || recording || state === "preparing" || snapshot?.ready || (snapshot?.transcript && !snapshot.unresolved)) && (
          <Button type="button" variant="ghost" className="min-h-11" onClick={() => { creation.current?.abort(); run(instance => instance.cancelTranscript()); }}>
            Cancel
          </Button>
        )}
        <label className="flex min-h-11 items-center gap-2 text-xs text-text-secondary">
          <input type="checkbox" checked={snapshot?.mode === "conversation"} disabled={blocked || busy || Boolean(snapshot?.unresolved)}
            onChange={event => run(instance => instance.setMode(event.target.checked ? "conversation" : "review"))} />
          Conversation mode
        </label>
        <label className="flex min-h-11 items-center gap-2 text-xs text-text-secondary">
          <input type="checkbox" checked={snapshot?.playback ?? true} disabled={blocked || !snapshot?.capabilities.output}
            onChange={event => run(instance => instance.setPlayback(event.target.checked))} />
          Read replies
        </label>
      </div>
      {(status[state] || snapshot?.ready) && (
        <p role="status" aria-live="polite" className="text-xs text-text-secondary">
          {snapshot?.ready ? "Review transcript" : status[state]}
          {snapshot?.startedAt ? ` · ${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}` : ""}
        </p>
      )}
      {snapshot && (snapshot.ready || snapshot.transcript) && (
        <div className="min-w-0 space-y-2">
          <Textarea aria-label="Voice transcript" rows={2} className="min-h-[64px]"
            value={snapshot.transcript} disabled={blocked || !snapshot.ready || Boolean(snapshot.unresolved)}
            onChange={event => run(instance => instance.editTranscript(event.target.value))} />
          {snapshot.ready && (
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" className="min-h-11" disabled={blocked || !snapshot.transcript.trim()}
                onClick={() => run(instance => instance.send())}>Send transcript</Button>
              <Button type="button" variant="ghost" className="min-h-11" disabled={blocked || !snapshot.transcript.trim()}
                onClick={() => run(instance => instance.applyTranscript("append"))}>Append to draft</Button>
              {typedDraft && <Button type="button" variant="ghost" className="min-h-11" disabled={blocked || !snapshot.transcript.trim()}
                onClick={() => run(instance => instance.applyTranscript("replace"))}>Replace draft</Button>}
            </div>
          )}
        </div>
      )}
      {snapshot?.unresolved && state !== "waiting" && (
        <Button type="button" variant="outline" className="min-h-11" disabled={blocked}
          onClick={() => run(instance => instance.recover())}>Recover original request</Button>
      )}
      {showReply && snapshot?.reply && <p className="break-words text-sm text-text-secondary" aria-label="Spoken Agent reply">{snapshot.reply}</p>}
      {creationError && <p role="alert" className="text-sm text-danger">{creationError}</p>}
      {snapshot?.error && <p role="alert" className="text-sm text-danger">{snapshot.error.message}</p>}
      {snapshot && !snapshot.capabilities.input && <p className="text-xs text-text-secondary">Voice input unavailable. Type your message.</p>}
      {snapshot && !snapshot.capabilities.output && <p className="text-xs text-text-secondary">Read replies as text.</p>}
      <details className="text-xs text-text-secondary">
        <summary className="min-h-11 cursor-pointer content-center">Voice details</summary>
        <p>Conversation mode sends completed utterances when the typed draft is empty. Browser speech may use a browser-managed service. Use Stop speaking to interrupt.</p>
        {snapshot?.error?.diagnostic && <p className="break-words">{snapshot.error.diagnostic.message}{snapshot.error.diagnostic.sourceCode ? ` (${snapshot.error.diagnostic.sourceCode})` : ""}</p>}
      </details>
    </section>
  );
}
