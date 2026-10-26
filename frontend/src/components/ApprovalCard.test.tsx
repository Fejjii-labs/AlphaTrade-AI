import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApprovalCard } from "@/components/ApprovalCard";
import type { ApprovalRequest } from "@/lib/api/types";
import { expectNoIdentifiers } from "@/test/no-identifiers";

const pendingApproval: ApprovalRequest = {
  id: "approval-12345678",
  proposal_id: "proposal-1",
  organization_id: "org-1",
  user_id: "user-1",
  status: "pending",
  risk_level: "medium",
  confidence: 0.6,
  approval_reason: "Low confidence trade",
  created_at: new Date().toISOString(),
};

describe("ApprovalCard", () => {
  afterEach(() => cleanup());

  it("renders paper-safe action buttons for pending approvals", () => {
    render(
      <ApprovalCard
        approval={pendingApproval}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onNeedsAnalysis={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /approve for paper review/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reject proposal/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /needs more analysis/i })).toBeInTheDocument();
    expectNoIdentifiers();
  });

  it("hides action buttons when approval is approved", () => {
    render(
      <ApprovalCard
        approval={{ ...pendingApproval, status: "approved" }}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /approve for paper review/i })).not.toBeInTheDocument();
  });
});
