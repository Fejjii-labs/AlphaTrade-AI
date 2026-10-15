import type { VoiceError, VoiceSession } from "./types";
import { AGENT_TURN_TIMEOUT_MS } from "@/lib/agent-turn-budget";
import type {
  VoiceConversationOptions, VoiceConversationSnapshot, VoiceTurn, VoiceTurnResult,
} from "./conversation-types";

export const VOICE_AGENT_TIMEOUT_MS = AGENT_TURN_TIMEOUT_MS;

/** Half-duplex conversation; no Agent APIs, model routing, storage, or tool execution here. */
export class VoiceConversationController {
  private snapshot: VoiceConversationSnapshot;
  private subscribers = new Set<() => void>();
  private conversationId: string | null = null;
  private allowed = false;
  private visible = true;
  private disposed = false;
  private voiceEpoch = 0;
  private agentEpoch = 0;
  private voiceSession: VoiceSession | null = null;
  private abort: AbortController | null = null;
  private deadline: ReturnType<typeof setTimeout> | undefined;
  private pending = new Map<string, VoiceTurn>();

  constructor(private readonly options: VoiceConversationOptions) {
    this.snapshot = {
      state: "idle", sessionActive: false, mode: "review", playback: options.provider.capabilities.output,
      transcript: "", ready: false, reply: "", unresolved: null, error: null,
      capabilities: options.provider.capabilities, startedAt: null,
    };
  }

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.subscribers.add(listener);
    return () => { this.subscribers.delete(listener); };
  };

  private update(change: Partial<VoiceConversationSnapshot>) {
    if (this.disposed) return;
    this.snapshot = { ...this.snapshot, ...change };
    this.subscribers.forEach(listener => listener());
  }

  private cancelVoice() {
    this.voiceEpoch++;
    const session = this.voiceSession;
    this.voiceSession = null;
    session?.cancel();
  }

  private cancelAgent() {
    this.agentEpoch++;
    clearTimeout(this.deadline);
    const abort = this.abort;
    this.abort = null;
    abort?.abort();
    // Aborting a fetch does not prove the server rejected the turn. Keep pending.
  }

  setContext(conversationId: string | null, allowed: boolean) {
    if (this.disposed || (this.conversationId === conversationId && this.allowed === allowed)) return;
    this.cancelVoice();
    this.cancelAgent();
    this.conversationId = conversationId;
    this.allowed = allowed;
    this.reconcilePending();
    const restored = conversationId ? this.options.transport.pending?.(conversationId) : null;
    if (restored && restored.conversationId === conversationId && restored.origin === "voice") {
      this.pending.set(conversationId!, Object.freeze({ ...restored }));
    }
    const unresolved = conversationId ? this.pending.get(conversationId) ?? null : null;
    this.update({ state: unresolved ? "paused" : "idle", sessionActive: false,
      transcript: unresolved?.transcript ?? "", ready: false, reply: "", error: null, startedAt: null, unresolved });
  }

  reconcilePending() {
    if (this.disposed) return;
    for (const [id, turn] of this.pending) {
      // The current request consumes its own result to preserve playback behavior.
      if (id === this.conversationId && this.snapshot.state === "waiting") continue;
      const proof = this.options.transport.terminal?.(turn);
      if (proof?.turnKey === turn.turnKey && proof.conversationId === turn.conversationId &&
          (proof.outcome === "acknowledged" || proof.outcome === "rejected")) {
        this.pending.delete(id);
        if (this.snapshot.unresolved?.turnKey === turn.turnKey) {
          this.update({ unresolved: null, state: "paused", sessionActive: false,
            transcript: "", ready: false, error: null });
        }
      }
    }
  }

  setVisible(visible: boolean) {
    this.visible = visible;
    if (!visible) this.pause();
  }

  setMode(mode: VoiceConversationSnapshot["mode"]) {
    if (this.snapshot.sessionActive) this.pause();
    this.update({ mode });
  }

  setPlayback(playback: boolean) {
    this.update({ playback });
    if (!playback && this.snapshot.state === "speaking") this.stopSpeaking();
  }

  private canAct() {
    return !this.disposed && this.allowed && this.visible && this.conversationId !== null;
  }

  /** Only call in response to a user gesture. Automatic continuation uses listen below. */
  start() {
    if (!this.canAct() || this.snapshot.unresolved || this.snapshot.ready ||
        ["requesting", "listening", "preparing", "waiting", "speaking"].includes(this.snapshot.state)) return;
    this.update({ sessionActive: true, error: null });
    this.listen();
  }

  private voiceFailure(error: VoiceError, playback = false) {
    this.cancelVoice();
    this.update({ state: "error", sessionActive: false, startedAt: null,
      error: { message: playback ? "Playback unavailable. Read the reply or retry voice." : error.code === "no-speech"
        ? "No speech detected. Try again or type your message."
        : "Voice unavailable. Retry or type your message.", diagnostic: error } });
  }

  private listen() {
    if (!this.canAct() || !this.snapshot.sessionActive || this.snapshot.unresolved) return;
    this.cancelVoice();
    const epoch = this.voiceEpoch;
    const current = () => !this.disposed && epoch === this.voiceEpoch;
    this.update({ state: "requesting", transcript: "", ready: false,
      error: null, startedAt: Date.now() });
    try {
      const session = this.options.provider.listen({
        onState: state => {
          if (current()) this.update({ state: state === "transcribing" ? "preparing" : state });
        },
        onTranscript: transcript => { if (current()) this.update({ transcript }); },
        onComplete: text => {
          if (!current()) return;
          this.cancelVoice(); // Latch this utterance before any transport call.
          const transcript = text.trim();
          if (!transcript) {
            this.voiceFailure({ code: "no-speech", message: "Empty utterance" });
            return;
          }
          this.update({ state: "preparing", transcript, ready: true, startedAt: null });
          if (this.snapshot.mode === "conversation" && !this.options.getTypedDraft().trim()) this.send();
        },
        onError: error => { if (current()) this.voiceFailure(error); },
      }, this.snapshot.mode === "conversation" ? { turnCompletion: "utterance" } : undefined);
      if (current()) this.voiceSession = session;
      else session.cancel();
    } catch {
      if (current()) this.voiceFailure({ code: "failed", message: "Speech provider could not start." });
    }
  }

  stopRecording() {
    if (!["requesting", "listening"].includes(this.snapshot.state)) return;
    this.update({ state: "preparing" });
    this.voiceSession?.stop();
  }

  editTranscript(transcript: string) {
    if (this.snapshot.ready && !this.snapshot.unresolved) this.update({ transcript });
  }

  applyTranscript(action: "append" | "replace") {
    if (!this.canAct() || !this.snapshot.ready || this.snapshot.unresolved) return;
    const text = this.snapshot.transcript.trim();
    if (!text) return;
    const draft = this.options.getTypedDraft();
    this.options.setTypedDraft(action === "replace" ? text : `${draft}${draft ? "\n" : ""}${text}`);
    this.cancelTranscript();
  }

  cancelTranscript() {
    if (this.snapshot.unresolved) return;
    this.cancelVoice();
    this.update({ state: "idle", sessionActive: false, transcript: "", ready: false, startedAt: null });
  }

  send() {
    if (!this.canAct() || !this.snapshot.ready || this.snapshot.unresolved) return;
    const transcript = this.snapshot.transcript.trim();
    if (!transcript) return;
    const turn: VoiceTurn = Object.freeze({
      turnKey: this.options.createTurnKey?.() ?? crypto.randomUUID(),
      transcript, conversationId: this.conversationId!, origin: "voice",
    });
    this.pending.set(turn.conversationId, turn);
    this.update({ unresolved: turn, ready: false });
    this.request(turn, false);
  }

  recover() {
    if (!this.canAct() || !this.snapshot.unresolved || this.snapshot.state === "waiting") return;
    this.request(this.snapshot.unresolved, true);
  }

  private request(turn: VoiceTurn, recovery: boolean) {
    this.cancelVoice();
    this.cancelAgent();
    const epoch = this.agentEpoch;
    const current = () => !this.disposed && epoch === this.agentEpoch;
    const abort = new AbortController();
    this.abort = abort;
    this.update({ state: "waiting", error: null, startedAt: null });
    const uncertain = () => {
      if (!current()) return;
      this.cancelAgent();
      this.update({ state: "paused", sessionActive: false,
        error: { message: "Reply uncertain. Recover the original request." } });
      this.reconcilePending();
    };
    this.deadline = setTimeout(uncertain, this.options.agentTimeoutMs ?? VOICE_AGENT_TIMEOUT_MS);
    const complete = (result: VoiceTurnResult) => {
      if (!current()) return;
      // Treat malformed successful envelopes as uncertain, including from JS adapters.
      if (!result || !["acknowledged", "rejected", "uncertain"].includes(result.outcome) ||
          (result.outcome === "acknowledged" && typeof result.reply !== "string")) {
        uncertain();
        return;
      }
      if (result.outcome === "uncertain") { uncertain(); return; }
      clearTimeout(this.deadline);
      this.agentEpoch++;
      this.abort = null;
      this.pending.delete(turn.conversationId);
      this.update({ unresolved: null });
      if (result.outcome === "rejected") {
        this.update({ state: "error", sessionActive: false, ready: true,
          transcript: turn.transcript,
          error: { message: "Request rejected. Edit the transcript or use typed input." } });
        return;
      }
      this.update({ reply: result.reply, transcript: "", ready: false });
      if (this.snapshot.sessionActive && this.snapshot.mode === "conversation" && this.snapshot.playback && result.reply.trim()) {
        this.play(result.reply);
      } else this.continueSession();
    };
    try {
      const request = { ...turn, signal: abort.signal };
      const promise = recovery ? this.options.transport.recover(request) : this.options.transport.submit(request);
      void promise.then(complete, uncertain);
    } catch { uncertain(); }
  }

  private play(reply: string) {
    this.cancelVoice();
    const epoch = this.voiceEpoch;
    const current = () => !this.disposed && epoch === this.voiceEpoch;
    this.update({ state: "speaking", startedAt: Date.now() });
    try {
      const session = this.options.provider.speak(reply, {
        onStart: () => {},
        onEnd: () => {
          if (!current()) return;
          this.cancelVoice();
          this.continueSession();
        },
        onError: error => { if (current()) this.voiceFailure(error, true); },
      });
      if (current()) this.voiceSession = session;
      else session.cancel();
    } catch {
      if (current()) this.voiceFailure({ code: "failed", message: "Playback could not start." }, true);
    }
  }

  private continueSession() {
    if (this.canAct() && this.snapshot.sessionActive && this.snapshot.mode === "conversation") this.listen();
    else this.update({ state: "idle", sessionActive: false, startedAt: null });
  }

  /** Browser voice has no reliable acoustic barge-in. Interrupt explicitly and pause. */
  stopSpeaking() {
    if (this.snapshot.state === "speaking") this.pause();
  }

  pause() {
    this.cancelVoice();
    this.cancelAgent();
    this.update({ state: "paused", sessionActive: false, startedAt: null });
  }

  end() {
    this.pause();
    this.update({ state: this.snapshot.unresolved ? "paused" : "idle",
      transcript: this.snapshot.ready ? this.snapshot.transcript : "" });
  }

  dispose() {
    if (this.disposed) return;
    this.end();
    this.disposed = true;
    this.subscribers.clear();
    this.options.provider.dispose();
  }
}
