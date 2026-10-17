import { useState } from "react";
import { createRoot } from "react-dom/client";
import { VoiceConversationControls } from "@/components/agent/VoiceConversationControls";
import { createBrowserVoiceProvider, type BrowserRecognition } from "@/lib/voice/browser-voice-provider";
import type { VoiceAgentTransport, VoiceTurn } from "@/lib/voice/conversation-types";
import "@/app/globals.css";

type Events = {
  recognition: BrowserRecognition | null;
  speech: SpeechSynthesisUtterance | null;
  submissions: VoiceTurn[];
  recoveries: VoiceTurn[];
  listens: number;
  result: "acknowledged" | "uncertain";
  finish(text: string): void;
  partial(text: string): void;
  playbackEnd(): void;
  deny(): void;
};
declare global { interface Window { voiceHarness: Events } }

const fixture: Events = {
  recognition: null, speech: null, submissions: [], recoveries: [], listens: 0, result: "acknowledged",
  partial(text) { fixture.recognition?.onresult?.({ results: [{ isFinal: false, 0: { transcript: text } }] }); },
  finish(text) {
    fixture.recognition?.onresult?.({ results: [{ isFinal: true, 0: { transcript: text } }] });
    fixture.recognition?.onend?.();
  },
  playbackEnd() { fixture.speech?.onend?.(new Event("end") as SpeechSynthesisEvent); },
  deny() { fixture.recognition?.onerror?.({ error: "not-allowed" }); },
};
window.voiceHarness = fixture;

class Recognition implements BrowserRecognition {
  lang = ""; continuous = false; interimResults = true;
  onstart: BrowserRecognition["onstart"] = null;
  onend: BrowserRecognition["onend"] = null;
  onresult: BrowserRecognition["onresult"] = null;
  onerror: BrowserRecognition["onerror"] = null;
  start() { fixture.recognition = this; fixture.listens++; this.onstart?.(); }
  stop() { this.onend?.(); }
  abort() {}
}
class Utterance {
  lang = "";
  onstart: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public text: string) {}
}
Object.defineProperties(window, {
  SpeechRecognition: { value: Recognition },
  SpeechSynthesisUtterance: { value: Utterance },
  speechSynthesis: { value: {
    speak(utterance: SpeechSynthesisUtterance) { fixture.speech = utterance; utterance.onstart?.(new Event("start") as SpeechSynthesisEvent); },
    cancel() {},
  } },
});

const transport: VoiceAgentTransport = {
  async submit(request) {
    const { turnKey, transcript, conversationId, origin } = request;
    const turn = { turnKey, transcript, conversationId, origin };
    fixture.submissions.push(turn);
    return fixture.result === "uncertain" ? { outcome: "uncertain" }
      : { outcome: "acknowledged", reply: "Review recorded risk before confirming any proposal." };
  },
  async recover(request) {
    const { turnKey, transcript, conversationId, origin } = request;
    const turn = { turnKey, transcript, conversationId, origin };
    fixture.recoveries.push(turn);
    return { outcome: "acknowledged", reply: "Recovered original reply." };
  },
};

function Harness() {
  const [draft, setDraft] = useState("");
  const [conversationId, setConversationId] = useState("fixture-conversation");
  const [authenticated, setAuthenticated] = useState(true);
  return (
    <main style={{ maxWidth: 760, margin: "32px auto", padding: 16, fontFamily: "system-ui" }}>
      <h1 style={{ fontSize: 20, marginBottom: 16 }}>AlphaTrade voice conversation</h1>
      <p style={{ marginBottom: 16, fontSize: 12 }}>Deterministic component harness · no microphone, Agent service or orders.</p>
      <label style={{ display: "block", marginBottom: 8 }}>Typed draft
        <textarea aria-label="Typed draft" value={draft} onChange={event => setDraft(event.target.value)}
          style={{ width: "100%", minHeight: 80, display: "block", padding: 8, background: "var(--color-surface-0)", border: "1px solid var(--color-border)" }} />
      </label>
      <VoiceConversationControls conversationId={conversationId} sessionKey="fixture-user" authenticated={authenticated}
        typedDraft={draft} onTypedDraftChange={setDraft} transport={transport} createProvider={createBrowserVoiceProvider} />
      <details style={{ marginTop: 24 }}><summary>Harness events</summary>
        <button onClick={() => fixture.finish("Review my recorded risk")}>Finish utterance</button>{" "}
        <button onClick={() => fixture.playbackEnd()}>Finish playback</button>{" "}
        <button onClick={() => fixture.deny()}>Deny microphone</button>{" "}
        <button onClick={() => setConversationId("other-conversation")}>Navigate</button>{" "}
        <button onClick={() => setAuthenticated(false)}>Logout</button>
      </details>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<Harness />);
