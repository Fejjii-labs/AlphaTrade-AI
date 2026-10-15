export type VoiceInputState =
  "idle" | "requesting" | "listening" | "transcribing";

export type VoiceErrorCode =
  | "unsupported"
  | "permission"
  | "service"
  | "language"
  | "unavailable"
  | "network"
  | "no-speech"
  | "timeout"
  | "failed";

// Only allowlisted browser codes are retained; never expose a provider's raw message.
export type VoiceError = {
  code: VoiceErrorCode;
  message: string;
  sourceCode?: string;
};
export type VoiceSession = { stop(): void; cancel(): void };
export type VoiceListenOptions = {
  /** A single browser utterance ends on the browser's silence boundary. */
  turnCompletion?: "utterance";
};

/** Transport only: providers never know about Agent APIs, tools, or authority. */
export interface VoiceProvider {
  readonly capabilities: { input: boolean; output: boolean };
  diagnoseMicrophone?(callbacks: {
    onComplete(): void;
    onError(error: VoiceError): void;
  }): VoiceSession;
  listen(callbacks: {
    onState(state: Exclude<VoiceInputState, "idle">): void;
    onTranscript(text: string): void;
    onComplete(text: string): void;
    onError(error: VoiceError): void;
  }, options?: VoiceListenOptions): VoiceSession;
  speak(
    text: string,
    callbacks: {
      onStart(): void;
      onEnd(): void;
      onError(error: VoiceError): void;
    },
  ): VoiceSession;
  dispose(): void;
}

export type VoiceProviderFactory = () => VoiceProvider;
