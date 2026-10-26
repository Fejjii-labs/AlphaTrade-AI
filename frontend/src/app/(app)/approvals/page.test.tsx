import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ApprovalsPage from "./page";
import type { ApprovalRequest, PaginatedApprovalRequests, TradeProposal } from "@/lib/api/types";

const search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useSearchParams: () => search,
}));

vi.mock("@/components/KillSwitchButton", () => ({
  KillSwitchButton: () => <button type="button">Kill switch</button>,
}));

vi.mock("@/components/ApprovalDetailPanel", () => ({
  ApprovalDetailPanel: ({ approval }: { approval: ApprovalRequest }) => (
    <div data-testid="approval-detail-panel">Detail {approval.id}</div>
  ),
}));

function makeApproval(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    id: "appr-1",
    proposal_id: "prop-1",
    organization_id: "org-1",
    user_id: "user-1",
    status: "pending",
    risk_level: "medium",
    confidence: 0.72,
    created_at: "2026-07-27T10:00:00.000Z",
    ...overrides,
  };
}

function makeProposal(): TradeProposal {
  return {
    id: "prop-1",
    organization_id: "org-1",
    user_id: "user-1",
    strategy_id: "htf_trend_pullback",
    symbol: "BTCUSDT",
    timeframe: "1h",
    direction: "long",
    entry_price: "50000",
    position_size: "0.1",
    leverage: "1",
    exit: {
      invalidation: "Below stop",
      stop_loss: "49000",
      take_profits: [{ price: "52000", size_fraction: 1 }],
    },
    confidence: 0.7,
    risk_level: "medium",
    rationale: "Pullback",
    status: "pending_approval",
    approval_required: true,
    created_at: "2026-07-27T10:00:00.000Z",
  };
}

function paginated(items: ApprovalRequest[]): PaginatedApprovalRequests {
  return { items, total: items.length, limit: 50, offset: 0 };
}

const mockReload = vi.fn<() => Promise<void>>();
const mockApprove = vi.fn();
const mockReject = vi.fn();
const mockNeeds = vi.fn();

let listState: {
  data: PaginatedApprovalRequests | null;
  loading: boolean;
  error: string | null;
};

let workflowState: {
  data: { approval: ApprovalRequest; proposal: TradeProposal | null } | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
};

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: (loader: () => Promise<unknown>, deps: unknown[]) => {
    void loader;
    if (Array.isArray(deps) && deps.length === 1 && deps[0] !== undefined) {
      return workflowState;
    }
    return {
      data: listState.data,
      loading: listState.loading,
      error: listState.error,
      reload: mockReload,
    };
  },
}));

vi.mock("@/lib/api", () => ({
  api: {
    approvals: {
      list: vi.fn(),
      workflow: vi.fn(),
      approve: (...args: unknown[]) => mockApprove(...args),
      reject: (...args: unknown[]) => mockReject(...args),
      needsMoreAnalysis: (...args: unknown[]) => mockNeeds(...args),
      modify: vi.fn(),
    },
  },
}));

describe("ApprovalsPage route honesty (FP2-129)", () => {
  beforeEach(() => {
    search.delete("id");
    listState = { data: null, loading: true, error: null };
    workflowState = {
      data: null,
      loading: false,
      error: null,
      reload: vi.fn(),
    };
    mockReload.mockResolvedValue(undefined);
    mockApprove.mockResolvedValue({});
    mockReject.mockResolvedValue({});
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders a single h1 and paper-only posture copy", () => {
    listState = { data: paginated([]), loading: false, error: null };
    render(<ApprovalsPage />);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Approvals");
    expect(screen.getByText(/real trading remains disabled/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /kill switch/i })).toBeInTheDocument();
  });

  it("renders only the loading state while the request is unresolved", () => {
    listState = { data: null, loading: true, error: null };
    render(<ApprovalsPage />);
    expect(screen.getByTestId("loading-state")).toBeInTheDocument();
    expect(screen.queryByTestId("error-state")).not.toBeInTheDocument();
    expect(screen.queryByText(/No approval requests/i)).not.toBeInTheDocument();
  });

  it("renders only the error state with retry when the request failed", () => {
    listState = { data: null, loading: false, error: "Approvals source down" };
    render(<ApprovalsPage />);
    expect(screen.getByTestId("error-state")).toHaveTextContent("Approvals source down");
    expect(screen.queryByText(/No approval requests/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(mockReload).toHaveBeenCalledTimes(1);
  });

  it("renders the honest empty state only after a successful empty response", () => {
    listState = { data: paginated([]), loading: false, error: null };
    render(<ApprovalsPage />);
    expect(screen.getByText(/No approval requests/i)).toBeInTheDocument();
    expect(screen.getByText(/Select an approval/i)).toBeInTheDocument();
    expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    expect(screen.queryByTestId("error-state")).not.toBeInTheDocument();
  });

  it("renders successful content and exposes paper-review approve action", async () => {
    const approval = makeApproval();
    listState = { data: paginated([approval]), loading: false, error: null };
    workflowState = {
      data: { approval, proposal: makeProposal() },
      loading: false,
      error: null,
      reload: vi.fn(),
    };
    render(<ApprovalsPage />);
    expect(screen.getByText(/^Approval ·/i)).toBeInTheDocument();
    expect(screen.getByTestId("approval-detail-panel")).toBeInTheDocument();
    expect(screen.queryByText(/No approval requests/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /approve for paper review/i }));
    expect(mockApprove).toHaveBeenCalledWith("appr-1", "Approved in UI");
  });
});
