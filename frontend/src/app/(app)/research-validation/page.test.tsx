import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ResearchValidationPage from "./page";
import { PROMOTE_RESEARCH_VALIDATION_CANDIDATE } from "@/lib/api";
import type { ResearchValidationEvidenceResponse } from "@/lib/api/types";

const RUN_ID = "11111111-1111-1111-1111-111111111111";

const sampleEvidence: ResearchValidationEvidenceResponse = {
  items: [
    {
      backtest_run_id: RUN_ID,
      strategy_id: "22222222-2222-2222-2222-222222222222",
      strategy_version_id: "33333333-3333-3333-3333-333333333333",
      strategy_name: "HTF Pullback",
      version: 2,
      symbol: "BTCUSDT",
      timeframe: "1h",
      regime: "trending",
      status: "completed",
      dataset_hash: "abcd1234efgh5678",
      config_hash: "cfg12345hash6789",
      result_hash: "res12345hash6789",
      evidence_tier: "tier2",
      sample_size: 42,
      oos_trade_count: 20,
      oos_expectancy: "0.12",
      oos_profit_factor: 1.4,
      confirm_trade_count: 5,
      eligible_for_promotion: true,
      warnings: ["insufficient_confirm_sample", "missing_oos"],
      existing_candidate_id: null,
      existing_run_plan_id: null,
      promotion_blocked_reason: null,
    },
  ],
  generated_at: "2026-07-25T00:00:00Z",
  note: "Advisory only — never feeds execution or risk decisions.",
};

const mockReload = vi.fn();
const mockEvidence = vi.fn();
const mockPromote = vi.fn();

let asyncState: {
  data: { forbidden: boolean; data: ResearchValidationEvidenceResponse | null } | null;
  loading: boolean;
  error: string | null;
};

vi.mock("@/hooks/useAsyncData", () => ({
  useAsyncData: () => ({
    data: asyncState.data,
    loading: asyncState.loading,
    error: asyncState.error,
    reload: mockReload,
  }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      researchValidation: {
        evidence: (...args: unknown[]) => mockEvidence(...args),
        promote: (...args: unknown[]) => mockPromote(...args),
        backtestStatus: vi.fn(),
      },
    },
  };
});

describe("ResearchValidationPage AT-035", () => {
  beforeEach(() => {
    asyncState = {
      data: { forbidden: false, data: sampleEvidence },
      loading: false,
      error: null,
    };
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders loading state", () => {
    asyncState = { data: null, loading: true, error: null };
    render(<ResearchValidationPage />);
    expect(screen.getByText(/loading research validation evidence/i)).toBeInTheDocument();
  });

  it("renders empty state", () => {
    asyncState = {
      data: { forbidden: false, data: { ...sampleEvidence, items: [] } },
      loading: false,
      error: null,
    };
    render(<ResearchValidationPage />);
    expect(screen.getByTestId("research-validation-page")).toBeInTheDocument();
    expect(screen.getByText(/no research validation evidence/i)).toBeInTheDocument();
  });

  it("renders error state", () => {
    asyncState = { data: null, loading: false, error: "Network error" };
    render(<ResearchValidationPage />);
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });

  it("renders permission denied state for 403", () => {
    asyncState = { data: { forbidden: true, data: null }, loading: false, error: null };
    render(<ResearchValidationPage />);
    expect(screen.getByTestId("research-validation-forbidden")).toBeInTheDocument();
    expect(screen.getByText(/do not have permission/i)).toBeInTheDocument();
  });

  it("renders evidence tier and warning banners", () => {
    render(<ResearchValidationPage />);
    expect(screen.getByTestId("research-validation-list")).toBeInTheDocument();
    expect(screen.getByTestId(`research-validation-item-${RUN_ID}`)).toBeInTheDocument();
    expect(screen.getByText("Tier 2")).toBeInTheDocument();
    expect(screen.getByTestId(`research-validation-warnings-${RUN_ID}`)).toBeInTheDocument();
    expect(screen.getByText(/confirm trade sample is below/i)).toBeInTheDocument();
    expect(screen.getByText(/out-of-sample metrics are missing/i)).toBeInTheDocument();
    expect(screen.getAllByText(/advisory only/i).length).toBeGreaterThan(0);
  });

  it("disables promote submit until confirm phrase typed", () => {
    render(<ResearchValidationPage />);
    const submit = screen.getByTestId(`research-validation-promote-submit-${RUN_ID}`);
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByTestId(`research-validation-promote-confirm-${RUN_ID}`), {
      target: { value: PROMOTE_RESEARCH_VALIDATION_CANDIDATE },
    });
    expect(submit).not.toBeDisabled();
  });

  it("shows already-exists message on idempotent promote", async () => {
    mockPromote.mockResolvedValue({
      candidate: { candidate_id: "candidate-1" },
      already_exists: true,
      eligibility: { eligible: true, tier: "tier2", warnings: [], blocked_reason: null },
      links: {
        backtest_run_id: RUN_ID,
        candidate_id: "candidate-1",
        run_plan_id: null,
      },
    });

    render(<ResearchValidationPage />);
    fireEvent.change(screen.getByTestId(`research-validation-promote-confirm-${RUN_ID}`), {
      target: { value: PROMOTE_RESEARCH_VALIDATION_CANDIDATE },
    });
    fireEvent.click(screen.getByTestId(`research-validation-promote-submit-${RUN_ID}`));

    await waitFor(() => {
      expect(await screen.findByTestId(`research-validation-promoted-${RUN_ID}`)).toHaveTextContent(
        /already exists in the paper validation queue/i,
      );
    });
  });

  it("promotes with confirmation phrase", async () => {
    mockPromote.mockResolvedValue({
      candidate: { candidate_id: "candidate-1" },
      already_exists: false,
      eligibility: { eligible: true, tier: "tier2", warnings: [], blocked_reason: null },
      links: {
        backtest_run_id: RUN_ID,
        candidate_id: "candidate-1",
        run_plan_id: null,
      },
    });

    render(<ResearchValidationPage />);
    fireEvent.change(screen.getByTestId(`research-validation-promote-confirm-${RUN_ID}`), {
      target: { value: PROMOTE_RESEARCH_VALIDATION_CANDIDATE },
    });
    fireEvent.click(screen.getByTestId(`research-validation-promote-submit-${RUN_ID}`));

    await waitFor(() => {
      expect(mockPromote).toHaveBeenCalledWith({
        confirm: PROMOTE_RESEARCH_VALIDATION_CANDIDATE,
        backtest_run_id: RUN_ID,
      });
    });
    expect(await screen.findByTestId(`research-validation-promoted-${RUN_ID}`)).toHaveTextContent(
      /promoted to the paper validation queue/i,
    );
  });
});
