"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import type { VoiceMeter } from "@/lib/voice/realtime-controller";

export type OrbState = "connecting" | "listening" | "thinking" | "speaking";

type Props = {
  /** Controller state; "speaking" is also inferred from live reply playback. */
  state: OrbState;
  /** Live audio level, read once per animation frame. */
  meter?: () => VoiceMeter;
  /** Short secondary line, e.g. time left. */
  detail?: string;
  onEnd(): void;
  /** Optional extra control, e.g. resuming muted GPT Live audio. */
  children?: ReactNode;
};

const LABELS: Record<OrbState, string> = {
  connecting: "Connecting…",
  listening: "Listening",
  thinking: "Thinking…",
  speaking: "Speaking",
};

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);
    const change = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener?.("change", change);
    return () => query.removeEventListener?.("change", change);
  }, []);
  return reduced;
}

/** Full-screen hands-free voice view: one animated circle, minimal text, End and Esc. */
export function VoiceOrb({ state, meter, detail, onEnd, children }: Props) {
  const orb = useRef<HTMLDivElement | null>(null);
  const end = useRef<HTMLButtonElement | null>(null);
  const onEndRef = useRef(onEnd);
  onEndRef.current = onEnd;
  const reduced = useReducedMotion();
  const [playing, setPlaying] = useState(false);
  const shown: OrbState = playing && state !== "connecting" ? "speaking" : state;

  useEffect(() => {
    end.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onEndRef.current(); }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);

  useEffect(() => {
    if (!meter || typeof requestAnimationFrame === "undefined") return;
    let frame = 0;
    let smooth = 0;
    let lastPlaying = false;
    const tick = () => {
      const { speaking, level } = meter();
      if (speaking !== lastPlaying) { lastPlaying = speaking; setPlaying(speaking); }
      // Speech RMS is small; scale it up and smooth it so the circle breathes, not jitters.
      smooth = smooth * 0.8 + Math.min(1, level * 4) * 0.2;
      if (orb.current) orb.current.style.setProperty("--orb-level", reduced ? "0" : smooth.toFixed(3));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [meter, reduced]);

  return (
    <div role="dialog" aria-modal="true" aria-label="Voice conversation"
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-10 bg-background/95 backdrop-blur-sm">
      <div className="flex h-72 w-72 items-center justify-center">
        <div ref={orb} data-testid="voice-orb" data-state={shown} data-reduced-motion={reduced ? "true" : undefined}
          aria-hidden="true"
          style={{
            transform: "scale(calc(1 + var(--orb-level, 0) * 0.35))",
            boxShadow: "0 0 calc(24px + var(--orb-level, 0) * 72px) calc(var(--orb-level, 0) * 18px) var(--color-accent-muted)",
          }}
          className={[
            "h-48 w-48 rounded-full bg-accent transition-[opacity,transform] duration-100 ease-out",
            shown === "thinking" ? "animate-pulse opacity-70 motion-reduce:animate-none" : "",
            shown === "connecting" ? "opacity-40" : "",
            shown === "listening" ? "opacity-90" : "",
          ].join(" ")}
        />
      </div>
      <div className="flex flex-col items-center gap-1 text-center">
        <p role="status" aria-live="polite" className="text-lg font-medium text-text-primary">{LABELS[shown]}</p>
        {detail ? <p className="text-sm text-text-muted">{detail}</p> : null}
      </div>
      <div className="flex items-center gap-3">
        {children}
        <Button ref={end} type="button" variant="secondary" className="h-12 min-w-32" onClick={() => onEndRef.current()}>
          End
        </Button>
      </div>
    </div>
  );
}
