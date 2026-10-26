"use client";

import Link from "next/link";
import { useState } from "react";

import { ConfidenceBadge } from "@/components/ConfidenceBadge";
import { PaperOrderButton } from "@/components/ProposalDetailPanel";
import { RiskBadge } from "@/components/RiskBadge";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import type { ApprovalRequest, TradeProposal } from "@/lib/api/types";
import { formatDate, formatDecimal } from "@/lib/utils";

export function ApprovalDetailPanel({
  approval,
  proposal,
  busy,
  onApprove,
  onReject,
  onNeedsAnalysis,
  onModify,
  onRefresh,
}: {
  approval: ApprovalRequest;
  proposal: TradeProposal | null | undefined;
  busy?: boolean;
  onApprove?: (id: string) => void;
  onReject?: (id: string) => void;
  onNeedsAnalysis?: (id: string) => void;
  onModify?: (id: string, fields: Record<string, string>, reason?: string) => void;
  onRefresh?: () => void;
}) {
  const [sizeOverride, setSizeOverride] = useState("");
  const isPending = approval.status === "pending";

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Approval · {formatDate(approval.created_at)}</CardTitle>
          <StatusBadge label={approval.status.replaceAll("_", " ")} tone="pending" />
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="flex flex-wrap gap-2">
          <RiskBadge level={approval.risk_level} />
          <ConfidenceBadge value={approval.confidence} />
        </div>

        {approval.approval_reason ? (
          <p className="text-zinc-300">{approval.approval_reason}</p>
        ) : null}
        <p className="text-zinc-500">Created {formatDate(approval.created_at)}</p>

        <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3 space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">
            Linked proposal
          </h3>
          {proposal ? (
            <>
              <p className="text-zinc-200">
                {proposal.symbol} · {proposal.direction.toUpperCase()} @{" "}
                {formatDecimal(proposal.entry_price)}
              </p>
              <Link
                href={`/proposals?id=${proposal.id}`}
                className="text-emerald-400 hover:underline"
              >
                View proposal details
              </Link>
            </>
          ) : (
            <p className="text-zinc-500">Proposal {approval.proposal_id} not loaded.</p>
          )}
        </div>

        {approval.modified_fields && Object.keys(approval.modified_fields).length > 0 ? (
          <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-200">
            Modified fields: {JSON.stringify(approval.modified_fields)}
          </div>
        ) : null}

        {isPending && onApprove && onReject ? (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <Button disabled={busy} onClick={() => onApprove(approval.id)}>
                Approve for paper review
              </Button>
              <Button variant="destructive" disabled={busy} onClick={() => onReject(approval.id)}>
                Reject proposal
              </Button>
              {onNeedsAnalysis ? (
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => onNeedsAnalysis(approval.id)}
                >
                  Needs more analysis
                </Button>
              ) : null}
            </div>
            {onModify ? (
              <div className="space-y-2 rounded-lg border border-zinc-800 p-3">
                <Label htmlFor="modify-size">Modify position size (paper plan only)</Label>
                <Input
                  id="modify-size"
                  placeholder="e.g. 0.005"
                  value={sizeOverride}
                  onChange={(e) => setSizeOverride(e.target.value)}
                />
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy || !sizeOverride.trim()}
                  onClick={() =>
                    onModify(
                      approval.id,
                      { position_size: sizeOverride.trim() },
                      "Modified in UI",
                    )
                  }
                >
                  Submit modification
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}

        {proposal ? (
          <PaperOrderButton proposal={proposal} approval={approval} onSuccess={onRefresh} />
        ) : null}
      </CardContent>
    </Card>
  );
}
