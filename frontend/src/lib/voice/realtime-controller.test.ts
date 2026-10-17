import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { RealtimeVoiceController, type RealtimeSnapshot } from "./realtime-controller";
import { clearTokens, setTokens } from "@/lib/auth/session";
import type { AgentTurnResult } from "@/lib/api/types";
import type { TurnBody } from "@/components/agent/turn-recovery";

const conversation = "11111111-1111-4111-8111-111111111111";
const key = "22222222-2222-4222-8222-222222222222";
const body: TurnBody = { message: "My BTC rule may be outdated", conversation_id: conversation };
class Socket {
  readyState = 1; bufferedAmount = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  sent: Record<string, unknown>[] = [];
  send(data: string) { this.sent.push(JSON.parse(data)); }
  close = vi.fn();
  emit(event: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(event) }); }
}
const controllers: RealtimeVoiceController[] = [];
function fixture(changes = {}) {
  const socket = new Socket();
  const states: RealtimeSnapshot[] = [];
  const audio = { start: vi.fn().mockResolvedValue(undefined), stop: vi.fn(), play: vi.fn(), interrupt: vi.fn().mockReturnValue(null) };
  const configuration = vi.fn().mockResolvedValue({ available: true, maximum_seconds: 30 });
  const pending = vi.fn().mockReturnValue(true), result = vi.fn().mockReturnValue(true);
  const connect = vi.fn().mockReturnValue(socket);
  const controller = new RealtimeVoiceController({
    context: { symbol: "BTCUSDT" }, ensureConversation: vi.fn().mockResolvedValue(conversation),
    changed: state => states.push(state), audio, configuration, pending, result, connect,
    ...changes,
  });
  controllers.push(controller);
  return { controller, socket, states, audio, pending, result, connect, configuration };
}
beforeEach(() => { sessionStorage.clear(); setTokens("fixture-auth-bearer", "fixture-refresh"); });
afterEach(() => { controllers.splice(0).forEach(controller => controller.dispose()); vi.useRealTimers(); });

describe("supported realtime controller (synthetic protocol, no actual provider/microphone)", () => {
  it("a saturated socket closes once without recursive shutdown or retry", async () => {
    const f = fixture(); await f.controller.start();
    f.socket.bufferedAmount = 192001;
    f.controller.interrupt();
    expect(f.socket.close).toHaveBeenCalledTimes(1);
    expect(f.states.at(-1)?.error).toContain("too slow");
    expect(f.socket.sent).toHaveLength(0);
    expect(f.connect).toHaveBeenCalledTimes(1);
  });
  it("unbound readiness leaves typed fallback without microphone or socket", async () => {
    const f = fixture({ configuration: vi.fn().mockResolvedValue({ available: false }) });
    await f.controller.start();
    expect(f.audio.start).not.toHaveBeenCalled(); expect(f.connect).not.toHaveBeenCalled();
    expect(f.states.at(-1)).toMatchObject({ state: "error", active: false, error: expect.stringContaining("Continue typing") });
  });
  it("microphone denial is explicit and does not connect", async () => {
    const audio = { start: vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError")), stop: vi.fn(), interrupt: vi.fn(), play: vi.fn() };
    const f = fixture({ audio }); await f.controller.start();
    expect(f.connect).not.toHaveBeenCalled();
    expect(f.states.at(-1)?.error).toContain("Microphone access was denied");
    expect(audio.stop).toHaveBeenCalled();
  });
  it("auth travels only in first application envelope; no model or provider key in URL", async () => {
    const f = fixture(); await f.controller.start(); f.socket.onopen?.();
    expect(f.connect.mock.calls[0][0]).toMatch(/ws:\/\/.*\/agent\/voice\/realtime$/);
    expect(f.connect.mock.calls[0][0]).not.toContain("fixture-auth");
    expect(f.socket.sent[0]).toEqual({ type: "session.start", access_token: "fixture-auth-bearer", conversation_id: conversation, context: { symbol: "BTCUSDT" } });
    expect(f.socket.sent[0]).not.toHaveProperty("model");
    f.socket.emit({ type: "session.ready", session_id: "synthetic" });
    expect(f.states.at(-1)).toMatchObject({ state: "listening", active: true });
  });
  it("two consecutive spoken requests retain context and durable acknowledgement order", async () => {
    const f = fixture(); await f.controller.start(); f.socket.onopen?.();
    f.socket.emit({ type: "session.ready" });
    for (const requestKey of [key, "33333333-3333-4333-8333-333333333333"]) {
      f.socket.emit({ type: "turn.pending", request_key: requestKey, body });
      expect(f.pending).toHaveBeenLastCalledWith(requestKey, body);
      expect(f.socket.sent.at(-1)).toEqual({ type: "turn.accept", request_key: requestKey });
      const canonical = { reply: "Stored rule is unverified; current quote unavailable.", conversation_id: conversation } as AgentTurnResult;
      f.socket.emit({ type: "turn.result", request_key: requestKey, result: canonical });
      expect(f.result).toHaveBeenLastCalledWith(requestKey, canonical);
    }
    expect(f.connect).toHaveBeenCalledTimes(1);
  });
  it("storage refusal sends no canonical-turn acknowledgement", async () => {
    const f = fixture({ pending: vi.fn().mockReturnValue(false) }); await f.controller.start();
    f.socket.emit({ type: "session.ready" }); f.socket.emit({ type: "turn.pending", request_key: key, body });
    expect(f.socket.sent.some(event => event.type === "turn.accept")).toBe(false);
    expect(f.states.at(-1)?.error).toContain("recovery could not be retained");
    expect(f.socket.close).toHaveBeenCalledTimes(1);
  });
  it("lost reply keeps original envelope without reconnect or automatic duplicate", async () => {
    const f = fixture(); await f.controller.start(); f.socket.emit({ type: "session.ready" });
    f.socket.emit({ type: "turn.pending", request_key: key, body }); f.socket.onerror?.();
    expect(f.pending).toHaveBeenCalledTimes(1); expect(f.pending).toHaveBeenCalledWith(key, body);
    expect(f.connect).toHaveBeenCalledTimes(1); expect(f.result).not.toHaveBeenCalled();
    expect(f.states.at(-1)?.error).toContain("Recover");
  });
  it("malformed successful canonical reply remains a recovery obligation", async () => {
    const f = fixture({ result: vi.fn().mockReturnValue(false) }); await f.controller.start();
    f.socket.emit({ type: "turn.pending", request_key: key, body });
    f.socket.emit({ type: "turn.result", request_key: key, result: { broken: true } });
    expect(f.states.at(-1)?.error).toContain("could not be verified");
    expect(f.socket.close).toHaveBeenCalled(); expect(f.connect).toHaveBeenCalledTimes(1);
  });
  it("logout independently closes tracks/socket and fences delayed events", async () => {
    const f = fixture(); await f.controller.start(); f.socket.emit({ type: "session.ready" });
    const late = f.socket.onmessage; clearTokens();
    late?.({ data: JSON.stringify({ type: "turn.pending", request_key: key, body }) });
    expect(f.audio.stop).toHaveBeenCalled(); expect(f.socket.close).toHaveBeenCalled();
    expect(f.pending).not.toHaveBeenCalled(); expect(f.states.at(-1)?.active).toBe(false);
  });
  it("conversation change ends voice and prevents applying another conversation reply", async () => {
    const f = fixture(); await f.controller.start(); const late = f.socket.onmessage;
    f.controller.setConversation("different-conversation");
    late?.({ data: JSON.stringify({ type: "turn.result", request_key: key, result: {} }) });
    expect(f.result).not.toHaveBeenCalled(); expect(f.socket.close).toHaveBeenCalled();
  });
  it("interruption sends played position and provider-request cancellation", async () => {
    const f = fixture(); await f.controller.start(); f.socket.emit({ type: "session.ready" });
    f.audio.interrupt.mockReturnValue({ item_id: "audio-one", audio_end_ms: 230 });
    f.controller.interrupt();
    expect(f.socket.sent.at(-1)).toEqual({ type: "response.interrupt", item_id: "audio-one", audio_end_ms: 230 });
    f.socket.emit({ type: "playback.clear" });
    expect(f.socket.sent.at(-1)).toEqual({ type: "playback.truncate", item_id: "audio-one", audio_end_ms: 230 });
  });
  it("permission completion after logout cannot leak an active microphone", async () => {
    let finish!: () => void;
    const audio = { start: vi.fn(() => new Promise<void>(resolve => { finish = resolve; })), stop: vi.fn(), play: vi.fn(), interrupt: vi.fn() };
    const f = fixture({ audio }); const starting = f.controller.start();
    for (let i = 0; i < 5; i++) await Promise.resolve();
    clearTokens(); finish(); await starting;
    expect(audio.stop.mock.calls.length).toBeGreaterThanOrEqual(2); expect(f.connect).not.toHaveBeenCalled();
  });
  it("finite client duration ends capture without reconnect", async () => {
    vi.useFakeTimers(); const f = fixture(); await f.controller.start();
    await vi.advanceTimersByTimeAsync(30001);
    expect(f.audio.stop).toHaveBeenCalled(); expect(f.socket.close).toHaveBeenCalled(); expect(f.connect).toHaveBeenCalledTimes(1);
  });
});
