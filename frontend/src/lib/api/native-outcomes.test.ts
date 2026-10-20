import { beforeEach, describe, expect, it, vi } from "vitest";
import { nativeOutcomeFixture, nativePerformanceFixture } from "@/test/native-outcome-fixtures";
import { experimentId, versionId } from "@/test/experiment-fixtures";
import { apiFetch } from "./client";
import { experimentNativeOutcome, experimentNativePerformance } from "./generated/client";
import * as validators from "./generated/validators";

vi.mock("./client", async original => ({ ...await original<typeof import("./client")>(), apiFetch: vi.fn() }));
beforeEach(() => { vi.mocked(apiFetch).mockReset(); });
const outcome = {
  organization_id: experimentId, execution_account_id: experimentId, native_uid: "synthetic-uid",
  version_id: versionId, strategy_version_id: experimentId, variant_key: "baseline",
  source_record_id: experimentId, status: "unavailable", reason: "native_exit_parent_unverified",
  evaluated_at: "2026-10-11T11:00:00Z", coverage: "incomplete", freshness: "unavailable",
  quantity_unit: "contracts", quantity: "0.123456789123456789", currency: null,
  reported_realized_pnl: null, fee_cost: null, realized_pnl_after_fees_excluding_funding: null,
  facts: [], funding: null,
};
const performance = {
  organization_id: experimentId, version_id: versionId, sample_group_id: experimentId,
  variant_key: "baseline", native_uid: "synthetic-uid", evaluated_at: outcome.evaluated_at,
  status: "unavailable", reason: "insufficient_samples", sample_count: 0, required_sample_count: 30,
  currency: null, reported_realized_pnl: null, fee_cost: null,
  realized_pnl_after_fees_excluding_funding: null, win_rate: null, outcomes: [],
};
describe("generated native outcome boundary", () => {
  it("reads with all scoped identities, auth/cancellation, exact units and unknown money", async () => {
    const signal = new AbortController().signal;
    vi.mocked(apiFetch).mockResolvedValue(outcome);
    await expect(experimentNativeOutcome(experimentId, versionId, experimentId, { variant_key: "baseline" }, { signal })).resolves.toEqual(outcome);
    expect(apiFetch).toHaveBeenCalledExactlyOnceWith(`/experiments/${experimentId}/versions/${versionId}/outcomes/${experimentId}`, expect.objectContaining({ method: "GET", auth: true, signal, query: { variant_key: "baseline" } }));
    expect(outcome.quantity).toBe("0.123456789123456789");
    expect(outcome.fee_cost).toBeNull();
  });
  it("keeps zero eligible samples unavailable, refuses provisional totals and incomplete availability claims", async () => {
    vi.mocked(apiFetch).mockResolvedValue(performance);
    await expect(experimentNativePerformance(experimentId, versionId)).resolves.toEqual(performance);
    expect(validators.experimentNativePerformanceResponse({ ...performance, fee_cost: "0" })).toBe(false);
    expect(validators.experimentNativePerformanceResponse({ ...performance, status: "available", reason: null })).toBe(false);
    expect(validators.experimentNativeOutcomeResponse({ ...outcome, status: "available", reason: null })).toBe(false);
    expect(validators.experimentNativeOutcomeResponse({ ...outcome, reason: null })).toBe(false);
    expect(validators.experimentNativeOutcomeResponse({ ...outcome, quantity: 1 })).toBe(false);
    expect(validators.experimentNativeOutcomeResponse({ ...outcome, quantity_unit: "base" })).toBe(false);
  });
  it("accepts complete Pydantic-valid transport shapes and rejects removed core lineage or money", () => {
    // Synthetic transport fixtures prove schema interoperability, not venue trust.
    expect(validators.experimentNativeOutcomeResponse(nativeOutcomeFixture)).toBe(true);
    expect(validators.experimentNativePerformanceResponse(nativePerformanceFixture)).toBe(true);
    for (const change of [{ proof: null }, { fee_cost: null }, { native_exit_order_ids: [] }, { freshness: undefined }])
      expect(validators.experimentNativeOutcomeResponse({ ...nativeOutcomeFixture, ...change })).toBe(false);
  });
  it("never retries or leaks a malformed native response", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ secret: "private source response" });
    await expect(experimentNativeOutcome(experimentId, versionId, experimentId)).rejects.toMatchObject({ status: 0, message: "API response did not match its contract. Refresh or try again later." });
    expect(apiFetch).toHaveBeenCalledOnce();
  });
  it("accepts owned price statistics while fee-adjusted results remain unavailable", () => {
    const owned = { ...nativeOutcomeFixture, status: "unavailable", reason: "native_monetary_semantics_unverified",
      reported_realized_pnl: null, fee_cost: null, realized_pnl_after_fees_excluding_funding: null,
      closure_verified: true, price_pnl_excluding_fees_and_funding: "2.5",
      price_pnl_methodology: "verified-linear-fill-prices/v1" };
    const report = { ...performance, sample_count: 1, required_sample_count: 1,
      price_pnl_sample_count: 1, price_pnl_excluding_fees_and_funding: "2.5", price_pnl_win_rate: "1",
      price_pnl_currency: "USDT", price_pnl_methodology: "verified-linear-fill-prices/v1", outcomes: [owned] };
    expect(validators.experimentNativeOutcomeResponse(owned)).toBe(true);
    expect(validators.experimentNativePerformanceResponse(report)).toBe(true);
    expect(validators.experimentNativeOutcomeResponse({ ...owned, proof: null })).toBe(false);
    expect(validators.experimentNativePerformanceResponse({ ...report, price_pnl_methodology: null })).toBe(false);
    expect(validators.experimentNativePerformanceResponse({ ...report, fee_cost: "0" })).toBe(false);
  });
});
