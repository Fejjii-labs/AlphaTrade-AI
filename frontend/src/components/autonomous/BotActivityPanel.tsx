"use client";

import Link from "next/link";
import { useCallback } from "react";

import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { BotAction } from "@/lib/api/types";
import { botReasonSentence } from "@/lib/bot-reasons";
import { formatDateTime } from "@/lib/format";

function market(action: BotAction): string {
  const side = action.direction ? (action.direction.toLowerCase() === "short" ? "short" : "long") : "";
  return [action.symbol, side].filter(Boolean).join(" ");
}

/** One plain sentence per bot decision: "Opened BTCUSDT long", "Skipped ETHUSDT short: Daily loss limit reached." */
export function actionSentence(action: BotAction): string {
  const what = market(action);
  if (action.outcome === "entered") return what ? `Opened ${what}` : "Opened a trade";
  const verb = action.outcome === "rejected" ? "Rejected" : "Skipped";
  const why = botReasonSentence(action.reason);
  return `${verb}${what ? ` ${what}` : ""}${why ? `: ${why}` : ""}`;
}

/** The bot's last five decisions in words. */
export function BotActivityPanel() {
  const loader = useCallback(() => api.autonomous.activity(), []);
  const { data, loading, error } = useAsyncData(loader, []);
  if (loading) return <p className="text-sm text-text-muted">Loading…</p>;
  if (error || !data) return <p className="text-sm text-text-muted">Bot activity unavailable.</p>;
  const actions = data.recent_actions.slice(0, 5);
  if (!actions.length) return <p className="text-sm text-text-muted">No decisions yet.</p>;
  return (
    <ul className="divide-y divide-border-subtle text-sm" data-testid="bot-activity">
      {actions.map((action, index) => (
        <li key={`${action.at}-${index}`} className="grid min-h-11 grid-cols-[auto_1fr_auto] items-center gap-x-3">
          <span className="text-xs text-text-muted">{formatDateTime(action.at)}</span>
          <span>{actionSentence(action)}</span>
          {action.journal_trade_id ? (
            <Link className="text-accent" href={`/journal?trade_id=${encodeURIComponent(action.journal_trade_id)}`}>
              Trade
            </Link>
          ) : (
            <span />
          )}
        </li>
      ))}
    </ul>
  );
}
