# Notification and market evidence workflow continuation

Base: reviewed PR249 candidate `df0a12d3`. This work belongs to the isolated
workflow feature branch; the frozen candidate, release owner, deployment settings,
operator preferences and trading permissions stay with their existing owners.

## Notification diagnosis

The existing Nested producer requires a latest confirmed closed-bar setup, fresh
required canonical evidence and the existing Candidate/recipient gates. The SFP
producer already projects journaled sweep, reclaim, confirmation, invalidation,
expiry and risk-block facts through the existing policy-controlled Telegram
outbox. SFP informational events do not authorize an executable SFP plan. The
comment in `record_paper_link` describes its Nested-only return adapter; it does
not mean the separate SFP lifecycle producer is absent.

The existing Agent strategy reader now answers Nested/SFP alert questions from
bounded persisted receipts, rather than diagnosing a gap from an empty alert list:

1. Exact strategy-version/user/tenant-bound scan statuses and canonical refusals.
2. Acquisition and evaluation attempt statuses and allowlisted reason codes.
3. Brain detection states, detector reasons, paper decision/risk reasons and
   existing Candidate links.
4. Typed Telegram outbox event/state, suppression or transport reason, attempt
   count, send timestamp and presence of an existing provider message receipt.

The read uses the latest seven days, up to 100 rows per stage/version and up to
eight already selected definitions. It reports truncation. Samples do not prove
continuous coverage. Missing receipts remain unknown; a successful scan does not
substitute for trade-window/component coverage proofs. Current setup Candidate
links are projections, not a reconstructed historical Candidate creation count.
SENT/ACKNOWLEDGED state does not prove a human read a notification. Legacy rows
without typed strategy facts cannot be attributed. Raw errors, message contents,
bot/chat identities, provider message IDs and transport payloads are not returned.

No scanner, detector, strategy threshold, subscription, quiet hour, quality filter,
activation flag, binding or delivery policy changes. No alert is sent by these
diagnostic reads. In particular `POLICY_QUALITY_MISSING` remains a valid refusal:
coverage/components are not invented 0–100 quality scores.

Actual deployed Nested/SFP gap attribution remains blocked: this task has no
authenticated runtime tenant/version/time-window receipts or deployment identity.
Local fixtures prove the diagnostic joins and policy-state reporting; they do
not establish October runtime coverage or the cause of a live missing alert.

## Existing Agent market context and acquisition

The existing canonical quote reader still owns OI, bounded executed flow/CVD and
resting book context. Its deterministic text now retains provider, adapter,
instrument, event/observation/collection timestamps, calculation method and exact
contract reason where available. Consumer freshness/identity/hash checks continue
to reject unusable values. Current optional market availability is distinct from
the availability recorded in a stored strategy setup.

The existing independent exchange probe now also verifies the existing resting
book reader/validator and reports snapshot provenance. Book failure does not hide
OI/flow diagnostics; incomplete core CVD does not hide a supported independent
book. All-requested success now includes a usable book. No new provider adapter or
market pipeline was created.

Preserved limitations: OI is a single provider record in native units; no inferred
OI change/notional. CVD resets at the declared ten-minute start and depends on a
proven executed trade window. Divergence remains the existing two print-close
method. The book is a depth-limited snapshot, excludes RPI and has no historical
coverage. Cross-venue context cannot satisfy required canonical venue evidence.

One actual read-only access check on 2026-10-12 through the inherited environment
proxy attempted:

- Binance `GET https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT`
- Bybit `GET https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=5min&limit=1`

Both returned urllib `URLError: Tunnel connection failed: 403 Forbidden` before
any provider response. This is a proxy access blocker, not evidence of a provider
outage, exchange regional policy or missing credentials. There was no bypass or
retry. Native OI, executed flow, CVD and book acquisition/freshness/coverage are
unverified; no native market or financial values are invented.

## Focused verification and next operation

Run from `backend` with a separately isolated disposable PostgreSQL database;
the inherited PostgreSQL fixtures reset that database's public schema:

```sh
PHASE1_POSTGRES_URL='<isolated disposable test URL>' .venv/bin/pytest tests/test_notification_workflow_diagnostics.py tests/test_strategy_brain_nested.py tests/test_sfp_strategy_brain_runtime.py tests/test_sfp_telegram_policy_integration.py tests/test_public_market_evidence_foundation.py tests/test_independent_exchange_probe.py -o addopts='' -q
.venv/bin/mypy --follow-imports=silent src/app/strategy_brain/notification_diagnostics.py src/app/interactive_agent/market_context.py src/app/evidence_pipeline/probe.py
```

The new diagnostic tests exercise actual persisted SQLite/PostgreSQL joins,
tenant/user ownership, finite receipt windows, capped/truncated counts, canonical
coverage refusal, policy suppression, provider receipt presence and Agent reads.
The market tests use recorded HTTP fixtures and invoke the actual canonical
reader and `InteractiveAgentService.handle_turn`. They are simulated transport
tests, not successful exchange or Telegram requests. Full backend CI remains
reserved for the combined release owner.

Focused results: **140 passed, zero skipped** with the disposable PostgreSQL
selection above. The subsequent final diagnostic/probe selector passed **14,
zero skipped** after adding the independent supported-Bybit-book regression and
preserving an imported pytest fixture during lint. Ruff lint/format passes the
eight scoped Python files; strict mypy passes the three source modules in the
command above. Initial fixture-construction and fixture-registration failures
were corrected; no assertions, canonical gates or strategy rules were removed.

Next executable steps once the relevant access is available: obtain the installed
revision and a read-only tenant-scoped scan/acquisition/evaluation, Brain and
Telegram-outbox receipt packet for the affected strategy versions and exact UTC
window; call `read_notification_diagnostics` against that approved read-only
session and compare its rejection/delivery receipts with the provider coverage
proofs. Independently run the existing `python -m app.evidence_pipeline.probe
--provider binance_usdm` and `--provider bybit_usdt_perpetual` through authorized
exchange egress, retaining their exact timestamps/hashes and unavailable reasons.
No trading or notification activation is needed for either evidence operation.
