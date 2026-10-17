import type { VoiceAgentTransport, VoiceTurn, VoiceTurnResult } from "@/lib/voice/conversation-types";
import { pendingFor, pendingTurn, subscribeRecovery, terminalFor, type PendingTurn } from "./turn-recovery";

/** Adapter only. Both voice methods enter AgentWorkspace's existing shared request pipeline. */
export function createAgentVoiceTransport(
  scope: string,
  run: (request: VoiceTurn & { signal: AbortSignal }, recovery?: PendingTurn) => Promise<VoiceTurnResult>,
): VoiceAgentTransport {
  return {
    submit: request => run(request),
    recover: request => {
      const saved = pendingTurn(scope, request.turnKey);
      // Never reconstruct the complete body from today's composer/context.
      if (!saved?.voice || saved.voice.conversationId !== request.conversationId ||
          saved.voice.transcript !== request.transcript) return Promise.resolve({ outcome: "uncertain" });
      return run(request, saved);
    },
    pending: id => pendingFor(scope, id)?.voice ?? null,
    terminal: turn => terminalFor(scope, turn.turnKey),
    subscribe: listener => subscribeRecovery(scope, listener),
  };
}
