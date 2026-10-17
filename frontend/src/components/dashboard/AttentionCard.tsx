"use client";

import { useCallback, useEffect, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { AttentionQueue } from "@/lib/api/attention-types";
import { formatDateTime, humanizeToken } from "@/lib/format";

export function AttentionContent({ queue, now }: { queue: AttentionQueue; now: number }) {
  const items = queue.items.filter((item) =>
    item.expires_at == null || Date.parse(item.expires_at) > now,
  );
  return (
    <div className="space-y-3 text-sm" data-testid="attention-content">
      <p className="text-xs text-text-muted">
        Paper review only · As of {formatDateTime(queue.generated_at)}
      </p>
      {items.length === 0 ? (
        <p>No action available from the recorded attention sources.</p>
      ) : (
        <ul className="space-y-3">
          {items.slice(0, 5).map((item) => (
            <li key={item.item_id} className="break-words border-b border-border-subtle pb-3 last:border-0">
              <p className="font-medium">
                {humanizeToken(item.severity)} · {item.title}{item.symbol ? ` · ${item.symbol}` : ""}
              </p>
              <p className="mt-1 whitespace-pre-wrap text-text-secondary">{item.reason}</p>
              {item.recommended_next_action ? <p className="mt-1">{item.recommended_next_action}</p> : null}
              <details className="mt-2 text-xs text-text-muted">
                <summary className="cursor-pointer">Sources and review state</summary>
                <p>{humanizeToken(item.category)}</p>
                {item.strategy_id ? <p>Strategy: {item.strategy_id}</p> : null}
                {item.strategy_version_id ? <p>Strategy version: {item.strategy_version_id}</p> : null}
                <p>Acknowledgement: {humanizeToken(item.acknowledgement_state)}</p>
                <p>Expires: {item.expires_at ? formatDateTime(item.expires_at) : "On recorded state change"}</p>
                <ul className="mt-1 space-y-1">
                  {item.sources.map((source, index) => (
                    <li key={index}>{source.record_type} · {source.record_id} · {formatDateTime(source.occurred_at)}</li>
                  ))}
                </ul>
              </details>
            </li>
          ))}
        </ul>
      )}
      {items.length > 5 ? <p className="text-xs text-text-muted">Showing 5 of {items.length} recorded items.</p> : null}
      <details className="text-xs text-text-muted">
        <summary className="cursor-pointer">Coverage</summary>
        <ul className="mt-1 space-y-1">{queue.limitations.map((text) => <li key={text}>{text}</li>)}</ul>
      </details>
    </div>
  );
}

export function AttentionCard() {
  const loader = useCallback(() => api.dashboard.attention(), []);
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const [now, setNow] = useState(() => Date.now());
  // Remove expired market items even while the operator leaves this view open.
  useEffect(() => {
    const nextExpiry = Math.min(...(data?.items ?? []).flatMap((item) => {
      const expiry = item.expires_at ? Date.parse(item.expires_at) : NaN;
      return expiry > now ? [expiry] : [];
    }));
    if (!Number.isFinite(nextExpiry)) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.min(
      Math.max(0, nextExpiry - Date.now()), 2_147_483_647,
    ));
    return () => clearTimeout(timer);
  }, [data, now]);
  const valid = data?.schema_version === "AttentionQueue/v1"
    && Array.isArray(data.items) && Array.isArray(data.limitations);
  return (
    <Card data-testid="dashboard-attention">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>Attention</CardTitle>
        <button type="button" onClick={() => void reload()} disabled={loading} className="text-sm underline">Refresh attention</button>
      </CardHeader>
      <CardContent>
        {loading ? <p role="status">Loading attention…</p> : error || !valid ? (
          <p role="alert">Attention unavailable. Refresh to retry.</p>
        ) : data ? <AttentionContent queue={data} now={Math.max(now, Date.now())} /> : null}
      </CardContent>
    </Card>
  );
}
