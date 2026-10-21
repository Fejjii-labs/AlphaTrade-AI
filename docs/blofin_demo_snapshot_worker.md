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
  `paper_exchange_demo` and `paper_internal` both qualify. Real trading does not.
- `GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID` and `GOVERNED_BLOFIN_DEMO_USER_ID`
  are set. The cycle does not guess an organization.
- Either the dedicated read-only set is complete
  (`BLOFIN_READONLY_SYNC_ENABLED` plus `BLOFIN_READONLY_API_KEY`,
  `BLOFIN_READONLY_API_SECRET`, and `BLOFIN_READONLY_API_PASSPHRASE`), or that
  set is absent and the main demo key is present (`BLOFIN_API_KEY`,
  `BLOFIN_API_SECRET`, `BLOFIN_API_PASSPHRASE`). The shared demo key is
  accepted in `EXCHANGE_MODE=paper_exchange_demo` with `BLOFIN_DEMO_ENABLED=true`,
  or in `EXCHANGE_MODE=paper_internal` with
  `BLOFIN_DEMO_SNAPSHOT_INTERNAL_READ_ENABLED=true`. The internal flag is
  separate from `BLOFIN_DEMO_ENABLED` so the Telegram staging projection can
  stay on `paper_internal` and live USD-M evidence. It does not route orders.
  A partial read-only set does not fall back. The worker logs booleans and
  the UID identity result (`verified` or `mismatch`) only. Snapshot and read
  sync issue GET requests through the allowlisted demo client. They do not
  place orders. The activity worker still requires the dedicated read-only
  key.

Otherwise the cycle is not attached, or its status is `skipped`, and it does
not open the database or call BloFin. Identity and UID checks for a snapshot
fetch stay inside `BloFinSyncService`. The dedicated key and the main demo-key
fallback both
refuse a native UID that differs from `BLOFIN_ACTIVITY_EXPECTED_UID` for
`BLOFIN_ACTIVITY_ORGANIZATION_ID`, and both apply the activity-account
snapshot proof on read. The fallback also refuses before any venue call
when `BLOFIN_ACTIVITY_ORGANIZATION_ID` is unset, when that organization
does not match the sync organization, or when `BLOFIN_ACTIVITY_EXPECTED_UID`
is unset. The refusal log is `blofin_fallback_sync_refused` plus a reason
token. A dedicated key that can trade is still refused.
The main demo key is not refused only because it can trade. The worker
records actor `system` and provenance `sync_origin=worker`. When the
read-only set is absent, the dashboard refresh uses the same GET-only client
and the main demo key. It does not place orders.

| Name | Role |
| --- | --- |
| `BLOFIN_DEMO_SNAPSHOT_SYNC_ENABLED` | Default false. Required before the worker attaches the cycle. |
| `BLOFIN_DEMO_SNAPSHOT_INTERNAL_READ_ENABLED` | Default false. Required in `paper_internal` before the main demo key may back the GET snapshot. Does not open orders or `BLOFIN_DEMO_ENABLED`. |
| `BLOFIN_DEMO_SNAPSHOT_SYNC_SECONDS` | Cadence, 30–60. Default 45. Used only after the flag is on. |
| `GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID` | Owner organization pin. |
| `GOVERNED_BLOFIN_DEMO_USER_ID` | Owner user pin. |
| `BLOFIN_API_KEY` | Main demo key. Used for GET sync when the read-only set is absent. |
| `BLOFIN_API_SECRET` | Main demo secret. Value stays in the environment. |
| `BLOFIN_API_PASSPHRASE` | Main demo passphrase. Value stays in the environment. |
| `BLOFIN_READONLY_SYNC_ENABLED` | Optional. Selects the dedicated read-only set when that set is complete. |
| `BLOFIN_READONLY_API_KEY` | Optional dedicated read-only key. Not required when the set is absent. |
| `BLOFIN_READONLY_API_SECRET` | Optional dedicated read-only secret. |
| `BLOFIN_READONLY_API_PASSPHRASE` | Optional dedicated read-only passphrase. |
| `BLOFIN_DEMO_REST_BASE_URL` | Allowlisted demo origin. |
| `EXECUTION_MODE` | Must stay `paper`. |
| `EXCHANGE_MODE` | `paper_exchange_demo` for the shared demo key with `BLOFIN_DEMO_ENABLED=true`. `paper_internal` accepts a complete read-only set, or the shared demo key when `BLOFIN_DEMO_SNAPSHOT_INTERNAL_READ_ENABLED=true`. |
| `ENABLE_REAL_TRADING` | Must stay false. |

A successful snapshot younger than the interval is reused and the cycle stays
ok. A degraded snapshot in that window is also reused, and the cycle reports
degraded, so the worker does not call BloFin again while it is fresh. An owner
refresh from the dashboard still calls BloFin. When
`BLOFIN_DEMO_MANUAL_MIRROR_ENABLED` is on, the same cycle still mirrors hand
trades while the snapshot is fresh, including a fresh degraded snapshot, and
also when the snapshot sync returns a health status outside ok or degraded.
On the paper_internal main-key path that mirror runs the #304 organization
pin and UID check before any account read, including this fresh skip. See
[blofin_demo_manual_mirror.md](blofin_demo_manual_mirror.md). The property
`blofin_paper_internal_main_key_selected` is what selects that main
`BLOFIN_API_*` key, before the demo-host allowlist.
The mirror does not run unless this cycle is attached: the snapshot flag is
on, the worker is armed, and either the read-only set or the main demo key
fallback above is present. A partial read-only set still skips with
`readonly_credentials_required` and does not open the mirror. A degraded
snapshot does not back off. A health status outside ok or degraded still
backs off. A mirror read failure does not back off the snapshot.
A failed snapshot sync backs off, doubling from the interval up to 900 seconds.
The provider's existing request
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
Decisions: AT-ADR-113, AT-ADR-114, and AT-ADR-115.

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
Turning it on does not attach the cycle by itself. The snapshot flag, an
armed worker, and the read credential setup above are still required. The
mirror uses the same GET-only client. This document names the flag only. Do
not set it in `render.yaml` or in a deployed environment from this change.
Contract, formulas, and the journal source `blofin_demo_manual` are in
[blofin_demo_manual_mirror.md](blofin_demo_manual_mirror.md).

## Staging env names for sync and autonomous demo orders

No values belong in this document. `BLOFIN_READONLY_*` is not required when
that set is unset. Do not set both `AUTONOMOUS_INTERNAL_PAPER_ENABLED` and
`AUTONOMOUS_BLOFIN_DEMO_ENABLED`. Both flags together fail closed. Production
still refuses `paper_exchange_demo` and both autonomous flags. Real trading
stays off.

Worker names for snapshot sync and autonomous demo orders:

- `ENVIRONMENT`
- `EXECUTION_MODE`
- `ENABLE_REAL_TRADING`
- `EXCHANGE_MODE`
- `BLOFIN_DEMO_ENABLED`
- `BLOFIN_API_KEY`
- `BLOFIN_API_SECRET`
- `BLOFIN_API_PASSPHRASE`
- `BLOFIN_DEMO_REST_BASE_URL`
- `BLOFIN_DEMO_SNAPSHOT_SYNC_ENABLED`
- `BLOFIN_DEMO_SNAPSHOT_INTERNAL_READ_ENABLED`
- `GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID`
- `GOVERNED_BLOFIN_DEMO_USER_ID`
- `GOVERNED_BLOFIN_DEMO_ACCOUNT_ID`
- `GOVERNED_BLOFIN_DEMO_ENABLED`
- `AUTONOMOUS_BLOFIN_DEMO_ENABLED`
- `WATCHER_ORCHESTRATION_ENABLED`
- `WATCHER_PAPER_STAGING_ACTIVATION`
- `WATCHER_PAPER_ORGANIZATION_ID`
- `BLOFIN_ACTIVITY_ORGANIZATION_ID`
- `BLOFIN_ACTIVITY_EXPECTED_UID`

`BLOFIN_ACTIVITY_ORGANIZATION_ID` must equal `GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID`.
`BLOFIN_ACTIVITY_EXPECTED_UID` is the native demo account id. The main demo-key
fallback refuses to sync when either name is unset or the organization does
not match. `BLOFIN_ACTIVITY_ENABLED` stays unset for this pin. Leave
`AUTONOMOUS_INTERNAL_PAPER_ENABLED` unset. The worker must be armed. A
disarmed boot does not attach the snapshot cycle. Optional mirror name:
`BLOFIN_DEMO_MANUAL_MIRROR_ENABLED`.

API names for dashboard read sync. The API uses the same demo key and mode.
It does not need the snapshot flag or the autonomous flag:

- `ENVIRONMENT`
- `EXECUTION_MODE`
- `ENABLE_REAL_TRADING`
- `EXCHANGE_MODE`
- `BLOFIN_DEMO_ENABLED`
- `BLOFIN_API_KEY`
- `BLOFIN_API_SECRET`
- `BLOFIN_API_PASSPHRASE`
- `BLOFIN_DEMO_REST_BASE_URL`
- `GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID`
- `BLOFIN_ACTIVITY_ORGANIZATION_ID`
- `BLOFIN_ACTIVITY_EXPECTED_UID`

`BLOFIN_ACTIVITY_ORGANIZATION_ID` must equal `GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID`.
`BLOFIN_ACTIVITY_EXPECTED_UID` is the same native demo account id as on the
worker. Read sync on the fallback key refuses without both names. Set the
organization id to the same organization the worker pins, so the
dashboard scope matches the snapshot owner. Activity sync is unchanged: its
identity check still requires a key the venue reports as read-only, so the
Read+Trade fallback does not satisfy `BLOFIN_ACTIVITY_ENABLED`.
