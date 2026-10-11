# PR249 frontend refinement

Status: READY for frontend review. Updated: 2026-10-11 (Europe/Berlin).
Base: PR249 `00f388017c6bcd2fb53d8135df60ac76bc714125`.
Branch: `codex/frontend-refinement-pr249` → `codex/interactive-voice-monitoring`.
Tested frontend commit: `2e54d0058efa7b12766a979b069e0d771d11ffdf`.
Later commits contain only this handoff and screenshot evidence.

Fixed acceptance checklist: **6/6 accepted** against focused frontend fixtures.

| Criterion | Implemented refinement | Completed work preserved |
| --- | --- | --- |
| Header / scrolling | Aligned two-row phone header; Search joins Status menu; outside press and Escape dismiss it. Structured draft and experiment evidence use page scrolling. | Visible mode/pause and existing confirmation; page-scrolling conversation and history drawer. The menu and phone history remain scrollable when necessary to fit the viewport. |
| Width / type / details | Search fills available width, strategy families use two full-width columns, 16px mobile fields and conversation text; monitoring parameters/IDs expand on demand. | Existing details for document provenance, originals, evidence and model usage. |
| Agent | Send, Upload and Talk to Agent grouped before secondary strategy/dictation options; aligned touch targets. | All realtime props, callbacks, controller/transport, recovery, interruption, capture and typed-action behavior. |
| Journal / Knowledge | Back labels name the destination; direct Knowledge links return to Knowledge; Back remains present during loading and unavailable saved-entry results. | Existing filtered-list/scroll restoration, document paging, content categories, correction/Undo, trade review and Dashboard statistics. |
| Strategies | Family cards start the library and expand the newest three detections from seven days; Browse opens retained family history. | Experiments, configuration/revision links, saved drafts, Risk Policy, full detection history and coin assignments. |
| Settings | Mobile monitoring inputs remain legible; technical assignment details collapse. | Existing three groups: Market Monitoring, Notifications (Telegram), Account & System; no operator values or action requests changed. |

Release coordination: [scope notice posted to PR249](https://github.com/Fejjii/AlphaTrade-AI/pull/249#issuecomment-6112278741). Credentials, hosting, deployment,
live voice acceptance and the canonical release HANDOFF.md remain with the release
owner. This document is the UI checkpoint to avoid competing release-document edits.
No backend, migration, model, trading, deployment configuration or generated-contract changes.

Verification: **140 unit tests / 10 files passed; 3 Chromium scenarios passed** at
1440×1000, 390×844 and 844×390. Zero skips/retries. TypeScript, changed-file ESLint
with zero warnings and `git diff --check` passed. The browser covers menu search,
outside/Escape dismissal, focus return, Agent send/upload and explicit voice
unavailable state with retained input, filtered Back navigation, missing saved
entry, exact original document, expandable recent detections, assignment details,
editable monitoring, Telegram/account groups and horizontal overflow. Existing
unit checks cover voice lifecycle/recovery and assignment save/replay/isolation.
All data/network responses are synthetic; no backend server or provider is started.
Initial stale shell expectations and an incomplete chunk fixture were corrected
before the clean final runs; validators/assertions were preserved.

Reproduce from `frontend/` after `npm ci --cache /tmp/alphatrade-npm-cache`:

```sh
npm run typecheck
npm run test -- src/components/layout/TopBar.test.tsx src/components/layout/AppShell.test.tsx src/components/agent/AgentWorkspace.test.tsx src/components/agent/turn-recovery.test.ts src/components/WatcherAssignmentsSection.test.tsx 'src/app/(app)/knowledge/page.test.tsx' 'src/app/(app)/strategies/page.test.tsx' 'src/app/(app)/settings/page.test.tsx' src/components/strategies/ExperimentsPanel.test.tsx src/lib/voice/realtime-controller.test.ts
REFINEMENT_SCREENSHOTS=1 PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium npm exec -- playwright test --config playwright.refinement.config.ts --retries=0
```

[Twelve screenshots and reproduction notes](screenshots/frontend-refinement/README.md).
Protected-path comparison against the base passed for backend, scripts, CI,
API/generated contracts, voice library, manifests, Dashboard and statistics.
No full backend CI, full frontend suite/build, migration, merge or deployment ran.

Remaining gaps: hosted/live voice and retrieval acceptance stay with the release
owner. Physical Safari/iOS keyboard and microphone were not tested by Chromium
viewport checks. Screenshots use the existing test-only font fixture, so hosted
font delivery remains unverified. Mac/iCloud sync is unavailable in this Linux
workspace; the canonical release HANDOFF.md was intentionally preserved.

Implemented and locally verified: 6/6 frontend criteria. Deployed/live accepted by
this task: none. Next action: review this isolated UI diff and integrate it through
the release owner, reconciling any later shared-file changes before qualification.
