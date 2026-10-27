import { StrategyDraftReview } from "@/components/agent/StrategyDraftReview";
import { Button } from "@/components/ui/button";
import type { AgentStructuredProposal } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type Lifecycle = AgentStructuredProposal["status"];

/** Plain status per lifecycle, with a tone for the colour-coded pill. */
const STATUS: Record<Lifecycle, { text: string; tone: "warn" | "ok" | "muted" | "down" }> = {
  proposed: { text: "Agent proposed this. Waiting for your decision.", tone: "warn" },
  confirmed_unapplied: { text: "Confirmed, not applied yet.", tone: "warn" },
  applied: { text: "Confirmed and applied.", tone: "ok" },
  rejected: { text: "You rejected this.", tone: "muted" },
  refused: { text: "Refused by safety rules.", tone: "down" },
};

const TONE = {
  warn: "border-warning-border bg-warning-muted/30",
  ok: "border-success-border bg-success-muted/30",
  muted: "border-border-subtle bg-surface-1",
  down: "border-danger-border bg-danger-muted/30",
};

const KIND_NOTE: Partial<Record<AgentStructuredProposal["kind"], string>> = {
  propose_strategy: "Confirm records the request. The strategy stays a draft.",
  propose_journal_entry: "Confirm writes one journal entry.",
};

/** One Agent proposal: summary, plain status, and Confirm / Reject while it waits. */
export function ProposalCard({
  proposal,
  deciding,
  onDecide,
}: {
  proposal: AgentStructuredProposal;
  deciding: boolean;
  onDecide: (statement: "I confirm" | "I reject") => void;
}) {
  const status = STATUS[proposal.status] ?? { text: "Status unknown.", tone: "muted" as const };
  const note = KIND_NOTE[proposal.kind];
  return (
    <li
      className={cn("space-y-2 rounded-card border p-4", TONE[status.tone])}
      data-proposal-status={proposal.status}
      data-testid="agent-proposal-card"
    >
      <p className="text-sm font-medium text-text-primary">{proposal.summary}</p>
      <p className="text-caption text-text-secondary" data-testid="agent-proposal-status">
        {status.text}
      </p>
      <p className="text-caption text-text-muted" data-testid="agent-proposal-reply-note">
        The reply did not confirm this proposal. Sending a chat or voice message does not confirm it either.
      </p>
      {note && proposal.status === "proposed" ? <p className="text-caption text-text-muted">{note}</p> : null}
      {proposal.linked_strategy_proposal_id ? (
        <StrategyDraftReview
          key={proposal.linked_strategy_proposal_id}
          conversationId={proposal.conversation_id}
          proposalId={proposal.linked_strategy_proposal_id}
        />
      ) : null}
      {proposal.status === "proposed" ? (
        <div className="flex flex-wrap gap-2">
          <Button type="button" aria-label="Confirm proposal" onClick={() => onDecide("I confirm")} disabled={deciding}>
            Confirm
          </Button>
          <Button type="button" variant="outline" aria-label="Reject proposal" onClick={() => onDecide("I reject")} disabled={deciding}>
            Reject
          </Button>
        </div>
      ) : null}
    </li>
  );
}
