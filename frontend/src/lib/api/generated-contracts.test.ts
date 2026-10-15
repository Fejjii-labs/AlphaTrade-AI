import { describe, expect, it, vi } from "vitest";
import { agentTurnFixture, attentionFixture, dailyReviewFixture } from "@/test/pilot-fixtures";
import * as validators from "./generated/validators";
import { api } from "./index";
import { apiFetch } from "./client";
vi.mock("./client", async (importOriginal) => {
  const original = await importOriginal<typeof import("./client")>();
  return { ...original, apiFetch: vi.fn() };
});

describe("generated HTTP boundaries", () => {
  it("rejects overlong requests before transport and accepts the schema boundary", async () => {
    vi.mocked(apiFetch).mockClear().mockResolvedValue(agentTurnFixture);
    await expect(api.agent.turn({ message: "x".repeat(8001) })).rejects.toMatchObject({
      status: 0, message: "Invalid API request. Check the supplied fields.",
    });
    expect(apiFetch).not.toHaveBeenCalled();
    await expect(api.agent.turn({ message: "x".repeat(8000) })).resolves.toEqual(agentTurnFixture);
    expect(apiFetch).toHaveBeenCalledOnce();
  });
  it("requires both stable message IDs and validates Boolean authority", () => {
    expect(validators.agentTurnResponse({ ...agentTurnFixture, authority_mutated: true })).toBe(true);
    expect(validators.agentTurnResponse({ ...agentTurnFixture, assistant_message_id: undefined })).toBe(false);
    expect(validators.agentTurnResponse({ ...agentTurnFixture, user_message_id: undefined })).toBe(false);
    expect(validators.agentTurnResponse({ ...agentTurnFixture, authority_mutated: "false" })).toBe(false);
  });
  it("preserves nullable/omitted fields and refuses unknown requests/enums", () => {
    const request = { message: "Review", strategy_id: null };
    expect(validators.agentTurnRequest(request)).toBe(true);
    expect(request).toEqual({ message: "Review", strategy_id: null });
    expect(validators.agentTurnRequest({ message: "Review" })).toBe(true);
    expect(validators.agentTurnRequest({ message: "Review", extra: true })).toBe(false);
    expect(validators.strategyPatchRequest({ setup_type: "nested_continuation" })).toBe(true);
    expect(validators.strategyPatchRequest({ setup_type: "invented" })).toBe(false);
  });
  it("rejects malformed/unsafe responses with a sanitized error", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ secret: "private-payload", authority_mutated: "false" });
    await expect(api.agent.turn({ message: "Review" })).rejects.toThrow("API response did not match its contract");
    await expect(api.dashboard.attention()).rejects.not.toThrow("private-payload");
  });
  it("does not coerce prices or discard decimal precision", () => {
    const quote = { symbol: "BTCUSDT", last_price: "0.123456789123456789", source: "fixture",
      is_live: false, is_stale: false, fallback_used: true, provider_name: "fixture" };
    const reply = { ...agentTurnFixture, market_quote: quote };
    expect(validators.agentTurnResponse(reply)).toBe(true);
    expect(reply.market_quote.last_price).toBe("0.123456789123456789");
    expect(validators.agentTurnResponse({ ...reply, market_quote: { ...quote, last_price: 1 } })).toBe(false);
  });
  it("uses apiFetch auth and cancellation, and never retries a malformed Agent result", async () => {
    vi.mocked(apiFetch).mockClear().mockResolvedValue(agentTurnFixture);
    const signal = new AbortController().signal;
    expect(await api.agent.turn({ message: "Review" }, { signal })).toEqual(agentTurnFixture);
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/agent/turns", expect.objectContaining({ auth: true, signal, method: "POST" }));
    expect(validators.attentionResponse(attentionFixture)).toBe(true);
    expect(validators.dailyReviewResponse(dailyReviewFixture)).toBe(true);
  });
  it("validates current indexing states and keeps datetime offsets strict", () => {
    const document = { id: agentTurnFixture.conversation_id, title: "Stored note", source_type: "general_note",
      version: 1, created_at: "2026-10-09T12:00:00Z", updated_at: "2026-10-09T12:00:00Z",
      ingestion_metadata: { indexing: { sql_chunk_count: 1, vector_backend: null,
        vector_index_status: "pending", fallback_used: false, observed_at: "2026-10-09T12:00:00Z",
        job_id: agentTurnFixture.conversation_id, attempts: 0, next_attempt_at: null, error_code: null } } };
    const page = { items: [document], total: 1, limit: 50, offset: 0 };
    for (const status of ["pending", "ready", "failed", "unknown"]) {
      document.ingestion_metadata.indexing.vector_index_status = status;
      expect(validators.knowledgeDocumentsResponse(page)).toBe(true);
    }
    document.created_at = "2026-10-09T12:00:00";
    expect(validators.knowledgeDocumentsResponse(page)).toBe(false);
    document.created_at = "2026-10-09T12:00:00Z";
    document.ingestion_metadata.indexing.vector_index_status = "invented";
    expect(validators.knowledgeDocumentsResponse(page)).toBe(false);
  });

});
