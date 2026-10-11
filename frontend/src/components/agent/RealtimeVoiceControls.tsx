"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { RealtimeVoiceController, type RealtimeSnapshot } from "@/lib/voice/realtime-controller";
import type { TurnBody } from "./turn-recovery";
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
};

export function RealtimeVoiceControls(props: Props) {
  const current = useRef(props);
  current.current = props;
  const controller = useRef<RealtimeVoiceController | null>(null);
  const [state, setState] = useState<RealtimeSnapshot>({ state: "idle", active: false, error: null, transcript: "" });
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
  return <div aria-label="Interactive voice" className="space-y-2">
    <div className="flex flex-wrap items-center gap-2">
      <Button type="button" variant="secondary"
        disabled={!state.active && (props.disabled || !props.authenticated)}
        onClick={() => state.active ? controller.current?.end() : void controller.current?.start()}>
        {state.active ? "End voice" : "Talk to Agent"}
      </Button>
      {state.active && <Button type="button" variant="secondary" onClick={() => controller.current?.interrupt()}>Interrupt</Button>}
      {state.active && <span role="status" className="text-sm text-text-muted">
        {state.state === "connecting" ? "Connecting…" : state.state === "thinking" ? "Thinking…" : state.state === "speaking" ? "Speaking" : "Listening"}
      </span>}
    </div>
    {state.transcript && <details className="text-sm text-text-muted"><summary className="min-h-11 cursor-pointer content-center">Heard request</summary><p className="mt-1 whitespace-pre-wrap">{state.transcript}</p></details>}
    {state.error && <p role="alert" className="text-sm text-danger">{state.error}</p>}
  </div>;
}
