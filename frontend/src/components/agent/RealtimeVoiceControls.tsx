"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { RealtimeVoiceController, type RealtimeSnapshot } from "@/lib/voice/realtime-controller";
import { formatCountdown } from "@/lib/voice/voice-reasons";
import type { TurnBody } from "./turn-recovery";
import { VoiceOrb, type OrbState } from "./VoiceOrb";
import type { AgentTurnResult } from "@/lib/api/types";

type Props = {
  authenticated: boolean;
  sessionKey: string | null;
  contextKey: string | number;
  conversationId: string | null;
  disabled: boolean;
  symbol?: string;
  strategyId?: string;
  ensureConversation(signal: AbortSignal): Promise<string | null>;
  onPending(key: string, body: TurnBody): boolean;
  onResult(key: string, result: AgentTurnResult): boolean;
  onActive?(active: boolean): void;
  /** Live voice availability from `/agent/voice/config`: true, false, or null when unknown. */
  onAvailability?(available: boolean | null): void;
};

type Availability = { available: boolean | null; reason: string | null };

/** Reads voice availability once per session so the mic can hide before a click. */
function useVoiceAvailability(sessionKey: string | null, authenticated: boolean): Availability {
  const [availability, setAvailability] = useState<Availability>({ available: null, reason: null });
  useEffect(() => {
    if (!authenticated) return;
    const abort = new AbortController();
    (async () => {
      try {
        const config = await api.agent.voiceConfig({ signal: abort.signal });
        if (!abort.signal.aborted) setAvailability({ available: config.available, reason: config.reason ?? null });
      } catch {
        // Unknown: keep today's behaviour (the button explains on click).
        if (!abort.signal.aborted) setAvailability({ available: null, reason: null });
      }
    })();
    return () => abort.abort();
  }, [sessionKey, authenticated]);
  return availability;
}

function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return now;
}

export function RealtimeVoiceControls(props: Props) {
  const current = useRef(props);
  current.current = props;
  const controller = useRef<RealtimeVoiceController | null>(null);
  const [state, setState] = useState<RealtimeSnapshot>({ state: "idle", active: false, error: null, transcript: "" });
  const availability = useVoiceAvailability(props.sessionKey, props.authenticated);
  useEffect(() => { current.current.onAvailability?.(availability.available); }, [availability.available]);
  const now = useNow(state.active && state.endsAt != null);
  useEffect(() => {
    const voice = new RealtimeVoiceController({
      context: { symbol: current.current.symbol, strategy_id: current.current.strategyId },
      ensureConversation: signal => current.current.ensureConversation(signal),
      pending: (key, body) => current.current.onPending(key, body),
      result: (key, result) => current.current.onResult(key, result),
      changed: snapshot => { setState(snapshot); current.current.onActive?.(snapshot.active); },
    });
    controller.current = voice;
    const visibility = () => { if (document.hidden) voice.end(); };
    document.addEventListener("visibilitychange", visibility);
    return () => {
      voice.dispose(); controller.current = null;
      current.current.onActive?.(false);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [props.sessionKey, props.contextKey, props.strategyId, props.symbol]);
  useEffect(() => {
    controller.current?.setConversation(props.conversationId);
    if (!props.authenticated) controller.current?.end();
  }, [props.authenticated, props.conversationId]);
  const meter = useCallback(() => controller.current?.meter() ?? { speaking: false, level: 0 }, []);
  const orbState: OrbState = state.state === "connecting" || state.state === "thinking" || state.state === "speaking"
    ? state.state : "listening";
  const detail = [
    state.provider === "gpt_live" ? "GPT Live" : null,
    state.outputMuted ? "Audio muted" : null,
    state.endsAt != null ? `${formatCountdown(state.endsAt - now)} left` : null,
    state.turnsLeft != null ? `${state.turnsLeft} ${state.turnsLeft === 1 ? "turn" : "turns"} left` : null,
  ].filter(Boolean).join(" · ");
  // One mic: when live voice is known to be off, the composer keeps browser dictation only.
  if (availability.available === false && !state.active) return null;
  return (
    <>
      <Button type="button" variant="secondary" className="h-11"
        disabled={state.active || props.disabled || !props.authenticated}
        onClick={() => void controller.current?.start()}>
        Talk to Agent
      </Button>
      {state.active && (
        <VoiceOrb state={orbState} meter={meter} detail={detail || undefined} onEnd={() => controller.current?.end()}>
          {state.outputMuted ? (
            <Button type="button" variant="secondary" className="h-12" onClick={() => controller.current?.resume()}>Resume audio</Button>
          ) : null}
        </VoiceOrb>
      )}
      {state.transcript ? (
        <details className="basis-full text-sm text-text-muted">
          <summary className="flex h-10 cursor-pointer items-center">Heard request</summary>
          <p className="mt-1 whitespace-pre-wrap">{state.transcript}</p>
        </details>
      ) : null}
      {(state.inputCaption || state.replyCaption) ? (
        <details className="basis-full text-sm text-text-muted">
          <summary className="flex h-10 cursor-pointer items-center">Live captions</summary>
          {state.inputCaption ? <p aria-label="Your speech caption" className="mt-1 whitespace-pre-wrap">{state.inputCaption}</p> : null}
          {state.replyCaption ? <p aria-label="Agent speech caption" className="mt-1 whitespace-pre-wrap">{state.replyCaption}</p> : null}
        </details>
      ) : null}
      {state.error ? (
        <p role="alert" data-limit={state.limit ? "true" : undefined}
          className={state.limit ? "basis-full text-sm text-warning" : "basis-full text-sm text-text-secondary"}>
          {state.limit ? <span className="font-medium">Limit reached · </span> : null}
          {state.error}
        </p>
      ) : null}
    </>
  );
}
