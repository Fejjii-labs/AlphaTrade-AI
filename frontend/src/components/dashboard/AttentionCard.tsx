"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { SectionHeader } from "@/components/ui/section-header";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { AttentionItem, AttentionQueue } from "@/lib/api/attention-types";
import { humanizeToken, stripDisplayIdentifiers } from "@/lib/format";

function latestSource(item: AttentionItem): number {
  return Math.max(...item.sources.map((source) => Date.parse(source.occurred_at)));
}

function isTelegramDeadLetter(item: AttentionItem): boolean {
  return item.category === "telegram_delivery_failure" && /\bdead[_ -]?letter\b/i.test(item.reason);
}

export function AttentionContent({
  queue,
  now,
  limit = 5,
  collapsible = false,
}: {
  queue: AttentionQueue;
  now: number;
  limit?: number;
  collapsible?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const items = queue.items.filter((item) =>
    item.expires_at == null || Date.parse(item.expires_at) > now,
  );
  if (collapsible) items.sort((a, b) => latestSource(b) - latestSource(a));
  const visible = collapsible && expanded
    ? items
    : (collapsible ? items.filter((item) => !isTelegramDeadLetter(item)) : items).slice(0, limit);
  return (
    <div className="space-y-3 text-sm" data-testid="attention-content">
      {visible.length === 0 ? (
        <p>{items.length ? "No recent alerts" : "No alerts"}</p>
      ) : (
        <ul className="space-y-3">
          {visible.map((item) => (
            <li key={item.item_id} className="break-words border-b border-border-subtle pb-3 last:border-0">
              <p className="font-medium">
                {humanizeToken(item.severity)} · {stripDisplayIdentifiers(item.title) || "Alert"}{item.symbol ? ` · ${item.symbol}` : ""}
              </p>
              <p className="mt-1 whitespace-pre-wrap text-text-secondary">{stripDisplayIdentifiers(item.reason)}</p>
            </li>
          ))}
        </ul>
      )}
      {visible.length < items.length ? <p className="text-xs text-text-muted">{visible.length} of {items.length}</p> : null}
      {collapsible && (expanded || visible.length < items.length) ? (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
          className="min-h-11 text-sm text-accent underline-offset-2 hover:underline"
        >
          {expanded ? "Show less" : "Show all"}
        </button>
      ) : null}
    </div>
  );
}

function validQueue(data: AttentionQueue | null | undefined): data is AttentionQueue {
  return data?.schema_version === "AttentionQueue/v1"
    && Array.isArray(data.items) && Array.isArray(data.limitations);
}

/** Compact alert count for the Dashboard strip; opens Settings > Alerts. */
export function AttentionBadge() {
  const loader = useCallback(() => api.dashboard.attention(), []);
  const { data, loading, error } = useAsyncData(loader, []);
  if (loading) return null;
  const now = Date.now();
  const items = !error && validQueue(data)
    ? data.items.filter((item) => item.expires_at == null || Date.parse(item.expires_at) > now)
    : null;
  const urgent = items?.some((item) => item.severity === "critical" || item.severity === "high");
  return (
    <Link href="/settings/alerts" data-testid="dashboard-strip-alerts" className="inline-flex">
      <Badge variant={items == null ? "warning" : items.length === 0 ? "muted" : urgent ? "danger" : "warning"}>
        {items == null
          ? "Alerts unavailable"
          : items.length === 0
            ? "No alerts"
            : `${items.length} alert${items.length === 1 ? "" : "s"}`}
      </Badge>
    </Link>
  );
}

export function AttentionCard({ limit, collapsible }: { limit?: number; collapsible?: boolean } = {}) {
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
  const valid = validQueue(data);
  return (
    <Card data-testid="dashboard-attention">
      <CardHeader className="gap-2 pb-2">
        <SectionHeader
          title="Recent alerts"
          actions={
            <button
              type="button"
              onClick={() => void reload()}
              disabled={loading}
              className="text-[length:var(--text-label)] text-accent underline-offset-2 hover:underline"
            >
              Refresh
            </button>
          }
        />
      </CardHeader>
      <CardContent className="card-inner pt-0">
        {loading ? <p role="status">Loading…</p> : error || !valid ? (
          <p role="alert">Alerts unavailable</p>
        ) : data ? <AttentionContent queue={data} now={Math.max(now, Date.now())} limit={limit} collapsible={collapsible} /> : null}
      </CardContent>
    </Card>
  );
}
