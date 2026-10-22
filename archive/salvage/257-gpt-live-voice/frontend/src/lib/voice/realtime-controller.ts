import { agentVoiceConfig } from "@/lib/api/generated/client";
import { getAccessToken } from "@/lib/auth/session";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";
import { appConfig } from "@/lib/config";
import { RealtimeAudio } from "./realtime-audio";
import type { AgentTurnResult } from "@/lib/api/types";
import type { TurnBody } from "@/components/agent/turn-recovery";

type State = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "error";
type Provider = "gpt_live" | "realtime";
export type RealtimeSnapshot = Readonly<{
  state: State; active: boolean; error: string | null; transcript: string;
  provider?: Provider; inputCaption?: string; replyCaption?: string; outputMuted?: boolean;
}>;
type AudioDevice = Pick<RealtimeAudio, "start" | "play" | "interrupt" | "stop">;
type VoiceConfiguration = {
  available: boolean; reason?: string | null; maximum_seconds?: number | null;
  provider?: Provider; transport?: "websocket_pcm24";
};
type Options = {
  ensureConversation(signal: AbortSignal): Promise<string | null>;
  context: { symbol?: string; strategy_id?: string };
  pending(key: string, body: TurnBody): boolean;
  result(key: string, result: AgentTurnResult): boolean;
  changed(snapshot: RealtimeSnapshot): void;
  audio?: AudioDevice;
  connect?: (url: string) => WebSocket;
  configuration?: (signal: AbortSignal) => Promise<VoiceConfiguration>;
};

export class RealtimeVoiceController {
  private snapshot: RealtimeSnapshot = { state: "idle", active: false, error: null, transcript: "" };
  private socket: WebSocket | null = null;
  private audio: AudioDevice;
  private controller: AbortController | null = null;
  private generation = 0;
  private conversation: string | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private unsubscribe: () => void;
  private ready = false;
  private disposed = false;
  private provider: Provider = "realtime";
  private outputMuted = false;
  private resumePending = false;
  private closingSockets = new Map<WebSocket, ReturnType<typeof setTimeout>>();

  constructor(private readonly options: Options) {
    this.audio = options.audio ?? new RealtimeAudio();
    this.unsubscribe = onSessionCleared(() => this.end());
  }

  private update(patch: Partial<RealtimeSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    if (!this.disposed) this.options.changed(this.snapshot);
  }

  async start(): Promise<void> {
    if (this.snapshot.active || this.disposed) return;
    const generation = ++this.generation;
    const session = sessionGeneration();
    const abort = new AbortController();
    this.controller = abort;
    const current = () => !this.disposed && generation === this.generation && session === sessionGeneration() && !abort.signal.aborted;
    this.update({ state: "connecting", active: true, error: null, transcript: "", provider: undefined, inputCaption: "", replyCaption: "", outputMuted: false });
    try {
      const config: VoiceConfiguration = await (this.options.configuration ?? (signal => agentVoiceConfig({ signal })))(abort.signal);
      if (!current()) return;
      this.provider = config.provider ?? "realtime";
      this.outputMuted = false;
      this.resumePending = false;
      this.update({ provider: this.provider });
      if (!config.available) throw new Error(this.provider === "gpt_live"
        ? "GPT Live is unavailable. Continue typing or use Record."
        : "Interactive voice is unavailable. Continue typing.");
      const conversation = await this.options.ensureConversation(abort.signal);
      if (!current()) return;
      if (!conversation) throw new Error("Finish or recover the current request before starting voice.");
      this.conversation = conversation;
      await this.audio.start(audio => {
        if (current() && this.ready) this.send({ type: "audio.append", audio });
      });
      if (!current()) { this.audio.stop(); return; }
      const token = getAccessToken();
      if (!token) throw new Error("Sign in to use voice.");
      const url = new URL(this.provider === "gpt_live" ? "/agent/voice/live" : "/agent/voice/realtime", appConfig.apiBaseUrl);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const socket = (this.options.connect ?? (address => new WebSocket(address)))(url.toString());
      this.socket = socket;
      socket.onopen = () => {
        if (current()) this.send({ type: "session.start", access_token: token, conversation_id: conversation, context: this.options.context });
      };
      socket.onmessage = event => {
        if (!current()) return;
        try { this.receive(JSON.parse(String(event.data))); }
        catch { this.fail("Voice reply could not be verified. Recover the saved request or continue typing."); }
      };
      socket.onerror = () => { if (current()) this.fail("Voice connection failed. Recover any pending request or continue typing."); };
      socket.onclose = () => { if (current()) this.fail("Voice ended. Recover any pending request or continue typing."); };
      this.timer = setTimeout(() => { if (current()) this.end(); }, Math.min(config.maximum_seconds ?? 180, 300) * 1000);
    } catch (error) {
      if (current()) this.fail(error && typeof error === "object" && "name" in error && error.name === "NotAllowedError"
        ? "Microphone access was denied. Continue typing."
        : error instanceof Error ? error.message : "Voice unavailable. Continue typing.");
    }
  }

  private receive(event: Record<string, unknown>): void {
    switch (event.type) {
      case "session.ready": this.ready = true; this.update({ state: "listening" }); break;
      case "speech.started": this.update({ state: "listening" }); break;
      case "speech.stopped": this.update({ state: "thinking" }); break;
      case "playback.clear": {
        const position = this.audio.interrupt();
        if (this.provider === "gpt_live") {
          if (event.muted === true) this.outputMuted = true;
          this.update({ state: "listening", outputMuted: this.outputMuted });
        } else if (position) this.send({ type: "playback.truncate", ...position });
        break;
      }
      case "playback.ready":
        if (this.provider !== "gpt_live") throw new Error("Unexpected playback acknowledgement.");
        if (!this.resumePending) break;
        this.outputMuted = false;
        this.resumePending = false;
        this.update({ outputMuted: false });
        break;
      case "audio.delta":
        if (typeof event.audio !== "string" || typeof event.item_id !== "string") throw new Error("Invalid audio.");
        if (this.outputMuted) break;
        this.audio.play(event.audio, event.item_id); this.update({ state: "speaking" }); break;
      case "response.done":
        if (this.provider === "realtime") this.update({ state: "listening" });
        break;
      case "turn.pending": {
        const key = String(event.request_key);
        const body = event.body as TurnBody;
        if (!this.options.pending(key, body)) {
          this.fail("Voice recovery could not be retained. Continue typing."); return;
        }
        this.update({ state: "thinking", transcript: body.message });
        this.send({ type: "turn.accept", request_key: key });
        break;
      }
      case "turn.result":
        if (!this.options.result(String(event.request_key), event.result as AgentTurnResult)) throw new Error("Invalid canonical reply.");
        break;
      case "turn.uncertain": this.fail("The Agent request may have completed. Recover its original request before sending again."); break;
      case "session.error": this.fail("Interactive voice is unavailable. Recover any pending request or continue typing."); break;
      // Provider captions never complete a turn or become the canonical Agent reply.
      case "input.delta":
      case "reply.delta": {
        if (typeof event.text !== "string") throw new Error("Invalid voice caption.");
        const field = event.type === "input.delta" ? "inputCaption" : "replyCaption";
        this.update({ [field]: ((this.snapshot[field] ?? "") + event.text).slice(-4000) });
        break;
      }
      case "session.closed": {
        if (typeof event.finalized !== "boolean") throw new Error("Invalid session close.");
        if (this.socket) this.closeSocket(this.socket);
        this.socket = null;
        const generation = this.generation + 1;
        this.end();
        if (this.provider === "gpt_live" && !event.finalized) this.usageUnconfirmed(generation);
        break;
      }
      case "input.interrupted": this.update({ state: "listening" }); break;
      default: throw new Error("Unknown voice reply.");
    }
  }

  private send(event: Record<string, unknown>): void {
    if (this.socket?.readyState === 1) {
      if (this.socket.bufferedAmount > 192000) { this.fail("Voice connection is too slow. Continue typing."); return; }
      this.socket.send(JSON.stringify(event));
    }
  }

  interrupt(): void {
    const position = this.audio.interrupt();
    if (this.provider === "gpt_live") {
      this.outputMuted = true;
      this.resumePending = false;
      this.send({ type: "response.interrupt" });
    } else this.send({ type: "response.interrupt", ...(position ?? { audio_end_ms: 0 }) });
    if (this.snapshot.active) this.update({ state: "listening", outputMuted: this.outputMuted });
  }

  resume(): void {
    if (this.provider !== "gpt_live" || !this.snapshot.active || !this.outputMuted || this.resumePending) return;
    this.resumePending = true;
    this.audio.interrupt();
    this.send({ type: "playback.resume" });
  }

  setConversation(conversation: string | null): void {
    if (this.conversation !== null && conversation !== this.conversation) this.end();
  }

  end(): void {
    const generation = ++this.generation;
    this.ready = false;
    this.resumePending = false;
    this.outputMuted = false;
    this.controller?.abort(); this.controller = null;
    this.audio.stop();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const socket = this.socket;
    this.socket = null;
    this.conversation = null;
    this.update({ state: "idle", active: false, outputMuted: false });
    if (socket?.readyState === 1 && socket.bufferedAmount <= 192000) {
      if (this.provider === "gpt_live") this.drain(socket, generation);
      else {
        socket.send(JSON.stringify({ type: "session.end" }));
        this.closeSocket(socket);
      }
    }
    else if (socket) this.closeSocket(socket);
  }

  private fail(error: string): void {
    this.end(); this.update({ state: "error", error, active: false });
  }

  private closeSocket(socket: WebSocket): void {
    const timer = this.closingSockets.get(socket);
    if (timer) clearTimeout(timer);
    this.closingSockets.delete(socket);
    socket.onopen = null; socket.onmessage = null; socket.onerror = null; socket.onclose = null;
    socket.close();
  }

  private usageUnconfirmed(generation: number): void {
    if (!this.disposed && generation === this.generation && !this.snapshot.active
      && this.snapshot.state === "idle" && this.snapshot.error === null) {
      this.update({ error: "Voice ended, but final usage is unconfirmed." });
    }
  }

  private drain(socket: WebSocket, generation: number): void {
    // Capture and turn callbacks are already fenced. Only usage finalization is awaited.
    socket.onopen = null;
    socket.onmessage = event => {
      try {
        const message = JSON.parse(String(event.data));
        if (message?.type === "session.closed" && typeof message.finalized === "boolean") {
          if (!message.finalized) this.usageUnconfirmed(generation);
          this.closeSocket(socket);
        }
      } catch { this.closeSocket(socket); }
    };
    socket.onerror = () => this.closeSocket(socket);
    socket.onclose = () => this.closeSocket(socket);
    this.closingSockets.set(socket, setTimeout(() => {
      this.usageUnconfirmed(generation);
      this.closeSocket(socket);
    }, 12000));
    try { socket.send(JSON.stringify({ type: "session.end" })); }
    catch { this.closeSocket(socket); }
  }

  dispose(): void { this.disposed = true; this.end(); this.unsubscribe(); }
}
