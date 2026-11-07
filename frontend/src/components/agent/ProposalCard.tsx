import { StrategyDraftReview } from "@/components/agent/StrategyDraftReview";
import { Badge } from "@/components/ui/badge";
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

type Kind = AgentStructuredProposal["kind"];

/**
 * What Confirm does, per kind (server: interactive_agent/proposals.py
 * confirm_proposal → application.py apply_confirmed_action). Every kind is
 * listed so a new backend kind fails typecheck instead of showing no note.
 */
const KIND_NOTE = {
  none: "Nothing to apply. Confirm changes no records.",
  propose_observation:
    "Confirm saves this observation as a note at most. No strategy, rule or order changes.",
  propose_hypothesis:
    "Confirm saves this hypothesis as a note at most. It is not a strategy or rule, and no order is placed.",
  propose_strategy: "Confirm records the request. The strategy stays a draft.",
  propose_rule: "Confirm records your decision only. Structured rules are not written.",
  propose_journal_entry: "Confirm writes one journal entry.",
  propose_trade_decision:
    "Confirm runs an advisory pre-trade check at most. No order is placed.",
  propose_lesson:
    "Confirm adds a lesson note to your journal when it can be attached. No order is placed.",
  propose_journal_append:
    "Confirm appends a note to an existing journal record. No order is placed.",
  propose_strategy_evidence:
    "Confirm links this evidence to the strategy's knowledge. The strategy version does not change.",
  propose_validation_request:
    "Confirm queues a backtest request when its inputs are complete. Nothing is promoted or traded.",
  propose_knowledge: "Confirm adds this to your knowledge base (quota applies). No order is placed.",
  propose_watcher_change:
    "Confirm changes the Watcher's monitored symbols or assignments. Monitoring only; no trading starts.",
  enable_real_trading: "Real trading cannot be enabled from the Agent. This request is refused.",
} satisfies Record<Kind, string>;

const PAPER_EXECUTION_NOTE =
  "Confirm rechecks risk and the quote, then opens this trade on the internal paper simulator if it is still valid. No exchange order is sent.";

/** Where a confirmed proposal can act. Only prepared paper plans touch a venue. */
function proposalVenue(proposal: AgentStructuredProposal): {
  label: string;
  variant: "paper" | "danger" | "muted";
} {
  if (proposal.kind === "enable_real_trading") return { label: "Real trading refused", variant: "danger" };
  if (proposal.kind === "propose_trade_decision" && proposal.payload?.paper_execution)
    return { label: "Paper · internal simulator", variant: "paper" };
  return { label: "No order", variant: "muted" };
}

/** One Agent proposal: summary, plain status, and Confirm / Reject while it waits. */
export function ProposalCard({
  proposal,
  deciding,
  onDecide,
  error,
}: {
  proposal: AgentStructuredProposal;
  deciding: boolean;
  onDecide: (statement: "I confirm" | "I reject") => void;
  /** Last confirm/reject failure for this proposal, shown on the card. */
  error?: string | null;
}) {
  const status = STATUS[proposal.status] ?? { text: "Status unknown.", tone: "muted" as const };
  const venue = proposalVenue(proposal);
  const note =
    proposal.kind === "propose_trade_decision" && proposal.payload?.paper_execution
      ? PAPER_EXECUTION_NOTE
      : (KIND_NOTE[proposal.kind] ?? "Confirm applies only what the summary above describes.");
  return (
    <li
      className={cn("space-y-2 rounded-card border p-4", TONE[status.tone])}
      data-proposal-status={proposal.status}
      data-testid="agent-proposal-card"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-sm font-medium text-text-primary">{proposal.summary}</p>
        <Badge variant={venue.variant} data-testid="agent-proposal-venue">
          {venue.label}
        </Badge>
      </div>
      <p className="text-caption text-text-secondary" data-testid="agent-proposal-status">
        {status.text}
      </p>
      <p className="text-caption text-text-muted" data-testid="agent-proposal-reply-note">
        The reply did not confirm this proposal. Sending a chat or voice message does not confirm it either.
      </p>
      {proposal.status === "proposed" ? (
        <p className="text-caption text-text-muted" data-testid="agent-proposal-kind-note">
          {note}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-caption text-danger" data-testid="agent-proposal-error">
          {error} The proposal is unchanged; you can retry.
        </p>
      ) : null}
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
