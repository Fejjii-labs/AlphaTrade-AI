import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceConversationController } from "./conversation-controller";
import type { VoiceAgentTransport, VoiceTurnResult } from "./conversation-types";
import type { VoiceProvider } from "./types";

function setup(speechOutput = true) {
  let input!: Parameters<VoiceProvider["listen"]>[0];
  let output!: Parameters<VoiceProvider["speak"]>[1];
  let draft = "";
  const cancelInput = vi.fn();
  const cancelOutput = vi.fn();
  const provider: VoiceProvider = {
    capabilities: { input: true, output: speechOutput },
    listen: vi.fn(events => { input = events; events.onState("listening"); return { stop: vi.fn(), cancel: cancelInput }; }),
    speak: vi.fn((_text, events) => { output = events; events.onStart(); return { stop: cancelOutput, cancel: cancelOutput }; }),
    dispose: vi.fn(),
  };
  const transport: VoiceAgentTransport = {
    submit: vi.fn(async () => ({ outcome: "acknowledged" as const, reply: "Review risk first." })),
    recover: vi.fn(async () => ({ outcome: "acknowledged" as const, reply: "Recovered reply." })),
  };
  const controller = new VoiceConversationController({ provider, transport,
    getTypedDraft: () => draft, setTypedDraft: text => { draft = text; },
    createTurnKey: () => "stable-key", agentTimeoutMs: 1000 });
  controller.setContext("conversation-1", true);
  return { controller, provider, transport, cancelInput, cancelOutput,
    input: () => input, output: () => output,
    draft: () => draft, setDraft: (text: string) => { draft = text; },
    start: () => { controller.setMode("conversation"); controller.start(); },
    finish: (text = "Review my risk") => input.onComplete(text),
  };
}

describe("voice conversation controller", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("requires an explicit start, plays one acknowledged reply, then listens again", async () => {
    const s = setup();
    expect(s.provider.listen).not.toHaveBeenCalled();
    s.start();
    expect(s.provider.listen).toHaveBeenCalledWith(expect.any(Object), { turnCompletion: "utterance" });
    s.finish();
    expect(s.controller.getSnapshot().state).toBe("waiting");
    expect(s.transport.submit).toHaveBeenCalledWith(expect.objectContaining({
      turnKey: "stable-key", transcript: "Review my risk", conversationId: "conversation-1", origin: "voice", signal: expect.any(AbortSignal),
    }));
    await Promise.resolve();
    expect(s.provider.speak).toHaveBeenCalledWith("Review risk first.", expect.any(Object));
    expect(s.controller.getSnapshot().state).toBe("speaking");
    expect(s.provider.listen).toHaveBeenCalledTimes(1);
    s.output().onEnd();
    expect(s.provider.listen).toHaveBeenCalledTimes(2);
    expect(s.controller.getSnapshot().state).toBe("listening");
    s.controller.dispose();
  });

  it("latches completion and ignores repeated events and playback recognition feedback", async () => {
    const s = setup();
    s.start();
    const late = s.input();
    late.onTranscript("partial");
    late.onTranscript("partial");
    expect(s.transport.submit).not.toHaveBeenCalled();
    s.finish();
    late.onComplete("Review my risk");
    await Promise.resolve();
    late.onTranscript("Application reply");
    late.onComplete("Application reply");
    expect(s.transport.submit).toHaveBeenCalledTimes(1);
    expect(s.controller.getSnapshot().transcript).toBe("");
    s.controller.dispose();
  });

  it.each(["", "   "])("never submits silence %j or automatically loops listening", text => {
    const s = setup(); s.start(); s.finish(text);
    expect(s.transport.submit).not.toHaveBeenCalled();
    expect(s.provider.listen).toHaveBeenCalledTimes(1);
    expect(s.controller.getSnapshot().state).toBe("error");
    s.controller.dispose();
  });

  it("review mode requires a deliberate send and never resumes or plays automatically", async () => {
    const s = setup(); s.controller.start(); s.finish();
    expect(s.transport.submit).not.toHaveBeenCalled();
    s.controller.editTranscript("Edited transcript");
    s.controller.send(); s.controller.send();
    await Promise.resolve();
    expect(s.transport.submit).toHaveBeenCalledTimes(1);
    expect(s.provider.speak).not.toHaveBeenCalled();
    expect(s.controller.getSnapshot().reply).toBe("Review risk first.");
    expect(s.controller.getSnapshot().state).toBe("idle");
    s.controller.dispose();
  });

  it("preserves a draft written during recognition and requires send, append or replace", () => {
    const s = setup(); s.start(); s.setDraft("Typed first"); s.finish();
    expect(s.transport.submit).not.toHaveBeenCalled();
    expect(s.draft()).toBe("Typed first");
    s.controller.applyTranscript("append");
    expect(s.draft()).toBe("Typed first\nReview my risk");
    expect(s.controller.getSnapshot().sessionActive).toBe(false);
    s.controller.start(); s.finish("Replacement");
    s.controller.applyTranscript("replace");
    expect(s.draft()).toBe("Replacement");
    s.controller.dispose();
  });

  it("sending a reviewed transcript preserves the typed draft", async () => {
    const s = setup(); s.setDraft("Keep typed"); s.start(); s.finish(); s.controller.send();
    await Promise.resolve();
    expect(s.draft()).toBe("Keep typed");
    s.controller.dispose();
  });

  it.each(["uncertain", "throw", "timeout", "malformed"])("pauses on %s and recovers only the original frozen envelope", async kind => {
    const s = setup();
    let resolve!: (result: VoiceTurnResult) => void;
    if (kind === "timeout") vi.mocked(s.transport.submit).mockImplementation(() => new Promise(r => { resolve = r; }));
    else if (kind === "throw") vi.mocked(s.transport.submit).mockRejectedValue(new Error("private failure"));
    else vi.mocked(s.transport.submit).mockResolvedValue(kind === "malformed" ? { outcome: "acknowledged" } as VoiceTurnResult : { outcome: "uncertain" });
    s.start(); s.finish();
    const retained = s.controller.getSnapshot().unresolved!;
    expect(Object.isFrozen(retained)).toBe(true);
    if (kind === "timeout") vi.advanceTimersByTime(1000);
    await Promise.resolve();
    expect(s.controller.getSnapshot()).toMatchObject({ state: "paused", sessionActive: false, unresolved: retained });
    s.controller.start(); s.controller.send(); s.finish("Do not submit");
    expect(s.transport.submit).toHaveBeenCalledTimes(1);
    if (kind === "timeout") {
      expect(vi.mocked(s.transport.submit).mock.calls[0][0].signal.aborted).toBe(true);
      resolve({ outcome: "acknowledged", reply: "Late" });
      await Promise.resolve();
      expect(s.controller.getSnapshot().unresolved).toBe(retained);
    }
    s.controller.recover(); s.controller.recover();
    await Promise.resolve();
    expect(s.transport.recover).toHaveBeenCalledExactlyOnceWith(expect.objectContaining(retained));
    expect(s.controller.getSnapshot().unresolved).toBeNull();
    expect(s.provider.speak).not.toHaveBeenCalled();
    expect(s.provider.listen).toHaveBeenCalledTimes(1);
    s.controller.dispose();
  });

  it("known rejection unlocks the envelope and preserves editable speech", async () => {
    const s = setup(); vi.mocked(s.transport.submit).mockResolvedValue({ outcome: "rejected" });
    s.start(); s.finish(); await Promise.resolve();
    expect(s.controller.getSnapshot()).toMatchObject({ state: "error", unresolved: null, ready: true, transcript: "Review my risk" });
    s.controller.dispose();
  });

  it.each(["end", "hidden", "logout", "navigation", "dispose"])("ignores late recognition after %s", event => {
    const s = setup(); s.start(); const late = s.input();
    if (event === "end") s.controller.end();
    if (event === "hidden") s.controller.setVisible(false);
    if (event === "logout") s.controller.setContext("conversation-1", false);
    if (event === "navigation") s.controller.setContext("conversation-2", true);
    if (event === "dispose") s.controller.dispose();
    late.onComplete("Late request"); late.onState("listening");
    expect(s.transport.submit).not.toHaveBeenCalled();
    expect(s.cancelInput).toHaveBeenCalled();
    if (event === "hidden") { s.controller.setVisible(true); expect(s.provider.listen).toHaveBeenCalledTimes(1); }
    s.controller.dispose();
  });

  it.each(["end", "hidden", "navigation", "logout", "dispose"])("retains in-flight identity and ignores late Agent reply after %s", async event => {
    const s = setup(); let resolve!: (result: VoiceTurnResult) => void;
    vi.mocked(s.transport.submit).mockImplementation(() => new Promise(r => { resolve = r; }));
    s.start(); s.finish(); const pending = s.controller.getSnapshot().unresolved;
    if (event === "end") s.controller.end();
    if (event === "hidden") s.controller.setVisible(false);
    if (event === "navigation") s.controller.setContext("conversation-2", true);
    if (event === "logout") s.controller.setContext("conversation-1", false);
    if (event === "dispose") s.controller.dispose();
    resolve({ outcome: "acknowledged", reply: "Late reply" }); await Promise.resolve();
    expect(s.provider.speak).not.toHaveBeenCalled();
    expect(vi.mocked(s.transport.submit).mock.calls[0][0].signal.aborted).toBe(true);
    if (event === "navigation") s.controller.setContext("conversation-1", true);
    expect(s.controller.getSnapshot().unresolved).toEqual(pending);
    s.controller.dispose();
  });

  it("Stop speaking pauses and rejects late playback without restarting the microphone", async () => {
    const s = setup(); s.start(); s.finish(); await Promise.resolve();
    const late = s.output(); s.controller.stopSpeaking(); late.onEnd();
    expect(s.cancelOutput).toHaveBeenCalled();
    expect(s.provider.listen).toHaveBeenCalledTimes(1);
    expect(s.controller.getSnapshot().state).toBe("paused");
    s.controller.start(); expect(s.provider.listen).toHaveBeenCalledTimes(2);
    s.controller.dispose();
  });

  it.each(["end", "hidden", "navigation", "logout", "dispose"])("never resumes from a stale playback end after %s", async event => {
    const s = setup(); s.start(); s.finish(); await Promise.resolve();
    const late = s.output();
    if (event === "end") s.controller.end();
    if (event === "hidden") s.controller.setVisible(false);
    if (event === "navigation") s.controller.setContext("conversation-2", true);
    if (event === "logout") s.controller.setContext("conversation-1", false);
    if (event === "dispose") s.controller.dispose();
    late.onStart(); late.onEnd(); late.onError({ code: "failed", message: "Late failure" });
    expect(s.cancelOutput).toHaveBeenCalled();
    expect(s.provider.listen).toHaveBeenCalledTimes(1);
    expect(s.controller.getSnapshot().sessionActive).toBe(false);
    s.controller.dispose();
  });

  it("turns off playback and continues successive turns when explicitly selected", async () => {
    const s = setup(); s.controller.setPlayback(false); s.start(); s.finish(); await Promise.resolve();
    expect(s.provider.speak).not.toHaveBeenCalled();
    expect(s.provider.listen).toHaveBeenCalledTimes(2);
    s.controller.dispose();
  });

  it("missing playback capability leaves reply text and continues the enabled session", async () => {
    const s = setup(false); s.start(); s.finish(); await Promise.resolve();
    expect(s.provider.speak).not.toHaveBeenCalled();
    expect(s.controller.getSnapshot()).toMatchObject({ playback: false, reply: "Review risk first.", state: "listening" });
    s.controller.dispose();
  });

  it("retains reply text when playback fails and permits typed fallback after microphone denial", async () => {
    const s = setup(); s.start(); s.input().onError({ code: "permission", message: "Denied" });
    expect(s.controller.getSnapshot().state).toBe("error");
    s.setDraft("Typed fallback"); expect(s.draft()).toBe("Typed fallback");
    s.setDraft(""); s.controller.start(); s.finish(); await Promise.resolve();
    s.output().onError({ code: "unsupported", message: "No output" });
    expect(s.controller.getSnapshot()).toMatchObject({ reply: "Review risk first.", state: "error", sessionActive: false });
    s.controller.dispose();
  });

  it("restores pending identity from the integrator after a remount", () => {
    const s = setup();
    const pending = { turnKey: "saved-key", conversationId: "saved-conversation", transcript: "Saved speech", origin: "voice" as const };
    s.transport.pending = () => pending;
    s.controller.setContext("saved-conversation", true); s.controller.start();
    expect(s.provider.listen).not.toHaveBeenCalled();
    expect(s.controller.getSnapshot().unresolved).toEqual(pending);
    s.controller.dispose();
  });

  it("reconciles a matching terminal proof after navigation without trusting missing storage", async () => {
    const s = setup();
    vi.mocked(s.transport.submit).mockResolvedValue({ outcome: "uncertain" });
    s.start(); s.finish(); await Promise.resolve();
    const turn = s.controller.getSnapshot().unresolved!;
    s.controller.setContext("conversation-2", true);
    s.transport.pending = () => null;
    s.controller.setContext("conversation-1", true);
    expect(s.controller.getSnapshot().unresolved).toEqual(turn);
    s.controller.setContext("conversation-2", true);
    Object.assign(s.transport, { terminal: () => ({ turnKey: "other-key", conversationId: "conversation-1", outcome: "acknowledged" }) });
    s.controller.setContext("conversation-1", true);
    expect(s.controller.getSnapshot().unresolved).toEqual(turn);
    s.controller.setContext("conversation-2", true);
    Object.assign(s.transport, { terminal: () => ({ turnKey: turn.turnKey, conversationId: turn.conversationId, outcome: "acknowledged" }) });
    s.controller.setContext("conversation-1", true);
    expect(s.controller.getSnapshot().unresolved).toBeNull();
    s.controller.start();
    expect(s.provider.listen).toHaveBeenCalledTimes(2);
    s.controller.dispose();
  });
});
