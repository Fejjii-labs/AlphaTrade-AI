"use client";

import { ShieldAlert, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useAppContext, useSafetyPosture } from "@/contexts/AppContext";

/**
 * Safety banner driven exclusively by backend /health truth (AT-017).
 *
 * Three states: posture unverified (health not loaded), paper-only not confirmed
 * (real trading enabled or non-paper mode), and paper mode confirmed. The banner
 * never claims "paper mode active" from build-time configuration.
 */
export function PaperModeBanner({ compact = false }: { compact?: boolean }) {
  const { executionMode, realTradingEnabled, providerMode, postureKnown } = useSafetyPosture();
  const { providers, health } = useAppContext();

  if (!postureKnown) {
    return (
      <div
        className="rounded-xl border border-zinc-700 bg-zinc-900/60 px-4 py-3"
        data-testid="paper-mode-banner-unverified"
      >
        <div className="flex items-center gap-2 text-zinc-300">
          <ShieldAlert className="h-4 w-4" />
          <span className="text-sm font-medium">Execution mode unverified</span>
        </div>
        <p className="mt-2 text-sm text-zinc-400">
          Waiting for backend health status. Trading posture is confirmed only from the live
          backend, never from build configuration.
        </p>
      </div>
    );
  }

  const paperConfirmed = executionMode === "paper" && realTradingEnabled === false;
  if (!paperConfirmed) {
    return (
      <div
        className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3"
        data-testid="paper-mode-banner-alert"
      >
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 text-red-300">
            <ShieldAlert className="h-4 w-4" />
            <span className="text-sm font-medium">Paper-only posture not confirmed</span>
          </div>
          <Badge variant="danger">{(executionMode ?? "unknown").toUpperCase()}</Badge>
          <Badge variant={realTradingEnabled ? "danger" : "success"}>
            Real trading {realTradingEnabled ? "enabled" : "disabled"}
          </Badge>
        </div>
        <p className="mt-2 text-sm text-red-200/80">
          Backend health does not report paper-only execution. Verify the deployment
          configuration before continuing.
        </p>
      </div>
    );
  }

  const marketProvider = providers?.providers.find((p) => p.kind === "market_data");
  const llmProvider = providers?.providers.find((p) => p.kind === "llm");
  const embeddingProvider = providers?.providers.find((p) => p.kind === "embeddings");
  const vectorProvider = providers?.providers.find((p) => p.kind === "vector");
  const redisProvider = providers?.providers.find((p) => p.name.toLowerCase().includes("redis"));

  return (
    <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 text-emerald-300">
          <ShieldCheck className="h-4 w-4" />
          <span className="text-sm font-medium">Paper mode active</span>
        </div>
        {!compact ? <Badge variant="success">{executionMode.toUpperCase()}</Badge> : null}
        <Badge variant="success">Real trading disabled</Badge>
        {!compact ? <Badge variant="info">Provider mode: {providerMode}</Badge> : null}
      </div>
      {!compact ? (
        <>
          <p className="mt-2 text-sm text-zinc-400">
            No real exchange orders are placed. Approvals gate paper execution only. Market data is
            read-only (Binance public API or mock fallback).
          </p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            {marketProvider ? (
              <Badge variant={marketProvider.is_mock ? "warning" : "info"}>
                Market: {marketProvider.name}
                {marketProvider.using_fallback ? " (fallback)" : ""}
              </Badge>
            ) : null}
            {llmProvider ? <Badge variant="muted">LLM: {llmProvider.name}</Badge> : null}
            {embeddingProvider ? (
              <Badge variant="muted">Embeddings: {embeddingProvider.name}</Badge>
            ) : null}
            {vectorProvider ? <Badge variant="muted">Qdrant: {vectorProvider.name}</Badge> : null}
            {redisProvider ? <Badge variant="muted">Redis: {redisProvider.health}</Badge> : null}
            {health ? <Badge variant="muted">API {health.status}</Badge> : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
