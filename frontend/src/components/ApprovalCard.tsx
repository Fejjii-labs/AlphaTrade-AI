"use client";

import { ConfidenceBadge } from "@/components/ConfidenceBadge";
import { RiskBadge } from "@/components/RiskBadge";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ApprovalRequest } from "@/lib/api/types";
import { formatDate } from "@/lib/utils";

export function ApprovalCard({
  approval,
  onApprove,
  onReject,
  onNeedsAnalysis,
  busy,
}: {
  approval: ApprovalRequest;
  onApprove?: (id: string) => void;
  onReject?: (id: string) => void;
  onNeedsAnalysis?: (id: string) => void;
  busy?: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Approval · {formatDate(approval.created_at)}</CardTitle>
          <StatusBadge label={approval.status.replaceAll("_", " ")} tone="pending" />
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex flex-wrap gap-2">
          <RiskBadge level={approval.risk_level} />
          <ConfidenceBadge value={approval.confidence} />
        </div>
        <p className="text-zinc-400">Proposal: {approval.proposal_id}</p>
        {approval.approval_reason ? (
          <p className="text-zinc-300">{approval.approval_reason}</p>
        ) : null}
        <p className="text-zinc-500">Created {formatDate(approval.created_at)}</p>
        {approval.status === "pending" && onApprove && onReject ? (
          <div className="flex flex-wrap gap-2 pt-2">
            <Button disabled={busy} onClick={() => onApprove(approval.id)}>
              Approve for paper review
            </Button>
            <Button variant="destructive" disabled={busy} onClick={() => onReject(approval.id)}>
              Reject proposal
            </Button>
            {onNeedsAnalysis ? (
              <Button variant="secondary" disabled={busy} onClick={() => onNeedsAnalysis(approval.id)}>
                Needs more analysis
              </Button>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
