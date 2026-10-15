# Backend launch recovery, 13 October 2026

Base: PR249 fa0c8ee34c8fbbde15872584087ac822dc8787e1.

The cancelled release run38402964357 has no accessible backend log or diagnostic artifact. Its test level cause remains unknown. This correction closes observed diagnostic gaps; it does not claim the full suite now passes.

## Change

1. A standard library launcher starts before uv, records checkout/workflow/source provenance, owns the child process group, and bounds the whole command.
2. The existing inner supervisor joins that group when launched by it. The launcher can terminate descendants even when the inner supervisor freezes.
3. Full release CI uploads source evidence before tool startup, separately bounds collection to180seconds and retains it before unfiltered acceptance. The100minute internal budget remains; the outer budget is101minutes, below the105minute step and120minute job limits.
4. A path scoped Backend launch proof workflow exercises only six process scenarios and retains early/final artifacts. It never enables full backend acceptance, application providers or deployment.
5. Exact selector mappings include the two infrastructure regression files for future launcher/supervisor changes. Existing required gate names and conditions remain.

## Focused evidence

Six new process scenarios and seven existing supervisor cases passed before the workflow selector check identified its app only path assumption. That guard now also permits the two exact infrastructure paths and verifies near misses do not expand selection. The final selector/workflow suite passed32cases. All changed Python files pass Ruff and formatting; diff whitespace passes. Counts are distinct across these files; no full application suite was run.

New scenarios: early provenance; exact nonzero exit; startup stall; external cancellation and descendant cleanup; actual supervisor frozen with SIGSTOP; actual uv startup without project dependencies or network.

The local harness uses Python3.12.14 with isolated pytest9.1.1 and Ruff0.17.0. Infrastructure tests bypass application conftest deliberately; an absent pytest asyncio plugin emits an irrelevant configuration warning. The hosted proof uses standard library unittest and actual installed uv. Ordinary CI retains locked project validation.

## Next

Inspect the short hosted proof and confirm both source/results artifacts exist. Inspect routine focused CI once. Integrate into the release candidate only after those pass. No extra full gate was dispatched by this correction. An actual complete suite run remains separate and must follow the existing release prerequisites after their documented ordering inconsistency is resolved.

A passing proof establishes bounded launch and artifact transport for controlled failures. It cannot recover the missing historical stack, certify real trading or establish that a future runner failure will always retain artifacts. Keep source and collection artifacts available even if final execution is lost.

