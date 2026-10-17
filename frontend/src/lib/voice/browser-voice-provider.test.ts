import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createBrowserVoiceProvider,
  VOICE_TIMEOUTS,
  type BrowserRecognition,
} from "./browser-voice-provider";

class Recognition implements BrowserRecognition {
  static instances: Recognition[] = [];
  static failStart = false;
  lang = "";
  continuous = false;
  interimResults = false;
  onstart: BrowserRecognition["onstart"] = null;
  onend: BrowserRecognition["onend"] = null;
  onerror: BrowserRecognition["onerror"] = null;
  onresult: BrowserRecognition["onresult"] = null;
  start = vi.fn(() => {
    if (Recognition.failStart) throw new Error("Unavailable");
  });
  stop = vi.fn();
  abort = vi.fn();
  constructor() {
    Recognition.instances.push(this);
  }
  result(text: string, isFinal = true) {
    this.onresult?.({ results: [{ isFinal, 0: { transcript: text } }] });
  }
}

const callbacks = () => ({
  onState: vi.fn(),
  onTranscript: vi.fn(),
  onComplete: vi.fn(),
  onError: vi.fn(),
});

describe("browser voice transport", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Recognition.instances = [];
    Recognition.failStart = false;
    vi.stubGlobal("SpeechRecognition", Recognition);
    vi.stubGlobal("isSecureContext", true);
  });
  afterEach(() => {
    Reflect.deleteProperty(document, "permissionsPolicy");
    Reflect.deleteProperty(document, "featurePolicy");
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("adds single-utterance completion without changing legacy continuous recording", () => {
    const provider = createBrowserVoiceProvider();
    provider.listen(callbacks());
    expect(Recognition.instances[0].continuous).toBe(true);
    const events = callbacks();
    provider.listen(events, { turnCompletion: "utterance" });
    const recognition = Recognition.instances[1];
    expect(recognition.continuous).toBe(false);
    recognition.result("Partial", false);
    expect(events.onComplete).not.toHaveBeenCalled();
    recognition.result("Completed utterance");
    recognition.result("Completed utterance");
    recognition.onend?.();
    expect(events.onComplete).toHaveBeenCalledExactlyOnceWith("Completed utterance");
    provider.dispose();
  });

  it("aborts recognition before playback and ignores late recognition callbacks", () => {
    class Utterance { constructor(public text: string) {} }
    vi.stubGlobal("SpeechSynthesisUtterance", Utterance);
    const speak = vi.fn();
    vi.stubGlobal("speechSynthesis", { speak, cancel: vi.fn() });
    const provider = createBrowserVoiceProvider();
    const events = callbacks();
    provider.listen(events, { turnCompletion: "utterance" });
    const recognition = Recognition.instances[0];
    const late = recognition.onresult;
    provider.speak("App reply", { onStart: vi.fn(), onEnd: vi.fn(), onError: vi.fn() });
    expect(recognition.abort).toHaveBeenCalledTimes(1);
    expect(recognition.abort.mock.invocationCallOrder[0]).toBeLessThan(speak.mock.invocationCallOrder[0]);
    late?.({ results: [{ isFinal: true, 0: { transcript: "App reply" } }] });
    expect(events.onTranscript).not.toHaveBeenCalled();
    provider.dispose();
  });

  it.each(["permissionsPolicy", "featurePolicy"])(
    "explains a %s microphone block before requesting capture or recognition",
    (name) => {
      const allowsFeature = vi.fn().mockReturnValue(false);
      Object.defineProperty(document, name, {
        value: { allowsFeature }, configurable: true,
      });
      const getUserMedia = vi.fn();
      vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
      const provider = createBrowserVoiceProvider();
      const events = callbacks();
      provider.listen(events);
      provider.diagnoseMicrophone?.(events);
      expect(events.onError).toHaveBeenCalledTimes(2);
      expect(events.onError).toHaveBeenLastCalledWith(expect.objectContaining({
        sourceCode: "permissions-policy",
        message: expect.stringContaining("deployed Permissions-Policy header"),
      }));
      expect(allowsFeature).toHaveBeenCalledWith("microphone");
      expect(getUserMedia).not.toHaveBeenCalled();
      expect(Recognition.instances).toHaveLength(0);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("reports permission, recording and transcription; completes only final speech at end", () => {
    const events = callbacks();
    const provider = createBrowserVoiceProvider();
    const session = provider.listen(events);
    const recognition = Recognition.instances[0];
    expect(events.onState).toHaveBeenLastCalledWith("requesting");
    recognition.onstart?.();
    expect(events.onState).toHaveBeenLastCalledWith("listening");
    recognition.result("Review risk", false);
    expect(events.onTranscript).toHaveBeenCalledWith("Review risk");
    expect(events.onComplete).not.toHaveBeenCalled();
    session.stop();
    session.stop();
    expect(recognition.stop).toHaveBeenCalledTimes(1);
    expect(events.onState).toHaveBeenLastCalledWith("transcribing");
    recognition.onresult?.({
      results: [
        { isFinal: true, 0: { transcript: " Review risk " } },
        { isFinal: true, 0: { transcript: "and my strategy" } },
        { isFinal: false, 0: { transcript: "unconfirmed words" } },
      ],
    });
    recognition.onend?.();
    expect(events.onComplete).toHaveBeenCalledExactlyOnceWith(
      "Review risk and my strategy",
    );
    expect(vi.getTimerCount()).toBe(0);
    provider.dispose();
  });

  it.each([
    ["not-allowed", "permission"],
    ["service-not-allowed", "service"],
    ["language-not-supported", "language"],
    ["audio-capture", "unavailable"],
    ["network", "network"],
    ["no-speech", "no-speech"],
    ["aborted", "failed"],
  ])("handles %s and ignores late results", (browserError, code) => {
    const events = callbacks();
    createBrowserVoiceProvider().listen(events);
    const recognition = Recognition.instances[0];
    const late = recognition.onresult;
    recognition.onerror?.({ error: browserError });
    late?.({ results: [{ isFinal: true, 0: { transcript: "Late request" } }] });
    expect(events.onError).toHaveBeenCalledWith(
      expect.objectContaining({ code, sourceCode: browserError }),
    );
    expect(recognition.abort).toHaveBeenCalled();
    expect(events.onTranscript).not.toHaveBeenCalled();
    expect(events.onComplete).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not infer microphone denial from recognition denial and omits unsafe codes", () => {
    const events = callbacks();
    const provider = createBrowserVoiceProvider();
    for (const code of [
      "not-allowed",
      "service-not-allowed",
      "unexpected provider secret",
    ]) {
      provider.listen(events);
      Recognition.instances.at(-1)?.onerror?.({ error: code });
      const failure = events.onError.mock.lastCall?.[0];
      expect(failure.message).not.toContain("Microphone permission was denied");
      expect(failure.message).not.toContain("unexpected provider secret");
      expect(failure.sourceCode).toBe(
        code.startsWith("unexpected") ? undefined : code,
      );
    }
  });

  it("recovers from a service rejection with a new gesture and browser language", () => {
    const provider = createBrowserVoiceProvider();
    const events = callbacks();
    provider.listen(events);
    const first = Recognition.instances[0];
    first.onerror?.({ error: "service-not-allowed" });
    expect(first.onstart).toBeNull();
    expect(first.onend).toBeNull();
    provider.listen(events);
    const retry = Recognition.instances[1];
    expect(retry.start).toHaveBeenCalledTimes(1);
    expect(retry.lang).toBe(navigator.language);
    retry.onstart?.();
    retry.result("Recovered transcript");
    retry.onend?.();
    expect(events.onComplete).toHaveBeenCalledExactlyOnceWith(
      "Recovered transcript",
    );
    provider.dispose();
    expect(retry.abort).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("checks audio capture independently and immediately stops every track", async () => {
    const tracks = [{ stop: vi.fn() }, { stop: vi.fn() }];
    const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => tracks });
    vi.stubGlobal("navigator", {
      mediaDevices: { getUserMedia },
      language: "en-US",
    });
    const events = { onComplete: vi.fn(), onError: vi.fn() };
    createBrowserVoiceProvider().diagnoseMicrophone?.(events);
    expect(getUserMedia).toHaveBeenCalledExactlyOnceWith({
      audio: true,
      video: false,
    });
    await Promise.resolve();
    expect(tracks.every((track) => track.stop.mock.calls.length === 1)).toBe(
      true,
    );
    expect(events.onComplete).toHaveBeenCalledTimes(1);
    expect(events.onError).not.toHaveBeenCalled();
    expect(Recognition.instances).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ["NotAllowedError", "permission"],
    ["SecurityError", "permission"],
    ["NotFoundError", "unavailable"],
    ["NotReadableError", "unavailable"],
    ["OverconstrainedError", "unavailable"],
    ["AbortError", "unavailable"],
  ])(
    "classifies local capture %s separately from speech service failure",
    async (name, code) => {
      const getUserMedia = vi
        .fn()
        .mockRejectedValue(new DOMException("private device details", name));
      vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
      const events = { onComplete: vi.fn(), onError: vi.fn() };
      createBrowserVoiceProvider().diagnoseMicrophone?.(events);
      await Promise.resolve();
      expect(events.onError).toHaveBeenCalledWith(
        expect.objectContaining({ code, sourceCode: name }),
      );
      expect(events.onError.mock.lastCall?.[0].message).not.toContain(
        "private device details",
      );
    },
  );

  it.each(["cancel", "dispose", "timeout"])(
    "stops capture resolved after %s without a success callback",
    async (action) => {
      let finish!: (stream: MediaStream) => void;
      const getUserMedia = vi.fn(
        () =>
          new Promise<MediaStream>((resolve) => {
            finish = resolve;
          }),
      );
      vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
      const events = { onComplete: vi.fn(), onError: vi.fn() };
      const provider = createBrowserVoiceProvider();
      const session = provider.diagnoseMicrophone?.(events);
      if (action === "cancel") session?.cancel();
      if (action === "dispose") provider.dispose();
      if (action === "timeout")
        vi.advanceTimersByTime(VOICE_TIMEOUTS.permission);
      const stop = vi.fn();
      finish({ getTracks: () => [{ stop }] } as unknown as MediaStream);
      await Promise.resolve();
      expect(stop).toHaveBeenCalledTimes(1);
      expect(events.onComplete).not.toHaveBeenCalled();
      expect(events.onError).toHaveBeenCalledTimes(
        action === "timeout" ? 1 : 0,
      );
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("bounds the permission wait and can retry", () => {
    const provider = createBrowserVoiceProvider();
    const events = callbacks();
    provider.listen(events);
    vi.advanceTimersByTime(VOICE_TIMEOUTS.permission);
    expect(events.onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "timeout" }),
    );
    provider.listen(callbacks());
    expect(Recognition.instances).toHaveLength(2);
    provider.dispose();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("finalizes at the recording limit and bounds a stalled transcription", () => {
    const events = callbacks();
    createBrowserVoiceProvider().listen(events);
    Recognition.instances[0].onstart?.();
    vi.advanceTimersByTime(VOICE_TIMEOUTS.recording);
    expect(Recognition.instances[0].stop).toHaveBeenCalledTimes(1);
    expect(events.onState).toHaveBeenLastCalledWith("transcribing");
    vi.advanceTimersByTime(VOICE_TIMEOUTS.transcription);
    expect(events.onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "timeout" }),
    );
    expect(events.onComplete).not.toHaveBeenCalled();
  });

  it("rejects an empty or interim-only transcript", () => {
    const events = callbacks();
    createBrowserVoiceProvider().listen(events);
    Recognition.instances[0].result("interim only", false);
    Recognition.instances[0].onend?.();
    expect(events.onComplete).not.toHaveBeenCalled();
    expect(events.onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "no-speech" }),
    );
  });

  it("cancels recording without completing and ignores delayed callbacks", () => {
    const events = callbacks();
    const session = createBrowserVoiceProvider().listen(events);
    const lateEnd = Recognition.instances[0].onend;
    Recognition.instances[0].result("Do not send this");
    session.cancel();
    lateEnd?.();
    expect(events.onComplete).not.toHaveBeenCalled();
    expect(events.onError).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("supports the prefixed API and reports unsupported or insecure input", () => {
    vi.stubGlobal("SpeechRecognition", undefined);
    vi.stubGlobal("webkitSpeechRecognition", Recognition);
    expect(createBrowserVoiceProvider().capabilities.input).toBe(true);
    vi.stubGlobal("isSecureContext", false);
    const provider = createBrowserVoiceProvider();
    const events = callbacks();
    expect(provider.capabilities.input).toBe(false);
    provider.listen(events);
    expect(events.onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "unsupported" }),
    );
  });

  it("handles a synchronous recognition start failure", () => {
    Recognition.failStart = true;
    const events = callbacks();
    createBrowserVoiceProvider().listen(events);
    expect(events.onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "failed" }),
    );
    expect(Recognition.instances[0].abort).toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ["NotAllowedError", "permission"],
    ["SecurityError", "permission"],
    ["NotSupportedError", "unsupported"],
    ["InvalidStateError", "failed"],
    ["AbortError", "failed"],
  ])(
    "retains a safe synchronous recognition %s without exposing raw messages",
    (name, code) => {
      // Instance field mock is overridden for this browser construction only.
      class FailingRecognition extends Recognition {
        start = vi.fn(() => {
          throw new DOMException("private browser details", name);
        });
      }
      vi.stubGlobal("SpeechRecognition", FailingRecognition);
      const events = callbacks();
      createBrowserVoiceProvider().listen(events);
      expect(events.onError).toHaveBeenCalledWith(
        expect.objectContaining({ code, sourceCode: name }),
      );
      expect(events.onError.mock.lastCall?.[0].message).not.toContain(
        "private browser details",
      );
      expect(Recognition.instances[0].abort).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("stops speech on interruption and ignores late speech events", () => {
    let spoken: SpeechSynthesisUtterance | undefined;
    class Utterance {
      constructor(public text: string) {}
    }
    const synthesis = {
      speak: vi.fn((value: SpeechSynthesisUtterance) => {
        spoken = value;
      }),
      cancel: vi.fn(),
    };
    vi.stubGlobal("SpeechSynthesisUtterance", Utterance);
    vi.stubGlobal("speechSynthesis", synthesis);
    const provider = createBrowserVoiceProvider();
    const events = { onStart: vi.fn(), onEnd: vi.fn(), onError: vi.fn() };
    expect(provider.capabilities.output).toBe(true);
    const session = provider.speak("Agent reply", events);
    spoken?.onstart?.(new Event("start") as SpeechSynthesisEvent);
    expect(events.onStart).toHaveBeenCalledTimes(1);
    const lateEnd = spoken?.onend;
    session.cancel();
    lateEnd?.call(spoken!, new Event("end") as SpeechSynthesisEvent);
    expect(events.onEnd).not.toHaveBeenCalled();
    provider.speak("Another reply", events);
    provider.listen(callbacks());
    expect(synthesis.cancel).toHaveBeenCalled();
    provider.dispose();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("bounds speech playback and handles missing output independently", () => {
    class Utterance {
      constructor(public text: string) {}
    }
    vi.stubGlobal("SpeechSynthesisUtterance", Utterance);
    vi.stubGlobal("speechSynthesis", { speak: vi.fn(), cancel: vi.fn() });
    const events = { onStart: vi.fn(), onEnd: vi.fn(), onError: vi.fn() };
    createBrowserVoiceProvider().speak("Reply", events);
    vi.advanceTimersByTime(VOICE_TIMEOUTS.speech);
    expect(events.onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "timeout" }),
    );
    vi.stubGlobal("speechSynthesis", undefined);
    const provider = createBrowserVoiceProvider();
    expect(provider.capabilities).toEqual({ input: true, output: false });
    provider.speak("Reply", events);
    expect(events.onError).toHaveBeenLastCalledWith(
      expect.objectContaining({ code: "unsupported" }),
    );
    vi.stubGlobal("speechSynthesis", { speak: vi.fn(), cancel: vi.fn() });
    vi.stubGlobal("SpeechSynthesisUtterance", undefined);
    expect(createBrowserVoiceProvider().capabilities.output).toBe(false);
  });
});
