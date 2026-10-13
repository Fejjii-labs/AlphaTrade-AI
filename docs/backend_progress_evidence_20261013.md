# Backend progress retention

The last full backend run38402964357 exceeded120minutes. Its backend log and final diagnostic artifact are unavailable. PR254 already bounded startup and retained source and collection before tests, but active test evidence was still uploaded only after execution. This change closes that remaining retention gap. It does not identify the missing historical stack or claim the full suite is passing.

## Acceptance

1. An initial artifact upload must succeed before the suite starts.
2. Periodic uploads retain the current phase and bounded diagnostic tails independently of test completion. Failed or stuck uploads stop execution with a distinct failure, never an unbounded wait or a passing test result.
3. A short hosted proof times out deliberately and downloads the active fixture artifact that was saved before timeout.
4. Existing explicit release gates and one unfiltered backend process remain. A separate diagnostic profiles only the two named PostgreSQL cases, not the full suite.

## Implementation

The existing launcher owns the test process group and deadlines. A small publisher copies an explicit file allowlist, caps each copied file at256KiB and excludes symlinks. Uploads use the official pinned GitHub artifact SDK in a separate killable Node24 subprocess, with a30second network budget. SDK credentials, environment and error bodies are never copied or printed. The package is CI only; application dependencies are unchanged.

One checkpoint is uploaded before testing, then at15minute intervals, at most seven checkpoints. Each immutable name includes run ID, attempt and sequence. Together with source, collection and final diagnostics this uses at most ten artifacts per backend job, retained seven days. No additional paid provider is introduced. Runtime limits remain180seconds per pytest phase,6000seconds internally,6060seconds for the whole launch command,105minutes for the step and120minutes for the job. Cleanup and an already pending upload have bounded overhead below the step budget.

The host proof uses an18second synthetic stalled fixture and6second upload interval. It requires the second artifact to contain that active fixture, downloads it after the forced timeout and checks exit124. The hosted PostgreSQL diagnostic runs only successful_conversation_paper_flow and duplicate_confirmation_and_restart_converge under150seconds, retaining cProfile, phase durations, source and result. This will distinguish schema construction from other setup work before changing fixtures or isolation.

## Validation and limits

Local:44 focused launcher, checkpoint and workflow scope cases passed. Changed Python lint/format, JavaScript syntax and YAML parsing passed. The pinned CI dependency audit reported zero vulnerabilities. Hosted proof run38442015361 succeeded on feature head602384688c9935d4fafe427ba251a48c1f0d75b1. Its synthetic fixture exited124 after18.064seconds; three progress checkpoints were uploaded, and checkpoint2 was downloaded after timeout and contained synthetic_stuck_fixture in setup. Artifact11834764374 retains the timeout result. Artifact11834888952 retains the actual PostgreSQL profile: both selected cases passed in6.05seconds, with2.821seconds in metadata.create_all,.841seconds in reset_public_schema and.598seconds in seed construction. This confirms setup overhead without establishing the historical timeout cause.

Routine CI38442015364 succeeded:110 backend tests in17.20seconds, frontend and deployment safety passed. Docker/evaluation/browser skipped by routine policy. PR255 merged into PR249 atde5c010c7c85dfe5cde1ec8fabf60b6262500fe0; the merge tree exactly matches tested tree2ebed239ecaeacbe32c4a4d50a1e8459c4ef4122. All four retention criteria are accepted. Full backend acceptance remains pending.

Proof: https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38442015361
Focused CI: https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38442015364
Integration: https://github.com/Fejjii/AlphaTrade-AI/pull/255

No full suite, deployment, main merge, exchange call, activation or operator setting change. Previously uploaded checkpoints survive a subsequent runner loss; the interval since the last successful upload may still be lost, and a failure before the initial upload is not claimed recoverable. This cannot guarantee against GitHub storage outages or artifact expiry. Live financial behavior is outside this repair.
