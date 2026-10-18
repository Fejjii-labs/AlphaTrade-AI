# BloFin demo snapshot worker and activity win rate

Read-only. This does not place, cancel, or amend orders, and it does not enable
real-money trading. Turning the activity worker on requires Sofien's approval.
Set the names below on the worker only after that approval. Do not put secret
values in the repository, logs, or pull requests.

## Snapshot cycle

The paper worker runs `BloFinSyncService.sync()` when both of these are true:

- the existing demo gates are open (`demo_account_active`: paper execution,
  real trading off, allowlisted demo host, and either dedicated read-only sync
  or demo exchange mode with credentials present);
- `GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID` and `GOVERNED_BLOFIN_DEMO_USER_ID`
  are set. The cycle does not guess an organization.

Otherwise the cycle status is `skipped` and it does not open the database or
call BloFin. Identity, read-only permission, and UID checks stay inside
`BloFinSyncService.sync`. The worker records actor `system` and provenance
`sync_origin=worker`.

| Name | Role |
| --- | --- |
| `BLOFIN_DEMO_SNAPSHOT_SYNC_SECONDS` | Cadence, 30–60. Default 45. |
| `GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID` | Owner organization pin. |
| `GOVERNED_BLOFIN_DEMO_USER_ID` | Owner user pin. |
| `BLOFIN_READONLY_SYNC_ENABLED` | Use the dedicated read-only sync path. |
| `BLOFIN_READONLY_API_KEY` | Dedicated read-only key. Value stays in the environment. |
| `BLOFIN_READONLY_API_SECRET` | Dedicated read-only secret. |
| `BLOFIN_READONLY_API_PASSPHRASE` | Dedicated read-only passphrase. |
| `BLOFIN_DEMO_REST_BASE_URL` | Allowlisted demo origin. |
| `EXECUTION_MODE` | Must stay `paper`. |
| `EXCHANGE_MODE` | `paper_internal` for the read-only path. |
| `ENABLE_REAL_TRADING` | Must stay false. |

A successful or degraded snapshot younger than the interval is reused, so an
open dashboard and the worker do not both call BloFin. A failed sync backs off,
doubling from the interval up to 900 seconds. The provider's existing request
rate limit still applies. Supervisor health includes `demo_snapshot` status:
`ok`, `skipped`, `backing_off`, `degraded`, or `stopped`.

The dashboard and `/exchange` refresh immediately for an owner when the gates
are open, then poll at the same interval. The card shows **Last synced** and a
stale state when the freshness window has passed or the last refresh failed.

## Activity sync Sofien must approve

The snapshot cycle does not import fills. Win rate stays empty until the
activity worker is enabled. These names are required on the worker. Approval
is required before any of them are set in a deployed environment.

| Name | Role |
| --- | --- |
| `BLOFIN_ACTIVITY_ENABLED` | Default false. `true` only with approval. |
| `BLOFIN_ACTIVITY_ORGANIZATION_ID` | Pinned organization. |
| `BLOFIN_ACTIVITY_EXPECTED_UID` | Pinned native demo UID. |
| `BLOFIN_READONLY_API_KEY` | Same dedicated read-only key. No trade permission. |
| `BLOFIN_READONLY_API_SECRET` | Dedicated read-only secret. |
| `BLOFIN_READONLY_API_PASSPHRASE` | Dedicated read-only passphrase. |
| `BLOFIN_READONLY_SYNC_ENABLED` | Required for the read-only client outside demo-exchange mode. |
| `BLOFIN_DEMO_REST_BASE_URL` | Allowlisted demo origin. |

Optional cadence names already exist: `BLOFIN_ACTIVITY_POLL_SECONDS`,
`BLOFIN_ACTIVITY_LOOKBACK_DAYS`, `BLOFIN_ACTIVITY_STALE_SECONDS`. Defaults are
unchanged.

While activity sync is disabled, the demo card says **Activity sync disabled**
and does not show a win rate or realized P&L. Manual AlphaTrade demo counts
remain a separate partial coverage and are not the account win rate.

## Trade method

Method id: `closed-round-trip-fillpnl-signed-fee/v1`.

- A trade is one position going from flat back to flat. The position key is
  `(instrument, position_side)` where `position_side` is `long`, `short`, or `net`.
- Buy increases a long or net position. Sell increases a short. The opposite
  side reduces it.
- Partial fills add together. The trade closes only when the signed size is zero.
- A fill that crosses zero is split by quantity. The closing share finishes the
  current trade. The remainder opens the next one. `fillPnl` and the signed fee
  are split by the same fraction.
- Realized P&L is the sum of native `fillPnl` plus the original signed fee.
  A negative fee reduces the result. This is used only when every slice has a
  fee, one fee currency, and that currency equals the stored settlement
  currency. Otherwise the round-trip is unresolved and is excluded from the
  win rate.
- Order `pnl` is not added, so it cannot double-count `fillPnl`. If an order's
  `filled_quantity` disagrees with the sum of its fills, those round-trips are
  unresolved.
- `win_rate` is wins / (wins + losses). A net of zero is a scratch: it is a
  trade and it is not a win or a loss. Open size is not a trade.
- The time window prefers the fill cursor coverage, then the earliest and latest
  fill in the counted trades.
- A gap, a row cap, a missing order, or an unresolved close is `partial`, not a
  complete account result.
