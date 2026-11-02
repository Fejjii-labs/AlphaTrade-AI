# Env drift guard (api ↔ paper worker)

Incident 2026-10-25: `BLOFIN_ACTIVITY_ORGANIZATION_ID` / `BLOFIN_ACTIVITY_EXPECTED_UID` were set on the
staging paper worker but not the API → manual demo refresh failed with
`blofin_fallback_sync_refused reason=activity_organization_unset`. Enabling `BLOFIN_ACTIVITY_ENABLED`
without `BLOFIN_READONLY_API_KEY/_SECRET/_PASSPHRASE` crash-looped the worker.

## Manifest
`backend/src/app/core/required_env.py`
- api always: `ENVIRONMENT`, `PROVIDER_MODE`, activity org id + expected UID (manual refresh / fallback sync).
- paper_worker always: `ENVIRONMENT`, `PROVIDER_MODE`.
- if `BLOFIN_ACTIVITY_ENABLED=true` (either role): read-only key/secret/passphrase + org id + expected UID.
- Shared values that must match: org id, expected UID, `ENVIRONMENT`, `PROVIDER_MODE`, `EXCHANGE_MODE`, `BLOFIN_DEMO_REST_BASE_URL`.

## Startup enforcement
API lifespan and `run_paper_worker_process` call `validate_required_env(role)`. When
`ENVIRONMENT` is staging/production (or `ENV_VALIDATION_ENFORCE=true`), missing keys raise
`MissingRequiredEnvError` naming the keys only, so the Render deploy fails instead of shipping.
Local/tests only log a warning. Emergency bypass: `ENV_VALIDATION_ENFORCE=false`.

## Pre-deploy check
```
RENDER_API_KEY=... python scripts/check_env_drift.py \
  [--api-service srv-d8fvbcd7vvec739mc060] [--worker-service srv-daqej7o473hc73fsc1k0]
```
Read-only. Prints names only; shared values compared by SHA-256. Exit 0 ok, 1 drift, 2 error.
Run before any staging env change or deploy.
