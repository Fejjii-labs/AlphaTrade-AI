# BloFin demo snapshot worker and activity win rate

Read-only. This does not place, cancel, or amend orders, and it does not enable
real-money trading. Turning the activity worker on requires Sofien's approval.
Set the names below on the worker only after that approval. Do not put secret
values in the repository, logs, or pull requests.

## Snapshot cycle

The paper worker attaches `BloFinSyncService.sync()` only when all of these
are true:

- `BLOFIN_DEMO_SNAPSHOT_SYNC_ENABLED` is true. The default is false, so a
  merge does not start polling.
- The worker is armed. A disarmed staging boot does not construct the cycle,
  a database session, or an exchange client.
- The existing demo gates are open (`demo_account_active`).
- `GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID` and `GOVERNED_BLOFIN_DEMO_USER_ID`
  are set. The cycle does not guess an organization.
- `BLOFIN_READONLY_SYNC_ENABLED` is true and `BLOFIN_READONLY_API_KEY`,
  `BLOFIN_READONLY_API_SECRET`, and `BLOFIN_READONLY_API_PASSPHRASE` are
  present. The unattended worker never falls back to the demo trading keys.
  A missing read-only credential skips with a log of booleans only.

Otherwise the cycle is not attached, or its status is `skipped`, and it does
not open the database or call BloFin. Identity and UID checks stay inside
`BloFinSyncService.sync`. The worker records actor `system` and provenance
`sync_origin=worker`. The interactive dashboard refresh can still use the
existing demo path; that path is not the unattended worker.

| Name | Role |
| --- | --- |
| `BLOFIN_DEMO_SNAPSHOT_SYNC_ENABLED` | Default false. Required before the worker attaches the cycle. |
| `BLOFIN_DEMO_SNAPSHOT_SYNC_SECONDS` | Cadence, 30–60. Default 45. Used only after the flag is on. |
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

A successful or degraded snapshot younger than the interval is reused, so the
worker does not call BloFin again for that snapshot while it is fresh. An
owner refresh from the dashboard still calls BloFin. When
`BLOFIN_DEMO_MANUAL_MIRROR_ENABLED` is on, the same cycle still mirrors hand
trades while the snapshot is fresh, and also when the snapshot sync returns
a health status outside ok or degraded. That unhealthy snapshot still backs
off. A mirror read failure does not back off the snapshot. A failed snapshot sync backs off, doubling from the interval
up to 900 seconds. The provider's existing request
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

Method id: `closed-round-trip-fillpnl-fee-recorded/v1`.

- A trade is one position going from flat back to flat. The position key is
  `(instrument, position_side)` where `position_side` is `long`, `short`, or `net`.
- Buy increases a long or net position. Sell increases a short. The opposite
  side reduces it.
- Partial fills add together. The trade closes only when the signed size is zero.
- A fill that crosses zero is split by quantity. The closing share finishes the
  current trade. The remainder opens the next one. `fillPnl` and the signed fee
  are split by the same fraction.
- The structural result is the sum of native `fillPnl`. The original signed
  fee is recorded and is not added. Fee sign, and whether `fillPnl` is gross
  or net of fees or funding, are not independently verified. A slice still
  needs a fee, one fee currency, and that currency equal to the stored
  settlement currency before the structural round-trip is complete. Otherwise
  it is unresolved.
- The dashboard does not publish `win_rate` or realized P&L from this method.
  A stored fill cursor, including a complete lookback, is not proof that each
  position started flat. Until both that proof and an independent fee basis
  exist, the card withholds those figures and the status stays `partial`.
- Order `pnl` is not added, so it cannot double-count `fillPnl`. If an order's
  `filled_quantity` disagrees with the sum of its fills, those round-trips are
  unresolved.
- In the structural function, `win_rate` is wins / (wins + losses) on `fillPnl`.
  A net of zero is a scratch: it is a trade and it is not a win or a loss.
  Open size is not a trade. That function is not what the dashboard publishes.
- The published time window is the fill cursor coverage. It is context for the
  withheld figures, not a scored interval.

## Hand-traded demo mirror

The same snapshot cycle can mirror Sofien's hand trades into the journal.
That path is separate from `fillPnl`. Official trade-history wording still
does not say whether `fillPnl` is gross or net of fees or funding, so the
activity win rate above stays withheld.

The mirror flag is `BLOFIN_DEMO_MANUAL_MIRROR_ENABLED`. It defaults off.
This document names the flag only. Do not set it in `render.yaml` or in a
deployed environment from this change. Contract, formulas, and the journal
source `blofin_demo_manual` are in
[blofin_demo_manual_mirror.md](blofin_demo_manual_mirror.md).
