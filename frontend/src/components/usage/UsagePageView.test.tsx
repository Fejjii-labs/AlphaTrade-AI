import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UsagePageView as UsagePage } from "./UsagePageView";
import type {
  PaginatedUsageEvents,
  QuotaStatus,
  UsageFeatureBreakdown,
  UsageProviderBreakdown,
  UsageSummary,
} from "@/lib/api/types";

const mockReload = vi.fn<() => Promise<void>>();

type UsageBundle = {
  summary: UsageSummary;
  events: PaginatedUsageEvents;
  quota: QuotaStatus | null;
  byFeature: UsageFeatureBreakdown[];
  byProvider: UsageProviderBreakdown[];
};

let asyncState: {
  data: UsageBundle | null;
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

vi.mock("@/lib/api", () => ({
  api: {
    usage: {
      summary: vi.fn(),
      events: vi.fn(),
      byFeature: vi.fn(),
      byProvider: vi.fn(),
      quota: vi.fn(),
    },
  },
}));

const summary: UsageSummary = {
  event_count: 3,
  total_input_tokens: 1000,
  total_output_tokens: 200,
  total_tokens: 1200,
  total_provider_reported_cost: "0.05",
  total_estimated_cost: "0.10",
  total_cost: "0.15",
  billing_grade_cost: "0.05",
  cost_is_placeholder: true,
  total_tool_calls: 2,
  fallback_count: 1,
  cache_hit_count: 0,
};

const quota: QuotaStatus = {
  quota: {
    organization_id: "org-1",
    monthly_token_limit: 2000000,
    monthly_cost_limit: "100.00",
    daily_request_limit: 5000,
    limit_agent_chat: 2000,
    limit_rag_ingest: 500,
    limit_market_analyze: 1000,
    limit_agent_narrative: 2000,
    limit_paper_execution: 200,
    soft_warning_threshold: "0.80",
    hard_block_threshold: "1.00",
  },
  usage: {
    monthly_tokens_used: 1200,
    monthly_tokens_limit: 2000000,
    monthly_tokens_pct: 0.0006,
    monthly_cost_used: "0.15",
    monthly_cost_limit: "100.00",
    monthly_cost_pct: 0.0015,
    daily_requests_used: 3,
    daily_requests_limit: 5000,
    daily_requests_pct: 0.0006,
    feature_usage: { agent_chat: 2, rag_ingest: 1 },
  },
  soft_limit_reached: false,
  hard_limit_reached: false,
  warnings: [],
  blocked_features: [],
};

function successBundle(overrides: Partial<UsageBundle> = {}): UsageBundle {
  return {
    summary,
    events: {
      items: [
        {
          request_id: "req-1",
          feature: "agent_chat",
          provider: "mock-llm",
          input_tokens: 100,
          output_tokens: 20,
          total_tokens: 120,
          estimated_cost: "0.01",
          cost_source: "estimate",
          cost_is_placeholder: true,
          tool_calls: 0,
          fallback_used: false,
          cache_hit: false,
          status: "ok",
          timestamp: "2026-07-27T10:00:00.000Z",
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    },
    quota,
    byFeature: [
      {
        feature: "agent_chat",
        event_count: 2,
        total_tokens: 800,
        total_cost: "0.08",
        fallback_count: 0,
      },
    ],
    byProvider: [
      {
        provider: "mock-llm",
        event_count: 2,
        total_tokens: 800,
        total_cost: "0.08",
        fallback_count: 1,
      },
    ],
    ...overrides,
  };
}

describe("Usage route (/usage) — FP2-129", () => {
  beforeEach(() => {
    asyncState = { data: null, loading: true, error: null };
    mockReload.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders loading without fabricated usage metrics", () => {
    render(<UsagePage />);
    expect(screen.getByTestId("loading-state")).toBeInTheDocument();
    expect(screen.queryByText(/Monthly tokens/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/1,200/)).not.toBeInTheDocument();
  });

  it("renders failed request with retry and no stale metrics", () => {
    asyncState = { data: null, loading: false, error: "Usage summary failed" };
    render(<UsagePage />);
    expect(screen.getByTestId("error-state")).toHaveTextContent("Usage summary failed");
    expect(screen.queryByText(/1,200/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(mockReload).toHaveBeenCalledTimes(1);
  });

  it("renders successful usage content with honest cost posture", () => {
    asyncState = { data: successBundle(), loading: false, error: null };
    render(<UsagePage />);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Usage");
    expect(screen.getByText(/not billing-grade/i)).toBeInTheDocument();
    expect(screen.getAllByText(/1,200/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Recent usage events/i)).toBeInTheDocument();
    expect(screen.getAllByText(/agent_chat/i).length).toBeGreaterThan(0);
  });

  it("renders honest empty events without inventing rows", () => {
    asyncState = {
      data: successBundle({
        events: { items: [], total: 0, limit: 20, offset: 0 },
      }),
      loading: false,
      error: null,
    };
    render(<UsagePage />);
    expect(screen.getByTestId("empty-state")).toHaveTextContent(/No usage events/i);
    expect(screen.queryByText(/req-1/i)).not.toBeInTheDocument();
  });
});
