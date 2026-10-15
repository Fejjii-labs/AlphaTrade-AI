# Agent voice conversation integration — PR238 handoff

Updated 2026-10-10. Continued from `43996d54052239657cdbace5a93e1098d00956b9`
on `codex/voice-conversation-foundation`. PR238 remains a draft based on PR237
(`bda597c2fffbf1a49beadc64d757100808094ca2`).

## Result and ownership

The actual `/agent` page now mounts one `VoiceConversationControls` beside the
typed composer. Spoken turns enter `AgentWorkspace.runTurn`, the same admission,
validation, attachment, idempotency and acknowledgment pipeline as typed turns.
`agent-voice-transport.ts` adapts the controller to that function; it has no API calls.
The only Agent turn request remains the existing `api.agent.turn` path.

This continuation owns AgentWorkspace, its adapter, local session metadata and
related tests. PR237 retains CI, generated clients and activity views. Backend,
migrations, generated files, CI and activity views are unchanged by the continuation.
No provider activation, model change, deployment, merge or full CI was performed.

## Interaction and lifecycle

Review mode is the default. Record produces an editable transcript; clearing its
value keeps the editor mounted and disables Send, Append and Replace until text is
entered. Sending speech preserves the typed draft. Append and Replace change the
typed draft only through explicit actions.

Conversation mode is explicit opt-in. A completed utterance submits automatically
when the typed draft is empty. A nonempty typed draft routes the utterance to review.
After a terminal acknowledgment, the Agent reply appears immediately in the canonical
thread; history reconciles in the background. Optional browser playback completes
before recognition resumes. Stop speaking pauses; another gesture restarts voice.

Starting voice from a new conversation uses the existing `api.conversations.create`
flow, retaining authorized strategy context. Creation shares admission and the Agent
timeout budget. Navigation, cancellation, logout and hidden tabs prevent a late
creation response from starting capture. The existing creation API has no abort
parameter: a cancelled wait may still leave an empty server conversation.

Typed drafts are stored locally per organization/user and conversation, including
the new-conversation draft. They survive conversation changes and tab reloads.
Pending speech retains its original voice envelope; terminal local evidence retains
its origin. Logout clears drafts, pending metadata and terminal evidence. No voice
origin field is added to `AgentTurnRequest`, and server provenance is not claimed.

One microphone controller is mounted. Navigation, logout, hidden tabs, identity
changes and unmount stop capture and playback and invalidate late speech callbacks.
Typed submission also stops capture and shares the same admission gate. Optional
strategy authoring/setup controls are inside Strategy options. Proposal confirmation
and rejection remain explicit; existing execution and kill-switch policy is unchanged.
Unavailable browser speech leaves the typed composer usable.

## Request and recovery guarantees

The controller supplies the voice turn key as the existing `Idempotency-Key`.
AgentWorkspace validates the complete generated request before retaining or sending
it. Invalid local requests remain editable and never become saved ambiguous requests.
Recovery reuses the retained complete body, document references and key, with a new
cancellation signal. It does not rebuild context from today's composer or reimport
an attachment. Double recovery clicks share admission; there is no automatic retry.

The pending store uses session storage with an actor-scoped in-memory fallback.
Missing storage alone does not clear ambiguity. A validated terminal reply records
explicit evidence for the exact turn key and conversation, then removes pending
metadata. The controller subscribes to those outcomes and reconciles its own pending
map when returning from another conversation. A mismatched key or conversation
cannot release the voice gate.

Late acknowledgments are retained in their own conversation while the authenticated
session is unchanged. They do not alter another conversation or play unsolicited
audio after navigation. This works when the transport actually delivers a proven
reply after cancellation. An aborted browser fetch does not establish server success;
the original request remains recoverable through the existing idempotency flow.
Neither absent history nor absent local pending storage is terminal evidence.

| Result | Behavior |
| --- | --- |
| Valid terminal acknowledgment | Display reply immediately, persist matching terminal evidence, release pending admission, reconcile history asynchronously. |
| Local validation/admission rejection before dispatch | Retain editable speech; no ambiguous request persisted. |
| Timeout, interruption, failed/malformed transport or conflict | Retain original body/key and voice provenance; pause for explicit recovery. |
| Reply acknowledged while capture is still pending | Display the reply, retain `turn_capture`, and require recovery before another spoken turn. |

The controller and shared request wait both use `AGENT_TURN_TIMEOUT_MS = 360_000`.
The wait is bounded even when an injected transport ignores AbortSignal; a delivered
late terminal reply can still reconcile metadata. Browser input/output timers retain
their existing budgets. Recovery completes with voice inactive; restarting capture
requires a user gesture.

## Verification

Focused unit/component/integration suite: **162 passed in eight files**, no skips.
This includes controller and provider behavior, compact and legacy controls, Agent
integration, shared adapter, recovery metadata and acknowledged-history reconciliation.
Coverage includes both reported defects, two consecutive spoken turns, complete-body
rejection, local provenance, original attachment/key recovery after remount, duplicate
clicks, pending capture, six-minute timeout, lost storage, late A acknowledgment while
viewing B, typed admission and logout.

Deterministic Chromium: **eight actual Agent page checks** and **five standalone
component harness checks** passed. Actual-page checks exercise new conversation
creation, successive spoken turns, immediate replies surviving stale history,
clear-and-replace review, typed drafts across navigation/reload, exact attachment
recovery, hidden-tab/navigation cancellation and unsupported speech fallback.
Widths: 320×720, 390×844 and 1440×1000; controls have at least 44px button heights
and no horizontal overflow. [Actual Agent screenshots](screenshots/voice-agent/README.md)
and [standalone evidence](screenshots/voice-conversation/README.md) use synthetic data.

Repository TypeScript checking, changed-file ESLint with zero warnings,
`git diff --check`, scoped secret inspection and ownership comparison passed.

Run from `frontend/`:

```sh
npx vitest run src/lib/voice/conversation-controller.test.ts src/lib/voice/browser-voice-provider.test.ts src/components/agent/VoiceConversationControls.test.tsx src/components/agent/AgentVoiceControls.test.tsx src/components/agent/AgentWorkspace.test.tsx src/components/agent/turn-recovery.test.ts src/components/agent/agent-voice-transport.test.ts src/components/agent/acknowledged-turn.test.ts
npm run typecheck
NEXT_FONT_GOOGLE_MOCKED_RESPONSES="$PWD/test-fixtures/offline-fonts.cjs" PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium npx playwright test --config playwright.voice.config.ts
npx playwright test --config playwright.voice-conversation.config.ts
```

The browser checks use the real browser voice provider with simulated Web Speech
events and fixture Agent responses. They verify integration and layout, not native
speech recognition. Actual microphone capture, permissions/service connectivity,
speaker feedback, Safari/WebKit, Firefox and physical iPhone remain unverified.
No production build or full frontend/backend CI was run. No acoustic barge-in,
echo cancellation or premium realtime speech is claimed. Browser speech may use
a browser-managed remote service, disclosed in Voice details; AlphaTrade's voice
modules do not store audio.

## Precise next handoff

PR238 integration work is complete for this scope; no implementation blocker remains.
Review the consolidated commit and this handoff alongside PR237. PR237's owner should
retain ownership of CI, generated clients and activity views and resolve any later
overlap in the shared Agent baseline without introducing another API path. Keep the
matching key/conversation terminal proof and complete saved request semantics intact.

Before claiming native voice support, verify microphone capture and two spoken turns
on the intended desktop browser, Safari and a physical iPhone. Check browser gesture
requirements, permission denial, hiding/navigation/logout, interruption and speaker
feedback. These hardware/browser checks are separate from the passing simulation.
Merging, deployment and provider activation require their own authorization.
