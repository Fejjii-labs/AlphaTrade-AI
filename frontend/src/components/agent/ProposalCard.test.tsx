import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AgentStructuredProposal } from "@/lib/api/types";

import { ProposalCard } from "./ProposalCard";

vi.mock("@/components/agent/StrategyDraftReview", () => ({
  StrategyDraftReview: () => <div data-testid="agent-strategy-draft" />,
}));

function proposal(overrides: Partial<AgentStructuredProposal> = {}): AgentStructuredProposal {
  return {
    proposal_id: "7f0f8a52-7a6c-4b2b-9c55-1f3a5b2d9e10",
    conversation_id: "11111111-1111-4111-8111-111111111111",
    content_hash: "a".repeat(64),
    kind: "propose_journal_entry",
    status: "proposed",
    summary: "Log the BTC long as a lesson.",
    ...overrides,
  } as AgentStructuredProposal;
}

describe("ProposalCard", () => {
  it("shows plain status, the kind note and decision buttons while proposed", () => {
    const onDecide = vi.fn();
    render(<ProposalCard proposal={proposal()} deciding={false} onDecide={onDecide} />);

    expect(screen.getByTestId("agent-proposal-status")).toHaveTextContent("Waiting for your decision");
    expect(screen.getByText("Confirm writes one journal entry.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm proposal" }));
    fireEvent.click(screen.getByRole("button", { name: "Reject proposal" }));
    expect(onDecide.mock.calls).toEqual([["I confirm"], ["I reject"]]);
  });

  it.each([
    ["applied", "Confirmed and applied.", "border-success-border"],
    ["rejected", "You rejected this.", "border-border-subtle"],
    ["refused", "Refused by safety rules.", "border-danger-border"],
    ["confirmed_unapplied", "Confirmed, not applied yet.", "border-warning-border"],
  ] as const)("renders %s as a coloured status without buttons", (status, text, border) => {
    render(<ProposalCard proposal={proposal({ status })} deciding={false} onDecide={vi.fn()} />);
    expect(screen.getByTestId("agent-proposal-status")).toHaveTextContent(text);
    expect(screen.getByTestId("agent-proposal-card")).toHaveClass(border);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText(/journal entry/)).not.toBeInTheDocument();
  });

  it("disables decisions while another decision is in flight", () => {
    render(<ProposalCard proposal={proposal()} deciding onDecide={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Confirm proposal" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject proposal" })).toBeDisabled();
  });

  it("embeds the strategy draft review only when a draft is linked, and never shows IDs or hashes", () => {
    const { container, rerender } = render(<ProposalCard proposal={proposal()} deciding={false} onDecide={vi.fn()} />);
    expect(screen.queryByTestId("agent-strategy-draft")).not.toBeInTheDocument();
    rerender(
      <ProposalCard
        proposal={proposal({ kind: "propose_strategy", linked_strategy_proposal_id: "22222222-2222-4222-8222-222222222222" })}
        deciding={false}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByTestId("agent-strategy-draft")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}|a{16}/);
  });
});
