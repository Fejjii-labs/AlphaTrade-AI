# BloFin demo hand-trade journal mirror

Read-only. This path does not place, cancel, or amend orders, and it does not
enable real-money trading. It runs inside the existing BloFin demo snapshot
worker on each cycle, including when the saved snapshot is still inside the
refresh interval. The snapshot itself is not fetched again while it is fresh.
A mirror read failure rolls back only the mirror write and does not back off
the snapshot cycle.

The flag name is `BLOFIN_DEMO_MANUAL_MIRROR_ENABLED`. It defaults off. This
document names the flag only. It is not set in `render.yaml` and this change
does not edit environment variables or Render.

While the flag is off, the demo card does not publish a hand-trade win rate.
The activity-worker `fillPnl` figures stay withheld for the reason in
[blofin_demo_snapshot_worker.md](blofin_demo_snapshot_worker.md).

## Endpoints

All calls are GET. The client is the dedicated read-only BloFin demo client.
Unknown paths and every non-GET method are refused before a request is sent.

| Endpoint | Role |
| --- | --- |
| `GET /api/v1/account/positions-history` | One closed or partial-close slice. |
| `GET /api/v1/account/funding-fees` | Funding bills. Positions history has no funding field. |
| `GET /api/v1/trade/fills-history` | Order ids only. `fillPnl` is not used. |
| `GET /api/v1/trade/orders-history` | `slTriggerPrice` and `tpTriggerPrice` when the opening order carried them. |
| `GET /api/v1/account/positions` | Open remainder after a partial close. |
| `GET /api/v1/market/instruments` | `contractValue` and `settleCurrency`. |

Official pages, read 2026-10-18:

- Positions history: <https://docs.blofin.com/index.html#get-positions-history>
- Funding fee history: <https://docs.blofin.com/index.html#get-funding-fee-history>
- Trade history (`fillPnl`): <https://docs.blofin.com/index.html#get-trade-history>
- Order history (fee described as "Fee and rebate"): <https://docs.blofin.com/index.html#get-order-history>

## Field semantics

`realizedPnl` is documented as "Realized profit and loss". The published ETH
row (side `buy`, open 1591.8, close 1592.5, `closePositions` 1, `realizedPnl`
`0.7`, `fee` `0.190536`) equals `(close - open) * contracts` and does not
subtract `fee`. `fee` is "Trading fee". Both published examples are positive.
The positions-history page does not define a negative fee.

`fundingFee` is "A positive value means received; a negative value means
paid." The funding route requires `begin` and `end` and covers at most seven
days. The mirror looks back seven days minus one second so that call stays
inside the documented window.

`fillPnl` is "Last filled profit and loss, applicable to orders which have a
trade and aim to close position." The page does not say whether that number
is gross or net of fees or funding. The published examples are `0`. This
mirror ignores `fillPnl`. An older note in
`docs/blofin_reconciliation_presentation_repair.md` called native exit
`fillPnl` gross trading P&L. That sentence is not the current official
wording, so it does not resolve the ambiguity. The activity win rate from
PR 280 stays withheld.

`side` is documented as "Position side, buy or sell". This mirror maps `buy`
to long and `sell` to short. A `positionSide` of `long` or `short` that
disagrees is a conflict and the row is not verified. The published BCH
example is `side=sell`, open 1890, close 1887, `closePositions` 290,
`realizedPnl` `-8.7`. That figure matches a long loss,
`(1887 - 1890) * 290 * 0.01`, and does not match the short formula. The row
stays unverified. The example is not treated as proof of the side mapping.

## Formulas

`size` is `closePositions * contractValue` (base quantity).

Price P&L, same shape as paper close economics:

- long: `(closeAveragePrice - openAveragePrice) * size`
- short: `(openAveragePrice - closeAveragePrice) * size`

A closed journal row is verified only when that price P&L equals
`realizedPnl` after quantizing both to 1e-8, `settleCurrency` is `USDT`,
`fee` is present, and funding for the slice is inside the fetched window and
is not shared with a different position on the same instrument.

Journal fee: a non-negative positions-history `fee` is a cost. A negative
`fee` is stored as a rebate (a negative fees amount) so subtraction adds it
back. The negative-fee case is inferred from the order-history label
"Fee and rebate" and from the existing reconciliation note that BloFin fill
fees use a positive cost and a negative rebate. Positions history itself does
not say that.

Journal funding is `-fundingFee`, so paid is positive and received is
negative. Net, only when the row is verified:

`net = price P&L - fee - funding`

Slippage is `0` on a verified row. Unverified closed rows stay `CLOSED` with
result `OPEN` and empty gross, net, fees, and funding, and the tag
`pnl-unverified`. Verified rows carry `pnl-verified`.

R is `net / planned_risk_amount` when a protective stop is known.
A long stop must be below the entry. A short stop must be above it. One
distinct stop or target is stored. Several candidates store none.
`planned_stop_price` and `planned_risk_amount` are filled only when the stop
is still empty, so a later tag does not overwrite a plan someone already set.

## Journal identity

Source for a new row is `blofin_demo_manual`. Exchange is `BLOFIN_DEMO`.
Timeframe is `manual`.

Closed slice key: `blofin-demo-history:{historyId}`.
Open remainder key: `blofin-demo-open:{positionId}:{createTime}`.

The organization already has a unique index on `(organization_id, external_ref)`.
A rerun updates the same row. Economics are recomputed when the stored
`blofin-history:` tags are empty or a subset of the ids just fetched. If the
seven-day window has dropped an older slice, the stored economics are left
as they are.

A partial close is one closed row per `historyId`, plus an open tracker for
the remainder still returned by `GET /api/v1/account/positions`. When that
position disappears, the open tracker is cancelled with `flat_on_venue`.
Its result stays `OPEN` and its net stays empty, so it is not a second trade
and it is not a win or a loss.

An open AlphaTrade row with source `manual_demo_test` or `manual`, exchange
empty or `BLOFIN_DEMO`, no `external_ref`, and the same normalized symbol,
direction, entry, and size is updated in place. Source stays
`manual_demo_test` or `manual`, so the row is not a second copy and it is
not included in the hand-trade win rate. More than one match is logged and
skipped. The stuck open example
`912f2bfd-9061-4196-82bd-a17a0650c14e` is this shape when the venue slice
matches. This repository change does not call BloFin, so it does not prove
that the live row matches a venue close.

## Tags after the mirror

`PATCH /journal/trades/{id}/annotation` accepts `strategy_label`,
`user_strategy_id`, `grade`, and `notes`. It does not accept prices, size,
fees, or P&L. An empty string clears strategy, grade, or notes. Grade is
`A` through `D` with an optional `+` or `-`, or `F`, stored as one `grade:`
tag so the journal table does not need a new column. There is no Alembic
revision. The source value fits the existing portable VARCHAR enum.

The journal entry shows the form when the source is `blofin_demo_manual` or
the external ref starts with `blofin-demo-`. The annotation route accepts
the same reconciled rows: source `blofin_demo_manual`, or source
`manual_demo_test` or `manual` whose external ref starts with
`blofin-demo-`. Other sources are rejected. Strategy analytics groups a
`blofin_demo_manual` row by its
strategy id, or by the trimmed strategy label when no id is set.

## What the card shows

Win rate, realized P&L, average R, and count come from closed
`blofin_demo_manual` rows for the organization.

- Flag off: `manual_mirror_status=disabled`. Headline numbers stay empty.
  The reason still names the unresolved `fillPnl` basis.
- Flag on, and any closed mirrored row is unverified: status `withheld`.
  Win rate, realized P&L, count, wins, and losses are empty.
- Flag on, and every closed mirrored row is verified (including none):
  status `ready`. Win rate is wins / (wins + losses). A net of zero is a
  scratch: it counts as a trade and it is not a win or a loss. Average R is
  the mean of `net / planned_risk` where risk is positive, quantized to
  0.0001. The method id is `positions-history-price-pnl-fee-funding/v1`.

The activity `fillPnl` method id stays
`closed-round-trip-fillpnl-fee-recorded/v1` and is still not published.

## Open questions

- The BCH `sell` example disagrees with sell = short. Rows that fail the
  price check stay unverified and withhold the headline.
- Funding is not on positions history. Coverage stops at seven days. A slice
  that opened before the window is unverified even when the price matches,
  including the published ETH row if it is fetched with a current window.
- `fillPnl` gross versus net is still undocumented.
- A negative positions-history fee is treated as a rebate by inference, not
  by an explicit sentence on that page.
- Reconciliation matches one slice's size. It does not add several partial
  closes into one existing manual row.
- Stops come from orders-history trigger prices, not from a separate TPSL
  history route.
- Contract value and USDT settlement are required. Anything else is unverified.
- The mirror runs on every snapshot-worker cycle while the flag is on,
  including while the saved snapshot is still fresh. A failed mirror does
  not delay the next snapshot.
