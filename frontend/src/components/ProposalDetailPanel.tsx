"use client";

import { useState } from "react";

import { LossAcceptancePanel } from "@/components/strategy/LossAcceptancePanel";
import { RiskBadge } from "@/components/RiskBadge";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/states";
import { api } from "@/lib/api";
import type { ApprovalRequest, TradeProposal } from "@/lib/api/types";
import {
  canExecutePaperOrder,
  newIdempotencyKey,
  paperSideForDirection,
} from "@/lib/workflow";
import { formatDecimal } from "@/lib/utils";

export function PaperOrderButton({
  proposal,
  approval,
  onSuccess,
}: {
  proposal: TradeProposal;
  approval: ApprovalRequest | null | undefined;
  onSuccess?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const eligibility = canExecutePaperOrder(proposal, approval);

  async function placePaperOrder() {
    if (!approval || !eligibility.allowed) return;
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      await api.execution.paperOrder({
        proposal_id: proposal.id,
        approval_id: approval.id,
        symbol: proposal.symbol,
        side: paperSideForDirection(proposal.direction),
        type: "market",
        size: proposal.position_size,
        idempotency_key: newIdempotencyKey(),
      });
      setSuccess("Paper order filled. No real exchange order was placed.");
      onSuccess?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Paper order failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <Button disabled={!eligibility.allowed || busy} onClick={() => void placePaperOrder()}>
        {busy ? "Creating paper order…" : "Create paper order (simulated)"}
      </Button>
      {!eligibility.allowed && eligibility.reason ? (
        <p className="text-xs text-amber-300">{eligibility.reason}</p>
      ) : (
        <p className="text-xs text-zinc-500">
          Paper-only simulation. Real trading remains disabled.
        </p>
      )}
      {error ? <ErrorState message={error} /> : null}
      {success ? <p className="text-sm text-emerald-300">{success}</p> : null}
    </div>
  );
}

export function ProposalDetailPanel({
  proposal,
  approval,
  onRefresh,
}: {
  proposal: TradeProposal;
  approval: ApprovalRequest | null | undefined;
  onRefresh?: () => void;
}) {
  const riskBlocked = proposal.risk_result?.action === "block";

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>
            {proposal.symbol} · {proposal.direction.toUpperCase()}
          </CardTitle>
          <StatusBadge label={proposal.status.replaceAll("_", " ")} tone="pending" />
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm text-zinc-300">
        <div className="flex flex-wrap gap-2">
          <RiskBadge level={proposal.risk_level} />
          {proposal.approval_required ? (
            <StatusBadge label="Approval required" tone="warn" />
          ) : null}
          {riskBlocked ? <StatusBadge label="Risk blocked" tone="danger" /> : null}
        </div>

        <p className="text-zinc-400">{proposal.rationale}</p>

        <div className="grid gap-2 text-zinc-400 sm:grid-cols-2">
          <span>Entry: {formatDecimal(proposal.entry_price)}</span>
          <span>Stop: {formatDecimal(proposal.exit.stop_loss)}</span>
          <span>Size: {formatDecimal(proposal.position_size)}</span>
          <span>Strategy: {proposal.strategy_id}</span>
        </div>

        {proposal.loss_acceptance_required || proposal.planned_loss_amount ? (
          <LossAcceptancePanel
            sizing={{
              entry_price: proposal.entry_price,
              invalidation_level: proposal.exit.stop_loss,
              stop_loss_distance: "0",
              account_balance: "10000",
              max_risk_percent: "1",
              maximum_acceptable_loss: proposal.planned_loss_amount ?? "0",
              notional_position_size: proposal.position_size,
              leverage_limit: proposal.leverage,
              leverage_recommendation: proposal.leverage,
              confidence_score: proposal.confidence * 100,
              confidence_adjusted_size: proposal.position_size,
              worst_case_scenario: "Worst case equals planned loss if stop is hit.",
              final_recommendation: "normal_size",
              planned_loss_amount: proposal.planned_loss_amount ?? "0",
            }}
            proposalId={proposal.id}
            onAccepted={() => onRefresh?.()}
          />
        ) : null}

        <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3">
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
            Linked approval
          </h3>
          {approval ? (
            <div className="space-y-1">
              <p>
                Status:{" "}
                <span className="text-zinc-200">{approval.status.replaceAll("_", " ")}</span>
              </p>
              <p className="text-xs text-zinc-500">ID: {approval.id}</p>
              {approval.approval_reason ? (
                <p className="text-zinc-400">{approval.approval_reason}</p>
              ) : null}
            </div>
          ) : (
            <p className="text-zinc-500">No approval record linked yet.</p>
          )}
        </div>

        <PaperOrderButton proposal={proposal} approval={approval} onSuccess={onRefresh} />
      </CardContent>
    </Card>
  );
}
