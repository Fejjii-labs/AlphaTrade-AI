import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StrategyDraftReview } from "./StrategyDraftReview";

const conversationId = "11111111-1111-4111-8111-111111111111";
const proposalId = "22222222-2222-4222-8222-222222222222";

vi.mock("@/lib/api", () => ({
  api: {
    conversations: {
      getProposal: vi.fn(async () => ({
        id: proposalId,
        conversation_id: conversationId,
        status: "draft",
        challenge_notes: [],
        proposed_structured_rules: {},
        proposed_pattern_spec: { pattern: "PATTERN_MARKER" },
        proposed_card: { title: "CARD_MARKER" },
        content_hash: null,
        validation: { valid: false },
      })),
    },
  },
}));

describe("StrategyDraftReview", () => {
  it("keeps pattern spec and card reachable in a raw view that is collapsed by default", async () => {
    render(<StrategyDraftReview conversationId={conversationId} proposalId={proposalId} />);
    const raw = await screen.findByTestId("agent-strategy-raw");
    expect(raw.tagName).toBe("DETAILS");
    expect(raw).not.toHaveAttribute("open");
    expect(raw.textContent).toContain("PATTERN_MARKER");
    expect(raw.textContent).toContain("CARD_MARKER");
  });
});
