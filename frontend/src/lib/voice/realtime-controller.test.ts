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
const sockets: Socket[] = [];
function fixture(changes = {}) {
  const socket = new Socket();
  sockets.push(socket);
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
afterEach(() => {
  controllers.splice(0).forEach(controller => controller.dispose());
  sockets.splice(0).forEach(socket => socket.emit({ type: "session.closed", finalized: true }));
  vi.useRealTimers();
});

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

describe("GPT Live controller (mocked transport, no live provider or microphone)", () => {
  const liveConfiguration = () => vi.fn().mockResolvedValue({ available: true, provider: "gpt_live", transport: "websocket_pcm24", maximum_seconds: 30 });

  it("selects the Live application route only from enabled server availability", async () => {
    const unavailable = fixture({ configuration: vi.fn().mockResolvedValue({ available: false, provider: "gpt_live" }) });
    await unavailable.controller.start();
    expect(unavailable.audio.start).not.toHaveBeenCalled(); expect(unavailable.connect).not.toHaveBeenCalled();
    expect(unavailable.states.at(-1)).toMatchObject({ active: false, provider: "gpt_live", error: "GPT Live is unavailable. Continue typing or use Record." });
    const live = fixture({ configuration: liveConfiguration() }); await live.controller.start(); live.socket.onopen?.();
    expect(live.connect.mock.calls[0][0]).toMatch(/ws:\/\/.*\/agent\/voice\/live$/);
    expect(live.socket.sent[0]).toMatchObject({ type: "session.start", conversation_id: conversation, access_token: "fixture-auth-bearer" });
    expect(live.connect.mock.calls[0][0]).not.toContain("fixture-auth");
  });

  it("continuously captures after readiness and keeps captions separate from two durable Agent turns", async () => {
    const f = fixture({ configuration: liveConfiguration() }); await f.controller.start();
    const capture = f.audio.start.mock.calls[0][0] as (audio: string) => void;
    capture("before-ready"); expect(f.socket.sent).toHaveLength(0);
    f.socket.emit({ type: "session.ready" });
    capture("first-request"); capture("second-request");
    expect(f.socket.sent).toEqual([{ type: "audio.append", audio: "first-request" }, { type: "audio.append", audio: "second-request" }]);
    f.socket.emit({ type: "input.delta", text: "My BTC ", start_ms: 0, end_ms: 500 });
    f.socket.emit({ type: "input.delta", text: "rule", start_ms: 500, end_ms: 800 });
    f.socket.emit({ type: "reply.delta", text: "Unverified provider caption", start_ms: 0, end_ms: 700 });
    expect(f.pending).not.toHaveBeenCalled(); expect(f.result).not.toHaveBeenCalled();
    expect(f.states.at(-1)).toMatchObject({ state: "listening", transcript: "", inputCaption: "My BTC rule", replyCaption: "Unverified provider caption" });
    for (const requestKey of [key, "33333333-3333-4333-8333-333333333333"]) {
      f.socket.emit({ type: "turn.pending", request_key: requestKey, body });
      expect(f.pending).toHaveBeenLastCalledWith(requestKey, body);
      expect(f.socket.sent.at(-1)).toEqual({ type: "turn.accept", request_key: requestKey });
      const canonical = { reply: "Grounded Agent result", conversation_id: conversation } as AgentTurnResult;
      f.socket.emit({ type: "turn.result", request_key: requestKey, result: canonical });
      expect(f.result).toHaveBeenLastCalledWith(requestKey, canonical);
    }
    f.socket.emit({ type: "audio.delta", audio: "spoken-reply", item_id: "live-output" });
    f.socket.emit({ type: "reply.delta", text: " more caption", start_ms: 700, end_ms: 900 });
    f.socket.emit({ type: "response.done" });
    expect(f.states.at(-1)?.state).toBe("speaking");
    expect(f.connect).toHaveBeenCalledTimes(1);
  });

  it("locally mutes interruptions until explicit Resume audio is acknowledged without provider truncation", async () => {
    const f = fixture({ configuration: liveConfiguration() }); await f.controller.start(); f.socket.emit({ type: "session.ready" });
    f.audio.interrupt.mockReturnValue({ item_id: "old-output", audio_end_ms: 230 });
    f.controller.interrupt();
    expect(f.socket.sent.at(-1)).toEqual({ type: "response.interrupt" });
    f.socket.emit({ type: "audio.delta", audio: "in-flight-output", item_id: "old-output" });
    f.socket.emit({ type: "playback.clear", muted: true });
    f.socket.emit({ type: "reply.delta", text: "Still a caption", start_ms: 0, end_ms: 200 });
    f.socket.emit({ type: "playback.ready" }); // A stale acknowledgement cannot bypass the user's mute.
    f.socket.emit({ type: "playback.clear", muted: false }); // Incoming speech flushes without lifting manual mute.
    f.socket.emit({ type: "audio.delta", audio: "still-muted", item_id: "old-output" });
    expect(f.audio.play).not.toHaveBeenCalled(); expect(f.states.at(-1)?.outputMuted).toBe(true);
    expect(f.socket.sent.some(event => event.type === "playback.truncate")).toBe(false);
    f.controller.resume(); f.controller.resume();
    expect(f.socket.sent.filter(event => event.type === "playback.resume")).toHaveLength(1);
    f.socket.emit({ type: "audio.delta", audio: "queued-before-ack", item_id: "old-output" });
    expect(f.audio.play).not.toHaveBeenCalled();
    f.socket.emit({ type: "playback.ready" });
    f.socket.emit({ type: "audio.delta", audio: "new-output", item_id: "live-output" });
    expect(f.audio.play).toHaveBeenCalledExactlyOnceWith("new-output", "live-output");
    expect(f.states.at(-1)?.outputMuted).toBe(false);
  });

  it("flushes overlapping output for new speech without muting continuous playback", async () => {
    const f = fixture({ configuration: liveConfiguration() }); await f.controller.start();
    f.socket.emit({ type: "audio.delta", audio: "previous", item_id: "live-output" });
    f.socket.emit({ type: "playback.clear", muted: false });
    f.socket.emit({ type: "input.delta", text: "Let me add", start_ms: 100, end_ms: 400 });
    f.socket.emit({ type: "audio.delta", audio: "next", item_id: "live-output" });
    expect(f.audio.interrupt).toHaveBeenCalledOnce(); expect(f.audio.play).toHaveBeenCalledTimes(2);
    expect(f.states.at(-1)?.outputMuted).toBe(false);
  });

  it("ends capture immediately and waits only for usage finalization while rejecting stale callbacks", async () => {
    vi.useFakeTimers(); const f = fixture({ configuration: liveConfiguration() }); await f.controller.start();
    f.socket.emit({ type: "session.ready" });
    const stale = f.socket.onmessage;
    const capture = f.audio.start.mock.calls[0][0] as (audio: string) => void;
    f.controller.end();
    expect(f.audio.stop).toHaveBeenCalledOnce(); expect(f.socket.close).not.toHaveBeenCalled();
    expect(f.socket.sent.at(-1)).toEqual({ type: "session.end" });
    const stateCount = f.states.length;
    stale?.({ data: JSON.stringify({ type: "turn.pending", request_key: key, body }) });
    f.socket.emit({ type: "turn.result", request_key: key, result: {} });
    f.socket.emit({ type: "audio.delta", audio: "late", item_id: "live-output" }); capture("late-capture");
    expect(f.pending).not.toHaveBeenCalled(); expect(f.result).not.toHaveBeenCalled(); expect(f.audio.play).not.toHaveBeenCalled();
    expect(f.states).toHaveLength(stateCount); expect(f.socket.sent.at(-1)).toEqual({ type: "session.end" });
    await vi.advanceTimersByTimeAsync(11999); expect(f.socket.close).not.toHaveBeenCalled();
    f.socket.emit({ type: "session.closed", finalized: true });
    expect(f.socket.close).toHaveBeenCalledOnce(); expect(f.socket.onmessage).toBeNull();
    await vi.advanceTimersByTimeAsync(2); expect(f.socket.close).toHaveBeenCalledOnce();
  });

  it.each(["end", "logout", "conversation", "dispose"])("bounds finalization waiting on %s cleanup to twelve seconds", async cleanup => {
    vi.useFakeTimers(); const f = fixture({ configuration: liveConfiguration() }); await f.controller.start();
    const stale = f.socket.onmessage;
    if (cleanup === "logout") clearTokens();
    else if (cleanup === "conversation") f.controller.setConversation("different-conversation");
    else if (cleanup === "dispose") f.controller.dispose();
    else f.controller.end();
    stale?.({ data: JSON.stringify({ type: "turn.result", request_key: key, result: {} }) });
    expect(f.audio.stop).toHaveBeenCalled(); expect(f.result).not.toHaveBeenCalled();
    expect(f.socket.close).not.toHaveBeenCalled(); await vi.advanceTimersByTimeAsync(12000);
    expect(f.socket.close).toHaveBeenCalledOnce(); expect(f.connect).toHaveBeenCalledOnce();
  });

  it("cannot close or mutate a new session when an earlier session finishes draining", async () => {
    const next = new Socket(); sockets.push(next);
    const f = fixture({ configuration: liveConfiguration() });
    f.connect.mockReturnValueOnce(f.socket).mockReturnValueOnce(next);
    await f.controller.start(); f.controller.end(); await f.controller.start(); next.emit({ type: "session.ready" });
    const stateCount = f.states.length;
    f.socket.emit({ type: "session.closed", finalized: true });
    expect(f.socket.close).toHaveBeenCalledOnce(); expect(next.close).not.toHaveBeenCalled();
    expect(f.states).toHaveLength(stateCount); expect(f.states.at(-1)).toMatchObject({ active: true, state: "listening" });
  });

  it.each(["unconfirmed", "timeout"])("reports %s final usage only after the ended session is idle", async reason => {
    vi.useFakeTimers(); const f = fixture({ configuration: liveConfiguration() }); await f.controller.start();
    f.controller.end();
    expect(f.states.at(-1)).toMatchObject({ active: false, state: "idle", error: null });
    if (reason === "unconfirmed") f.socket.emit({ type: "session.closed", finalized: false });
    else await vi.advanceTimersByTimeAsync(12000);
    expect(f.states.at(-1)).toMatchObject({ active: false, state: "idle", error: "Voice ended, but final usage is unconfirmed." });
    expect(f.socket.close).toHaveBeenCalledOnce();
  });

  it("reports an unconfirmed server close after stopping an active Live session", async () => {
    const f = fixture({ configuration: liveConfiguration() }); await f.controller.start();
    f.socket.emit({ type: "session.closed", finalized: false });
    expect(f.audio.stop).toHaveBeenCalledOnce(); expect(f.socket.close).toHaveBeenCalledOnce();
    expect(f.states.at(-1)).toMatchObject({ active: false, state: "idle", error: "Voice ended, but final usage is unconfirmed." });
  });

  it.each(["unconfirmed", "timeout"])("fences %s final-usage warnings after a newer session starts", async reason => {
    vi.useFakeTimers(); const next = new Socket(); sockets.push(next);
    const f = fixture({ configuration: liveConfiguration() });
    f.connect.mockReturnValueOnce(f.socket).mockReturnValueOnce(next);
    await f.controller.start(); f.controller.end(); await f.controller.start(); next.emit({ type: "session.ready" });
    const stateCount = f.states.length;
    if (reason === "unconfirmed") f.socket.emit({ type: "session.closed", finalized: false });
    else await vi.advanceTimersByTimeAsync(12000);
    expect(f.states).toHaveLength(stateCount); expect(f.states.at(-1)).toMatchObject({ active: true, state: "listening", error: null });
    expect(f.socket.close).toHaveBeenCalledOnce(); expect(next.close).not.toHaveBeenCalled();
  });

  it.each(["unconfirmed", "timeout"])("cannot replace a failure message with a %s final-usage warning", async reason => {
    vi.useFakeTimers(); const f = fixture({ configuration: liveConfiguration() }); await f.controller.start();
    f.socket.onerror?.(); const failure = f.states.at(-1);
    expect(failure).toMatchObject({ state: "error", error: expect.stringContaining("Voice connection failed") });
    if (reason === "unconfirmed") f.socket.emit({ type: "session.closed", finalized: false });
    else await vi.advanceTimersByTimeAsync(12000);
    expect(f.states.at(-1)).toEqual(failure); expect(f.socket.close).toHaveBeenCalledOnce();
  });

  it.each(["unconfirmed", "timeout"])("suppresses %s final-usage callbacks after disposal", async reason => {
    vi.useFakeTimers(); const f = fixture({ configuration: liveConfiguration() }); await f.controller.start(); f.controller.dispose();
    const stateCount = f.states.length;
    if (reason === "unconfirmed") f.socket.emit({ type: "session.closed", finalized: false });
    else await vi.advanceTimersByTimeAsync(12000);
    expect(f.states).toHaveLength(stateCount); expect(f.socket.close).toHaveBeenCalledOnce();
  });

  it("a saturated Live socket closes immediately instead of waiting for finalization", async () => {
    const f = fixture({ configuration: liveConfiguration() }); await f.controller.start(); f.socket.bufferedAmount = 192001;
    f.controller.interrupt();
    expect(f.socket.close).toHaveBeenCalledOnce(); expect(f.states.at(-1)?.error).toContain("too slow");
  });
});
