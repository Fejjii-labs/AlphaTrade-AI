# TrendPulse1R verification and runtime owner handoff

Feature branch: `codex/trendpulse-1r-adapter`, based on PR241 correction
`6ca6a76201547524275266dfa22fdf12f5932b14`. The exact final feature SHA and draft PR
are recorded in its description. Target is the corrected domain feature branch,
not the current release candidate. PR241 remains at its correction SHA.

[Adapter rules and interfaces](trendpulse_1r_adapter_contract.md).
[Pure JSON Schema](contracts/trendpulse_1r.v1.schema.json); corrected hash below.
Original bab0e9ce artifact SHA256 was
`de33e74c8e0fde1520df6b72450fd74325c0ec050aecf8cdf14549ed13ccb887`.

## Receipt correction evidence (PR243 continuation)

The original `bab0e9ce646a948789ff606b8e4c81e68e1a3988` evaluator was loaded
unchanged from `git show` and evaluated against independent positive-latency
fixtures: **6/6** (long/short × trigger closes at :05/:10/:15) failed with
`missing_causal_history`. Corrected adapter v2 admits the same candle windows
using receipts known at the post-close decision; no arrival timestamp is changed.

**140 passed in 15.65s, zero skips/warnings**, command from `backend/`:

```sh
.venv/bin/pytest -o addopts='' -q tests/test_trendpulse_1r_adapter.py \
  tests/test_trendpulse_1r_experiment_contract.py
.venv/bin/ruff check src/app/strategy_brain/trendpulse_1r tests/test_trendpulse_1r_adapter.py
.venv/bin/ruff format --check src/app/strategy_brain/trendpulse_1r tests/test_trendpulse_1r_adapter.py
.venv/bin/mypy --follow-imports=silent src/app/strategy_brain/trendpulse_1r
```

All fixture receipts now have observed/receive delays of 2/3 seconds; recorded
arrival is +4s and decision +5s. Coverage includes all three positions/both
orientations, both late receipt clocks, late prior/trigger arrivals, as-of repeat
immutability, fresh later evaluation, inclusive receipt cutoff, expiry at 60s,
missing history, same-close/future candles, unchanged structural 1R geometry,
retry natural-ID stability and decision-time derivation hash.

Scoped Ruff/format and strict typing pass (five source files). Shared `npm run
api:check` passes on this worktree with the unchanged full OpenAPI schema hash
`6f7f9ece7248442b53f9e3ebd84277053db284d1e52cf97a18a25ac5c4dcf0e3`.
Pure schema regenerated; current file SHA256
`a9355e359c6289754fef3d4452239503c586aee5cc5cd45a7db3df826f82ca31`.
The exact correction SHA and automatic focused CI evidence are in PR243. No
migration, screening runtime, execution, threshold tuning or activation is part
of this correction. Screening continuation starts separately after publication.

## Original pure adapter evidence (bab0e9ce)

- **261 passed in 194.79s, zero skips/warnings/failures** in the combined selection
  below. This includes **80 new adapter/contract/domain cases** and existing
  experiment lifecycle/risk, SFP detection/rolling receipt reuse, and canonical
  Nested meaning/paper-path regressions. It is not the complete backend suite.
- New cases cover causal UTC 15m/5m alignment and same-close exclusion; both
  availability clocks and late receipts; missing/unclear/incomplete candles;
  identical/reordered receipts and conflicting revisions; stable signal dedupe;
  long/short inverse structural geometry; non-power-of-ten tick rounding; invalid
  risk, precision, gap and expiry; fixed-version parameter guards and ambient
  Decimal independence; serialized request/schema drift; exact experiment variant,
  tenant/account/UID/source/hash binding and fresh promotion identity.
- Disposable PostgreSQL confirms immutable authored spec recognition, exact two
  timeframe universe, parameter override refusal, setup-only samples and continued
  execution admission refusal. Default source resolver returns 503 and creates no
  sample. Fixtures do not claim native execution or performance.
- Changed-source/tests Ruff, format checks and scoped strict mypy pass (six source
  files, `--follow-imports=silent`). Repository-wide mypy cleanliness is not claimed.
- `npm run api:check` passes with unchanged shared schema SHA256
  `6f7f9ece7248442b53f9e3ebd84277053db284d1e52cf97a18a25ac5c4dcf0e3`.
  No new HTTP route or shared generated artifact drift. `git diff --check` passes.

## Exact reproduction

Locked Python 3.12 development environment; independent venv in this worktree.
The URL below belongs only to a unique disposable PostgreSQL 16-alpine loopback
container, not a shared/native account database. Experiment fixtures create/drop
UUID schemas; existing Nested/SFP fixtures also reset its disposable public schema.
Use the same explicit fixture URL for both settings before collection. Remove the
owned fixture container after verification.

From `backend/`:

```sh
export EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://alphatrade:alphatrade@127.0.0.1:55439/alphatrade_test
export PHASE1_POSTGRES_URL="$EXPERIMENT_TEST_POSTGRES_URL"
.venv/bin/pytest -o addopts='' -q \
  tests/test_trendpulse_1r_adapter.py tests/test_trendpulse_1r_experiment_contract.py \
  tests/test_trendpulse_1r_domain.py tests/test_experiment_domain.py \
  tests/test_experiment_risk.py tests/test_sfp_detector.py \
  tests/test_sfp_receipt_reuse.py tests/test_strategy_brain_nested.py --tb=short
.venv/bin/ruff check src/app/strategy_brain/trendpulse_1r \
  src/app/experiments/service.py tests/test_trendpulse_1r*.py
.venv/bin/ruff format --check src/app/strategy_brain/trendpulse_1r \
  src/app/experiments/service.py tests/test_trendpulse_1r*.py
.venv/bin/mypy --follow-imports=silent src/app/strategy_brain/trendpulse_1r \
  src/app/experiments/service.py
```

From `frontend/`: `npm run api:check`.
Schema reproduction from `backend/`:

```sh
PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from pathlib import Path
from app.strategy_brain.trendpulse_1r.interface import TrendPulseInterface
Path('../docs/contracts/trendpulse_1r.v1.schema.json').write_text(
    json.dumps(TrendPulseInterface.model_json_schema(), indent=2, sort_keys=True) + '\n'
)
PY
```

## PR241 correction verification

Correction from `bed8994634eee1fffab578c57edb7126db5fab2f` was committed/pushed
once at `6ca6a76201547524275266dfa22fdf12f5932b14` before this adapter branch.
Original manual migration failure reproduced. Corrected affected migration/domain
selection: 75 passes/no skips, CI policy: 28 passes, generated frontend contracts:
7 passes; shared drift, frontend lint/typecheck and domain Ruff/typing passed.
Automatic focused [CI38066879849](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38066879849)
passed on that exact correction: **472 backend/no skips, 143 frontend**, with drift,
lint, typing and deployment-safety checks successful. All six experiment test
files were selected and their formerly skipped PostgreSQL cases executed. Two
existing backend warnings were reported. Complete backend acceptance, complete
frontend unit/build, Docker, evaluations and browser gates were skipped as required
by the focused policy. No manual dispatch. This evidence belongs to the correction
SHA, not an adapter runtime or full release acceptance.

## Remaining owner dependencies

The pure evaluator returns research prices/evidence; no quantity, Candidate,
TradePlan, dispatch or outcome is created. Domain running state is not activation.
Pure experiment binding is not a trusted source proof; declared native account/source
fields cannot verify identity or mint a native sample. Exploration evidence cannot
become fresh Validation performance through rebinding. Performance remains null;
default source ingestion503 and TrendPulse execution admission refusal remain.

Next owners need strategy library authoring/compiler/evaluation registration,
causal public acquisition and durable dedupe/revision replay, reviewed canonical
Candidate/TradePlan bridge, verified fresh native demo account and rule/quote/basis
proofs, atomic account-wide deterministic sizing/precision/exposure/loss reservations,
existing kill-switch/approval/final dispatch authority, protected native lifecycle
and independently reconciled outcomes, and separate trusted simulator/native sample
resolvers. Manual positions consume exposure without conferring management authority.
BloFin owns demo execution/replenishment. Follow the contract's runtime handoff
before claiming trading or performance tracking.

No new migration, frontend/CI feature changes, runtime activation, deployment,
orders, operator setting changes, credential changes, account mutations, Telegram
messages or full backend CI. Safe local operational handoff mirror is verified;
Mac/iCloud sync remains unverified in this managed workspace.
