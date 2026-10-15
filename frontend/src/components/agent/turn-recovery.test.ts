import { beforeEach, expect, it, vi } from "vitest";
import { clearPendingTurns, pendingFor, persistPending, persistPendingDurably, persistTypedDraft, typedDraftFor, recordTerminal, recoveryDetails, removePending, terminalFor } from "./turn-recovery";
const key = "11111111-1111-4111-8111-111111111111";
const conversation = "22222222-2222-4222-8222-222222222222";
beforeEach(() => { clearPendingTurns(); sessionStorage.clear(); });
it("acknowledges realtime recovery only after the exact body is durably stored", () => {
  const saved = { key, body: { message: "Original spoken input", conversation_id: conversation },
    conversationId: conversation, state: "uncertain" as const, createdAt: "2026-01-01T00:00:00Z" };
  expect(persistPendingDurably("actor", saved)).toBe(true);
  expect(JSON.parse(sessionStorage.getItem("alphatrade:pending-turn:actor")!)).toEqual([saved]);
});
it("storage denial cannot acknowledge a realtime business turn", () => {
  const saved = { key, body: { message: "Original spoken input", conversation_id: conversation },
    conversationId: conversation, state: "uncertain" as const, createdAt: "2026-01-01T00:00:00Z" };
  const denied = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("blocked", "SecurityError"); });
  try { expect(persistPendingDurably("actor", saved)).toBe(false); }
  finally { denied.mockRestore(); }
});
it("keeps typed drafts scoped to their actor/conversation and clears them on logout", () => {
  persistTypedDraft("actor", conversation, "Private draft");
  expect(typedDraftFor("actor", conversation)).toBe("Private draft");
  expect(typedDraftFor("actor", null)).toBe("");
  expect(typedDraftFor("other", conversation)).toBe("");
  sessionStorage.setItem("alphatrade:typed-draft:reload:" + conversation, "Reloaded draft");
  expect(typedDraftFor("reload", conversation)).toBe("Reloaded draft");
  clearPendingTurns();
  expect(typedDraftFor("actor", conversation)).toBe("");
});
it("retains the original body across reload and conflict attachment until acknowledgment", () => {
  const body = { message: "Original request", action: { name: "strategy.create", arguments: { text: "Original request", setup_type: "sfp" } } };
  persistPending("tenant:user", { key, body, conversationId: null, state: "uncertain", createdAt: "2026-01-01T00:00:00Z" });
  expect(pendingFor("tenant:user", null)?.body).toEqual(body);
  const old = pendingFor("tenant:user", null)!;
  persistPending("tenant:user", { ...old, conversationId: conversation, state: "turn_running" });
  expect(pendingFor("tenant:user", null)?.key).toBe(key);
  expect(pendingFor("tenant:user", conversation)?.body).toEqual(body);
  expect(pendingFor("other:user", null)).toBeNull();
  removePending("tenant:user", key);
  expect(pendingFor("tenant:user", null)).toBeNull();
});
it("requires a generated machine reason and UUID identities for recovery", () => {
  expect(recoveryDetails({ error: { details: { reason: "turn_capture", turn_id: key, conversation_id: conversation } } })).toMatchObject({ reason: "turn_capture" });
  expect(recoveryDetails({ error: { details: { reason: "invented", turn_id: key, conversation_id: conversation } } })).toBeNull();
  expect(recoveryDetails({ error: { details: { reason: "turn_running", turn_id: "invalid", conversation_id: conversation } } })).toBeNull();
});

it("validates the complete body and local voice identity before persistence", () => {
  const saved = { key, body: { message: "Review rules", conversation_id: conversation }, conversationId: conversation,
    state: "uncertain" as const, createdAt: "2026-01-01T00:00:00Z", origin: "voice" as const,
    voice: { turnKey: key, conversationId: conversation, transcript: "Review rules", origin: "voice" as const } };
  expect(() => persistPending("actor", { ...saved, body: { ...saved.body, source_document_id: "invalid" } })).toThrow();
  expect(() => persistPending("actor", { ...saved, voice: { ...saved.voice, transcript: "Changed speech" } })).toThrow();
  expect(() => persistPending("actor", { ...saved, voice: undefined })).toThrow();
  expect(sessionStorage.getItem("alphatrade:pending-turn:actor")).toBeNull();
  persistPending("actor", saved);
  expect(JSON.parse(sessionStorage.getItem("alphatrade:pending-turn:actor")!)).toEqual([saved]);
  sessionStorage.clear();
  expect(pendingFor("actor", conversation)).toEqual(saved);
});

it("requires matching terminal evidence and keeps an ambiguous request when storage is absent", () => {
  const saved = { key, body: { message: "Review rules", conversation_id: conversation }, conversationId: conversation,
    state: "uncertain" as const, createdAt: "2026-01-01T00:00:00Z" };
  persistPending("actor", saved);
  sessionStorage.clear();
  recordTerminal("actor", { turnKey: key, conversationId: key, outcome: "acknowledged" });
  expect(pendingFor("actor", conversation)).toEqual(saved);
  // A valid-looking but mismatched persisted proof also cannot erase the request.
  sessionStorage.setItem("alphatrade:turn-outcome:actor", JSON.stringify([{ turnKey: key, conversationId: key, outcome: "acknowledged" }]));
  expect(pendingFor("actor", conversation)).toEqual(saved);
  recordTerminal("actor", { turnKey: key, conversationId: conversation, outcome: "acknowledged" });
  expect(pendingFor("actor", conversation)).toBeNull();
  expect(terminalFor("actor", key)).toEqual({ turnKey: key, conversationId: conversation, outcome: "acknowledged" });
});
