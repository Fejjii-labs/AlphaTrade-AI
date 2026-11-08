# TradingView Signal Intake and BloFin Read-Only Sync (AT-037)

Secure, deterministic **TradingView webhook intake** with signal lifecycle, optional
routing into the existing **paper-validation candidate queue**, plus **BloFin demo
read-only synchronisation** for account/position/market context.

**Paper / demo only.** This slice never places, modifies, or cancels orders. Live
BloFin access and real trading remain disabled (`enable_real_trading=false`,
`execution_mode=paper`). BloFin sync requires demo exchange posture and never
calls execution APIs.

See also: [paper_validation.md](./paper_validation.md) · [research_validation.md](./research_validation.md) · [security.md](./security.md)

## TradingView webhook intake

| Concern | Behavior |
|---------|----------|
| Auth | HMAC-SHA256 over `{timestamp}.{raw_body}` via `X-AT-Signature` + `X-AT-Timestamp` |
| Secret | `TRADINGVIEW_WEBHOOK_SECRET` (fail-closed when missing/disabled) |
| Replay | Reject when `\|now - timestamp\| > TRADINGVIEW_WEBHOOK_MAX_SKEW_SECONDS` (default 300) |
| Idempotency | Unique `(organization_id, idempotency_key)` and `(organization_id, alert_id)`; same payload converges; different payload → `422` |
| Schema | Strict Pydantic model, `extra=forbid`, bounded field sizes |
| Storage | Redacted payload only (`redact_value`); secrets never logged |
| Org binding | The secret is global, so it is bound to `TRADINGVIEW_WEBHOOK_ORGANIZATION_ID`; payloads for any other org are refused. Unset is allowed only in `local`. Unknown, unbound and foreign orgs return the same `422 tradingview_webhook_organization_refused` (no existence oracle) |
| Rate limit | Public IP scope `tradingview:webhook`, `TRADINGVIEW_WEBHOOK_RATE_LIMIT` per `TRADINGVIEW_WEBHOOK_RATE_WINDOW_SECONDS` (60/hour default) |

### Endpoint

`POST /webhooks/tradingview` (unauthenticated, signed)

Example body:

```json
{
  "organization_id": "<uuid>",
  "alert_id": "tv-alert-100",
  "symbol": "BTCUSDT",
  "timeframe": "15m",
  "direction": "long",
  "setup_name": "HTF Pullback",
  "setup_version": 1,
  "strategy_id": "<uuid>",
  "strategy_version_id": "<uuid>",
  "trigger_level": 65000,
  "invalidation_level": 64000,
  "take_profit_level": 67000,
  "stop_loss_level": 64000,
  "confidence": 0.8,
  "idempotency_key": "tv-alert-100",
  "source": {"chart": "BTCUSDT.P"}
}
```

Headers:

```http
X-AT-Timestamp: 1710000000
X-AT-Signature: <hex hmac-sha256>
Content-Type: application/json
```

### Signal lifecycle statuses

| Status | Meaning |
|--------|---------|
| `received` | Persisted before business validation (transient) |
| `validated` | Schema + org/strategy linkage OK |
| `rejected` | Validation failed; `rejection_reason` + `validation_errors` set |
| `duplicate` | Reserved; duplicate deliveries converge to the original row |
| `candidate_created` | Optional paper-validation candidate linked |

### Authenticated inbox API

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/tradingview/signals` | Reader | Org-scoped inbox |
| GET | `/tradingview/signals/{id}` | Reader | Detail + links |
| POST | `/tradingview/signals/{id}/create-candidate` | Trader | Optional paper candidate (confirm phrase required) |

Confirm phrase: `CREATE_TRADINGVIEW_PAPER_CANDIDATE`.

Candidate creation builds a synthetic TradingView-origin alert + ready draft +
queued candidate (`promotion_source=tradingview_signal`). It never creates a live
order or executable proposal. Auto-create on intake is off by default
(`TRADINGVIEW_AUTO_CREATE_CANDIDATE=false`).

## BloFin demo read-only sync

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/exchange/blofin/sync` | Owner | Fetch + persist bounded snapshot |
| GET | `/exchange/blofin/sync/latest` | Reader | Latest snapshot (marks stale when aged) |

Safety:

- Uses `ensure_demo_exchange_access` + `get_demo_account_provider` only
- Never calls execution / place / cancel APIs
- Persists balances, positions, permission booleans, provider health, provenance
- Truncates to `BLOFIN_SYNC_MAX_BALANCES` / `BLOFIN_SYNC_MAX_POSITIONS`
- Missing/stale/unavailable data → `health_status` of `degraded` / `stale` / `unavailable`
- API secrets are never logged or stored

## Settings (defaults)

| Setting | Default | Notes |
|---------|---------|-------|
| `TRADINGVIEW_WEBHOOK_ENABLED` | `false` | Fail-closed when false |
| `TRADINGVIEW_WEBHOOK_SECRET` | empty | Required when enabled |
| `TRADINGVIEW_WEBHOOK_ORGANIZATION_ID` | empty | Org the secret is bound to; required outside local when enabled |
| `TRADINGVIEW_WEBHOOK_RATE_LIMIT` / `_RATE_WINDOW_SECONDS` | `60` / `3600` | Per-IP webhook budget |
| `TRADINGVIEW_WEBHOOK_MAX_SKEW_SECONDS` | `300` | Replay window |
| `TRADINGVIEW_AUTO_CREATE_CANDIDATE` | `false` | Intake does not auto-queue |
| `BLOFIN_SYNC_STALE_AFTER_SECONDS` | `300` | Freshness for latest reads |
| `BLOFIN_SYNC_MAX_POSITIONS` | `50` | Bound persisted positions |
| `BLOFIN_SYNC_MAX_BALANCES` | `50` | Bound persisted balances |

## Frontend

- `/tradingview-signals` — inbox, detail, rejection explanations, paper-candidate action
- `/exchange` — BloFin demo sync status panel (loading / empty / stale / error)

## Safety invariants

- `ENABLE_REAL_TRADING` remains false; paper execution mode unchanged
- No order placement, cancellation, or modification in this slice
- No live BloFin account access; demo exchange mode only
- Rate limits, denylist, proxy trust, and kill-switch controls are not weakened
- Webhook and BloFin secrets are never exposed in logs, API responses, or docs
