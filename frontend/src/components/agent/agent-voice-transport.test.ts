import { beforeEach, expect, it, vi } from "vitest";
import { createAgentVoiceTransport } from "./agent-voice-transport";
import { clearPendingTurns, persistPending, recordTerminal } from "./turn-recovery";
const turn = { turnKey: "11111111-1111-4111-8111-111111111111", conversationId: "22222222-2222-4222-8222-222222222222",
  transcript: "Original speech", origin: "voice" as const };
beforeEach(() => { clearPendingTurns(); sessionStorage.clear(); });

it("recovers the retained complete body and key rather than rebuilding from current context", async () => {
  const run = vi.fn().mockResolvedValue({ outcome: "acknowledged", reply: "Done" });
  const transport = createAgentVoiceTransport("actor", run);
  const saved = { key: turn.turnKey, body: { message: turn.transcript, conversation_id: turn.conversationId,
    source_document_id: "33333333-3333-4333-8333-333333333333" }, conversationId: turn.conversationId,
    state: "uncertain" as const, createdAt: "2026-01-01T00:00:00Z", origin: "voice" as const, voice: turn };
  persistPending("actor", saved);
  sessionStorage.clear();
  const request = { ...turn, signal: new AbortController().signal };
  await transport.recover(request);
  expect(run).toHaveBeenCalledExactlyOnceWith(request, saved);
  expect(run.mock.calls[0][1].body).toBe(saved.body);
  await transport.recover({ ...request, transcript: "Changed context" });
  expect(run).toHaveBeenCalledOnce();
  expect(transport.pending?.(turn.conversationId)).toEqual(turn);
});

it("publishes proven terminal evidence to the controller without another request", () => {
  const run = vi.fn();
  const transport = createAgentVoiceTransport("actor", run);
  const listener = vi.fn();
  const unsubscribe = transport.subscribe?.(listener);
  recordTerminal("actor", { turnKey: turn.turnKey, conversationId: turn.conversationId, outcome: "acknowledged" });
  expect(listener).toHaveBeenCalledOnce();
  expect(transport.terminal?.(turn)?.outcome).toBe("acknowledged");
  expect(run).not.toHaveBeenCalled();
  unsubscribe?.();
});
