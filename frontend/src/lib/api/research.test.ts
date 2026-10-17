import { beforeEach, expect, it, vi } from "vitest";
import { researchReceipt } from "@/test/research-fixtures";
import { experimentId, versionId } from "@/test/experiment-fixtures";
import { apiFetch } from "./client";
import { trendpulseScreen, trendpulseScreenings, trendpulseScreeningDetail } from "./generated/client";
vi.mock("./client", async original => ({ ...await original<typeof import("./client")>(), apiFetch: vi.fn() }));
beforeEach(() => vi.mocked(apiFetch).mockReset());
it("validates scoped summary reads and both version identities", async () => {
  const { evidence, signal: receiptSignal, ...summary } = researchReceipt();
  void evidence; void receiptSignal;
  vi.mocked(apiFetch).mockResolvedValue({ items: [summary], total: 1, limit: 5, offset: 0 });
  const signal = new AbortController().signal;
  await trendpulseScreenings(experimentId, versionId, { limit: 5, status: "refused" }, { signal });
  expect(apiFetch).toHaveBeenCalledExactlyOnceWith(`/experiments/${experimentId}/versions/${versionId}/trendpulse-screenings`, expect.objectContaining({ auth: true, signal, query: { limit: 5, status: "refused" } }));
});
it("submits exactly one validated immutable request and returns original receipts", async () => {
  const receipt = researchReceipt(); const body = { request_id: receipt.request_id, variant_key: "baseline", trigger_end: receipt.trigger_end };
  vi.mocked(apiFetch).mockResolvedValue(receipt); await expect(trendpulseScreen(experimentId, versionId, body)).resolves.toEqual(receipt);
  expect(apiFetch).toHaveBeenCalledOnce(); expect(vi.mocked(apiFetch).mock.calls[0][1]?.body).toBe(JSON.stringify(body));
});
it("refuses invalid local requests and caller evidence/clock before network", async () => {
  for (const body of [{ request_id: "invalid", variant_key: "baseline", trigger_end: "invalid" }, { request_id: researchReceipt().request_id, variant_key: "baseline", trigger_end: researchReceipt().trigger_end, evaluated_at: "2026-10-10T12:00:00Z" }]) await expect(trendpulseScreen(experimentId, versionId, body as never)).rejects.toMatchObject({ status: 0 });
  expect(apiFetch).not.toHaveBeenCalled();
});
it("retains uncertainty for malformed success without duplicate transport or exposed rejected data", async () => {
  vi.mocked(apiFetch).mockResolvedValue({ private_content: "hidden" }); await expect(trendpulseScreeningDetail(researchReceipt().id)).rejects.toMatchObject({ status: 0, body: null }); expect(apiFetch).toHaveBeenCalledOnce();
});
it.each([{ execution_authorized: true }, { management_authority: true }, { sample_eligible: true }, { performance: { returns: 0 } }, { receipt_provenance: "native" }])("rejects fabricated authority/performance/provenance %s", async fields => {
  vi.mocked(apiFetch).mockResolvedValue({ ...researchReceipt(), ...fields }); await expect(trendpulseScreeningDetail(researchReceipt().id)).rejects.toMatchObject({ status: 0 });
});
