"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { AgentMessageContent } from "@/components/agent/AgentMessageContent";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { formatDateTime, formatMoney, formatQuantity, humanizeToken } from "@/lib/format";
import type { AgentTurnResult } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import { manualDemo } from "@/lib/api/manual-demo";
import { friendlyError } from "@/lib/api/client";

export function ManualDemoDetail({ commandId }: { commandId: string }) {
  const load = useCallback(() => manualDemo.detail(commandId), [commandId]);
  const { data, loading, error, reload } = useAsyncData(load, [commandId]);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [answer, setAnswer] = useState<AgentTurnResult | null>(null);
  const [conversation, setConversation] = useState<string | null>(null);
  async function act(operation: () => Promise<unknown>) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setFailure(null);
    try {
      await operation();
    } catch (err) {
      setFailure(friendlyError(err, "Request failed"));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  const evidence = data?.evidence;
  return (
    <main data-testid="manual-demo-detail" className="space-y-4 [overflow-wrap:anywhere]">
      <PageHeader title="Manual BloFin demo attempt" description="Recorded evidence for this exact attempt" />
      <Link className="underline" href="/settings">
        Return to manual demo controls and history
      </Link>
      {loading && <p>Loading stored attempt…</p>}
      {error && (
        <div role="alert">
          <p>{error}</p>
          <Button onClick={() => void reload()}>Retry stored detail</Button>
        </div>
      )}
      {data && evidence && (
        <>
          <Card>
            <CardHeader><CardTitle>{data.symbol} · {data.side === "BUY" ? "Long" : "Short"} · BloFin demo</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-text-secondary">{data.account_name} · Manual connectivity test · {humanizeToken(evidence.execution_status ?? evidence.status)}</p>
              <p className="text-sm">{formatQuantity(data.requested_contracts, { maximumFractionDigits: 8 })} requested contracts = {formatQuantity(data.base_quantity, { maximumFractionDigits: 8 })} BTC</p>
              {data.blocked_reason ? <p role="status" className="text-sm text-warning">Blocked before submission: {data.blocked_reason.replaceAll("_", " ")}</p> : null}
              <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                {[
                  ["Filled contracts", formatQuantity(evidence.filled_quantity, { maximumFractionDigits: 8 })],
                  ["Entry", `${formatMoney(evidence.average_fill_price)} USDT`],
                  ["Entry fees", `${formatMoney(evidence.entry_fees)} USDT`],
                  ["Exit contracts", formatQuantity(evidence.exit_fills?.length ? evidence.exit_quantity : null, { maximumFractionDigits: 8 })],
                  ["Exit", `${formatMoney(evidence.exit_price)} USDT`],
                  ["Exit fees", `${formatMoney(evidence.exit_fees)} USDT`],
                  ["Gross trading PnL", `${formatMoney(evidence.gross_pnl)} USDT`],
                  ["Funding", `${formatMoney(evidence.funding)} USDT`],
                  ["Net PnL", `${formatMoney(evidence.net_pnl)} USDT`],
                ].map(([label, value]) => <div key={label}><dt className="text-xs text-text-secondary">{label}</dt><dd className="mt-1 text-sm tabular-nums">{value}</dd></div>)}
              </dl>
              <p className="text-sm">Trade lifecycle: {humanizeToken(evidence.position_status ?? "unknown")} · Current account: {humanizeToken(evidence.account_status ?? "unknown")}</p>
              <p className="text-xs text-text-secondary">Evidence observed {formatDateTime(evidence.observed_at)} · {humanizeToken(evidence.reconciliation_freshness ?? "never_observed")}</p>
              {evidence.missing_evidence[0] ? <p role="status" className="text-sm text-warning">{evidence.missing_evidence[0]}</p> : null}
              <p className="text-xs text-text-secondary">Refresh this same attempt to reconcile evidence. Do not resubmit.</p>
            </CardContent>
          </Card>
          <div className="flex flex-wrap items-center gap-3">
          {evidence.can_reconcile && (
            <Button
              disabled={busy || loading}
              onClick={() =>
                void act(async () => {
                  await manualDemo.reconcile(commandId);
                  setConfirmed(false);
                  await reload();
                })
              }
            >
              Refresh native evidence for this attempt
            </Button>
          )}
          {evidence.can_resolve && (
            <div className="space-y-2">
              <label>
                <input
                  type="checkbox"
                  checked={confirmed}
                  disabled={busy}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />{" "}
                Resolve only this attempt’s account reservation using fresh
                terminal evidence. Keep global safety and unrelated holds.
              </label>
              <Button
                disabled={busy || !confirmed}
                onClick={() =>
                  void act(async () => {
                    await manualDemo.resolve(commandId);
                    setConfirmed(false);
                    await reload();
                  })
                }
              >
                Resolve verified lifecycle
              </Button>
            </div>
          )}
          <Button
            disabled={busy}
            onClick={() =>
              void act(async () => {
                const result = await api.agent.turn({
                  message: `Explain manual BloFin demo command ${commandId}`,
                  action: {
                    name: "paper_trade.read_recorded",
                    arguments: {
                      command_id: commandId,
                      execution_venue: "BLOFIN_DEMO",
                      trade_origin: "manual_demo_test",
                    },
                  },
                });
                setAnswer(result);
                setConversation(result.conversation_id);
              })
            }
          >
            Ask Agent about this exact attempt
          </Button>
          </div>
          {answer && (
            <section aria-label="Agent explanation">
              <AgentMessageContent message={{ role: "assistant", content: answer.reply, payload: { interactive_agent: { recorded_evidence: answer.recorded_evidence, full_reply: answer.full_reply, sources: answer.connections } } }} />
              {conversation && (
                <Button
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const result = await api.agent.turn({
                        message: "Explain that trade",
                        conversation_id: conversation,
                      });
                      setAnswer(result);
                    })
                  }
                >
                  Explain that trade
                </Button>
              )}
            </section>
          )}
          {evidence.journal_trade_id && (
            <Link
              className="underline"
              href={`/journal?trade_id=${encodeURIComponent(evidence.journal_trade_id)}`}
            >
              Open this attempt’s Journal projection
            </Link>
          )}
          <details className="rounded-control border border-border-subtle p-4 text-sm">
            <summary className="cursor-pointer">Stored identities and history</summary>
            <div className="mt-3 space-y-2">
            <p>Attempt recorded: {formatDateTime(data.attempted_at)} · submission started: {data.submitted_at ? formatDateTime(data.submitted_at) : "Not recorded"}</p>
            <p>Account: {data.account_name} ({data.account_id})</p>
            <p>Remaining entry quantity: {formatQuantity(evidence.remaining_quantity, { maximumFractionDigits: 8 })} contracts</p>
            <p>Planned stop {formatMoney(data.stop)} · target {formatMoney(data.target)} USDT</p>
            <p>Historically configured protection: {humanizeToken(evidence.historical_protection)}</p>
            <p>Active protection: {evidence.position_status === "closed_verified" ? "Not applicable to verified closed exposure" : humanizeToken(evidence.protection)}</p>
            <p>Triggered protection: {humanizeToken(evidence.triggered_protection)}</p>
            {evidence.protection_history?.map((item) => <p key={item.tpsl_id}>Native protection history: {item.tpsl_id} · {item.state}. A canceled protection record does not establish a position exit.</p>)}
            {evidence.exit_fills?.map((fill) => <p key={fill.identity}>Exit fill {fill.identity}: {formatQuantity(fill.quantity, { maximumFractionDigits: 8 })} contracts at {formatMoney(fill.price)} USDT · {formatDateTime(fill.occurred_at)} · native fee {formatMoney(fill.fee)} USDT</p>)}
            <p>Fees use positive costs and negative rebates. Unknown funding is not zero.</p>
            <p>Venue reported fill PnL: {formatMoney(evidence.venue_reported_fill_pnl)} USDT. Funding and net outcome require separate evidence.</p>
            <p>Account recovery: {humanizeToken(evidence.recovery_status)}. {evidence.recovery_reason}</p>
            <p>This attempt’s reservation: {humanizeToken(evidence.reservation_status)}</p>
            {evidence.account_claim_command_ids?.length ? <p>Unresolved manual account claims: {evidence.account_claim_command_ids.map((id) => <Link key={id} className="mr-2 underline" href={`/execution/manual-demo/${encodeURIComponent(id)}`}>{id}</Link>)}</p> : null}
            {[...(evidence.reconciliation_diagnostics ?? []), ...(evidence.protection_diagnostics ?? [])].map((diagnostic, index) => <p key={`${diagnostic.stage}-${index}`}>{diagnostic.stage}: {diagnostic.reason_code} · {diagnostic.endpoint_name}{diagnostic.field_name ? ` · field ${diagnostic.field_name}` : ""}{diagnostic.http_status ? ` · HTTP ${diagnostic.http_status}` : ""}{diagnostic.venue_error_code ? ` · venue ${diagnostic.venue_error_code}` : ""}</p>)}
            {evidence.missing_evidence.map((note) => <p key={note}>{note}</p>)}
            <p>Command: {commandId}</p>
            <p>Native order: {evidence.venue_order_id ?? "Unverified"}</p>
            <p>
              Client order: {evidence.client_order_id || "No submission effect"}
            </p>
            <p>
              Protection:{" "}
              {evidence.protection_order_ids?.join(", ") || "Unverified"}
            </p>
            <p>
              Immutable plan: {evidence.revision_id} · hash {data.content_hash}
            </p>
            <p>Journal: {evidence.journal_trade_id ?? "No fill projection"}</p>
            </div>
          </details>
        </>
      )}
      {failure && <p role="alert">{failure}</p>}
    </main>
  );
}
