import type { VoiceError, VoiceProvider, VoiceSession } from "./types";

// The prefixed Web Speech API is not included in TypeScript's DOM library.
export interface BrowserRecognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onresult:
    | ((event: {
        results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
      }) => void)
    | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type SpeechWindow = Window & {
  SpeechRecognition?: new () => BrowserRecognition;
  webkitSpeechRecognition?: new () => BrowserRecognition;
  SpeechSynthesisUtterance?: typeof SpeechSynthesisUtterance;
};

export const VOICE_TIMEOUTS = {
  permission: 12_000,
  recording: 60_000,
  transcription: 8_000,
  speech: 120_000,
};

const RECOGNITION_CODES = new Set([
  "not-allowed",
  "service-not-allowed",
  "audio-capture",
  "network",
  "no-speech",
  "aborted",
  "language-not-supported",
  "bad-grammar",
  "phrases-not-supported",
]);

function recognitionError(rawCode: string): VoiceError {
  const sourceCode = RECOGNITION_CODES.has(rawCode) ? rawCode : undefined;
  const detail = (code: VoiceError["code"], message: string): VoiceError => ({
    code,
    message,
    ...(sourceCode ? { sourceCode } : {}),
  });
  switch (sourceCode) {
    case "not-allowed":
      return detail(
        "permission",
        "The browser rejected speech recognition access. This can involve microphone access or the speech service. Check microphone capture separately, then review browser and system permissions.",
      );
    case "service-not-allowed":
      return detail(
        "service",
        "The browser rejected its speech recognition service. Microphone permission may still be allowed. Check microphone capture separately; review browser policy or use a working browser or typed input.",
      );
    case "audio-capture":
      return detail(
        "unavailable",
        "Speech recognition could not capture audio. Check the selected microphone, connection and whether another app is using it.",
      );
    case "network":
      return detail(
        "network",
        "Speech recognition could not connect to its service. Check your connection, VPN and browser policy, then try again.",
      );
    case "language-not-supported":
      return detail(
        "language",
        "The browser speech service does not support the browser's selected language. Check browser language settings or type your message.",
      );
    case "no-speech":
      return detail(
        "no-speech",
        "No speech was detected. Try recording again.",
      );
    default:
      return detail(
        "failed",
        "Speech recognition failed. Try again or type your message.",
      );
  }
}

function recognitionException(error: unknown): VoiceError {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return { ...recognitionError("not-allowed"), sourceCode: name };
  }
  if (name === "NotSupportedError") {
    return {
      code: "unsupported",
      sourceCode: name,
      message:
        "The browser could not start speech recognition. Use a supported browser over HTTPS or type your message.",
    };
  }
  if (name === "InvalidStateError" || name === "AbortError") {
    return { ...recognitionError("failed"), sourceCode: name };
  }
  return recognitionError("failed");
}

function captureError(error: unknown): VoiceError {
  // Only the DOM error name is inspected; raw messages/device details are discarded.
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return {
      code: "permission",
      sourceCode: name,
      message:
        "Microphone capture was denied or blocked. Review browser and system microphone permissions and site policy, then retry the check.",
    };
  }
  if (
    [
      "NotFoundError",
      "NotReadableError",
      "OverconstrainedError",
      "AbortError",
    ].includes(name)
  ) {
    return {
      code: "unavailable",
      sourceCode: name,
      message:
        "Microphone capture is unavailable. Check your selected audio input, connection and other apps using the device, then retry.",
    };
  }
  return {
    code: "failed",
    message:
      "Microphone capture could not be checked. Try again or type your message.",
  };
}

function microphonePolicyError(): VoiceError | null {
  if (typeof document === "undefined") return null;
  const context = document as Document & {
    permissionsPolicy?: { allowsFeature(feature: string): boolean };
    featurePolicy?: { allowsFeature(feature: string): boolean };
  };
  const policy = context.permissionsPolicy ?? context.featurePolicy;
  if (policy && !policy.allowsFeature("microphone")) {
    return {
      code: "permission",
      sourceCode: "permissions-policy",
      message:
        "This page's Permissions Policy blocks microphone capture. Open the app directly in its own HTTPS tab; if it still fails, check the deployed Permissions-Policy header. Browser and system permission toggles cannot override this policy.",
    };
  }
  return null;
}

/** Browser-managed speech; no provider credentials, audio storage, or Agent calls. */
export function createBrowserVoiceProvider(): VoiceProvider {
  const browser =
    typeof window === "undefined" ? undefined : (window as SpeechWindow);
  const Recognition =
    browser?.SpeechRecognition ?? browser?.webkitSpeechRecognition;
  const synthesis = browser?.speechSynthesis;
  const Utterance = browser?.SpeechSynthesisUtterance;
  const input =
    typeof Recognition === "function" && browser?.isSecureContext !== false;
  const output = Boolean(synthesis && typeof Utterance === "function");
  let cancelInput: (() => void) | undefined;
  let cancelOutput: (() => void) | undefined;
  let cancelCapture: (() => void) | undefined;

  return {
    capabilities: { input, output },
    diagnoseMicrophone(callbacks) {
      cancelCapture?.();
      cancelInput?.();
      cancelOutput?.();
      const capture = browser?.navigator.mediaDevices;
      if (!capture?.getUserMedia || browser?.isSecureContext === false) {
        callbacks.onError({
          code: "unsupported",
          message:
            "Microphone capture checks require a supported browser over HTTPS.",
        });
        return { stop() {}, cancel() {} };
      }
      const policyError = microphonePolicyError();
      if (policyError) {
        callbacks.onError(policyError);
        return { stop() {}, cancel() {} };
      }
      let active = true;
      const cancel = () => {
        active = false;
        clearTimeout(timer);
      };
      const timer = setTimeout(() => {
        if (!active) return;
        cancel();
        callbacks.onError({
          code: "timeout",
          message:
            "Microphone check timed out. Dismiss any pending permission prompt. Any capture that opens later will be stopped immediately.",
        });
      }, VOICE_TIMEOUTS.permission);
      cancelCapture = cancel;
      const success = (stream: MediaStream) => {
        // Always stop every track, including after cancellation, timeout or disposal.
        stream.getTracks().forEach((track) => track.stop());
        if (!active) return;
        cancel();
        callbacks.onComplete();
      };
      const failure = (error: unknown) => {
        if (!active) return;
        cancel();
        callbacks.onError(captureError(error));
      };
      try {
        // Called directly by the button gesture; no recording, upload or recognition.
        void capture
          .getUserMedia({ audio: true, video: false })
          .then(success, failure);
      } catch (error) {
        failure(error);
      }
      return { stop: cancel, cancel };
    },
    listen(callbacks, options) {
      cancelCapture?.();
      cancelInput?.();
      cancelOutput?.();
      const noop: VoiceSession = { stop() {}, cancel() {} };
      if (!input || !Recognition) {
        callbacks.onError({
          code: "unsupported",
          message:
            "Voice input is unavailable in this browser. Use a supported browser over HTTPS or type your message.",
        });
        return noop;
      }
      const policyError = microphonePolicyError();
      if (policyError) {
        callbacks.onError(policyError);
        return noop;
      }
      let recognition: BrowserRecognition;
      try {
        recognition = new Recognition();
      } catch (error) {
        callbacks.onError(recognitionException(error));
        return noop;
      }
      let active = true;
      let stopping = false;
      let finalText = "";
      let timer: ReturnType<typeof setTimeout>;
      const cleanup = () => {
        active = false;
        clearTimeout(timer);
        recognition.onstart =
          recognition.onend =
          recognition.onerror =
          recognition.onresult =
            null;
      };
      const abort = () => {
        if (!active) return;
        cleanup();
        try {
          recognition.abort();
        } catch {
          /* Already ended. */
        }
      };
      const fail = (error: VoiceError) => {
        if (!active) return;
        abort();
        callbacks.onError(error);
      };
      const deadline = (ms: number, message: string) => {
        clearTimeout(timer);
        timer = setTimeout(() => fail({ code: "timeout", message }), ms);
      };
      const stop = () => {
        if (!active || stopping) return;
        stopping = true;
        callbacks.onState("transcribing");
        deadline(
          VOICE_TIMEOUTS.transcription,
          "Transcription timed out. Nothing was sent. Try recording again.",
        );
        try {
          recognition.stop();
        } catch {
          fail(recognitionError("failed"));
        }
      };
      cancelInput = abort;
      recognition.lang = browser?.navigator.language || "en-US";
      recognition.continuous = options?.turnCompletion !== "utterance";
      recognition.interimResults = true;
      recognition.onstart = () => {
        if (!active || stopping) return;
        callbacks.onState("listening");
        clearTimeout(timer);
        // At the recording limit, finalize captured speech instead of dropping it.
        timer = setTimeout(stop, VOICE_TIMEOUTS.recording);
      };
      recognition.onresult = (event) => {
        if (!active) return;
        const results = Array.from(event.results);
        finalText = results
          .filter((result) => result.isFinal)
          .map((result) => result[0].transcript.trim())
          .join(" ")
          .trim();
        callbacks.onTranscript(
          results
            .map((result) => result[0].transcript.trim())
            .join(" ")
            .trim(),
        );
      };
      recognition.onerror = (event) => fail(recognitionError(event.error));
      recognition.onend = () => {
        if (!active) return;
        cleanup();
        if (finalText) callbacks.onComplete(finalText);
        else callbacks.onError(recognitionError("no-speech"));
      };
      callbacks.onState("requesting");
      deadline(
        VOICE_TIMEOUTS.permission,
        "Speech recognition did not start in time. Nothing was sent. Check microphone capture separately and review browser speech service availability.",
      );
      try {
        recognition.start();
      } catch (error) {
        fail(recognitionException(error));
      }
      return { stop, cancel: abort };
    },
    speak(text, callbacks) {
      cancelCapture?.();
      cancelInput?.();
      cancelOutput?.();
      const noop: VoiceSession = { stop() {}, cancel() {} };
      if (!output || !synthesis || !browser || !Utterance) {
        callbacks.onError({
          code: "unsupported",
          message:
            "Speech output is unavailable in this browser. The Agent reply is available as text.",
        });
        return noop;
      }
      const utterance = new Utterance(text);
      utterance.lang = browser.navigator.language || "en-US";
      let active = true;
      const cleanup = () => {
        active = false;
        clearTimeout(timer);
        utterance.onstart = utterance.onend = utterance.onerror = null;
      };
      const cancel = () => {
        if (!active) return;
        cleanup();
        synthesis.cancel();
      };
      cancelOutput = cancel;
      const fail = (error: VoiceError) => {
        if (!active) return;
        cancel();
        callbacks.onError(error);
      };
      const timer = setTimeout(
        () =>
          fail({
            code: "timeout",
            message:
              "Speech playback timed out. You can read the full reply in the conversation.",
          }),
        VOICE_TIMEOUTS.speech,
      );
      utterance.onstart = () => {
        if (active) callbacks.onStart();
      };
      utterance.onend = () => {
        if (active) {
          cleanup();
          callbacks.onEnd();
        }
      };
      utterance.onerror = () =>
        fail({
          code: "failed",
          message:
            "Speech playback failed. The Agent reply is available as text.",
        });
      try {
        synthesis.speak(utterance);
      } catch {
        fail({
          code: "failed",
          message:
            "Speech playback failed. The Agent reply is available as text.",
        });
      }
      return { stop: cancel, cancel };
    },
    dispose() {
      cancelCapture?.();
      cancelInput?.();
      cancelOutput?.();
    },
  };
}
