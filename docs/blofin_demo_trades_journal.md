# BloFin demo trades in the journal: import design (A6)

Status: design for review (docs only). No code, flag, env, or database change
comes with this document. Implementation is A7 (`claude/a-blofin-journal-impl`).
Plan: `docs/plans/2026-10-26-ui-overhaul-plan.md` (PR #333), Griller condition K1.

Survey base: `main` at 5403acc9. All line references are to that commit.

## 1. Scope and owner

- **Owner.** `backend/src/app/services/blofin_demo_manual_mirror.py` stays the
  only writer of BloFin demo rows in `journal_trades`. It already holds the
  economics (price P&L, fee, funding) and the verification rule.
- **Fact store.** `blofin_activity_service.py` and the `blofin_activity_facts`
  table keep native order and fill facts. They never write the journal. A7 may
  read fill facts as a fee cross-check (§5); it does not add journal writes there.
- **Venue access.** GET only, through the existing read-only client
  (`get_readonly_client(..., activity_history=True, paper_internal_snapshot=True)`).
  The #304 org pin and UID check in `_require_internal_read_pin` runs before any
  account read, as it does today. Paper and BloFin demo only.
- **Out of scope.** Order placement, risk and cap logic, `services/risk/`,
  migrations, and any change to `ENABLE_REAL_TRADING` or execution mode.

## 2. What the mirror does today

| Venue row | Journal ref | Code |
| --- | --- | --- |
| Closed leg (`positions-history`) | `blofin-demo-history:{historyId}` | `_draft_from_slice`, line 580 |
| Open position (`positions`) | `blofin-demo-open:{positionId}:{stamp}` | `_open_drafts`, line 660 |

`{stamp}` is the position's `createTime` when the venue sends it as a string,
otherwise the literal `na` (line 654). So the open key is already
`positionId` + open time in the normal case. The defects are elsewhere:

1. **One position, several trades.** The open tracker and the closed legs never
   link. When a position goes flat, `_persist` sets the open tracker to
   `CANCELLED` / `flat_on_venue` (lines 1044–1059), and every `positions-history`
   row creates its own `CLOSED` trade. A position with two partial closes ends as
   three rows: one cancelled tracker and two closed trades with
   `exit_reason="partially_closed"`.
2. **`na` stamp.** A missing or non-string `createTime` gives
   `blofin-demo-open:{positionId}:na`. Two lifetimes of a reused `positionId`
   then share one ref and overwrite each other.
3. **Partial closes count as trades.** Each leg carries its own win/loss, so trade
   count and win rate are inflated.
4. **Seven-day window.** `_WINDOW` (line 80) is 7 days minus 1 s for all five
   history reads, because the funding route covers at most seven days. Anything
   older is never read, so the Oct 8 trade is outside every cycle.

## 3. Identity and dedupe (K1)

### 3.1 Keys

| Thing | Key | Notes |
| --- | --- | --- |
| Closed leg | venue `historyId` | One leg claims at most one journal row. |
| Position lifetime | `(positionId, openTimeMs)` | `openTimeMs` is `createTime` parsed to integer milliseconds and printed as a decimal string. |
| Journal trade | one per position lifetime | Open phase and closed phase are the same row. |

`openTimeMs` comes from `positions.createTime` for open rows and
`positions-history.createTime` for closed legs (the mirror already reads the
latter as `opened_at`, line 314). If `createTime` is missing or does not parse,
the row is **skipped and the cycle is marked incomplete**. The `na` fallback is
removed.

### 3.2 Refs stay compatible

Recommended (option B): keep both existing ref prefixes.

- While open: `external_ref = blofin-demo-open:{positionId}:{openTimeMs}`.
- On the first closed leg seen for that lifetime, the **same row** changes ref to
  `blofin-demo-history:{historyId}` of that leg and becomes `CLOSED` once the
  position is flat. The ref never changes again after that.
- Every row carries server-only tags (the `blofin-` prefix is reserved for the
  server by `client_reserved_tag`):
  - `blofin-position:{positionId}:{openTimeMs}`, the lifetime key, and
  - `blofin-history:{historyId}` for every leg it owns (already written today by
    `_tags`).

Why B: `blofin-demo-history:` is read by risk exclusion
(`services/risk/mirror_economics.py` `HISTORY_REF_PREFIX`,
`daily_risk_accounting.py:202`), journal routes (`journal.py:676`),
`journal_trade_service.py:610/657`, and the frontend
(`lib/journal-blofin-reconciliation.ts`). Reconciled manual rows keep
`source=MANUAL`, so for them the history ref is the only thing that keeps their
economics out of risk. A new `blofin-demo-position:` prefix (option A) would have
to change all of those, including single-owner risk files. B needs none of them.

The unique index `uq_journal_trades_org_external_ref` (org, ref) still holds:
an open ref and a history ref never collide, and each history ref is a real
`historyId`.

### 3.3 Lookup order for each venue row

For a closed leg `L` with `(historyId, positionId, openTimeMs)`:

1. A row (not superseded, §6) already holding `blofin-history:{historyId}` → that
   row. Update in place only when `_should_recompute` says so.
2. Else a row tagged `blofin-position:{positionId}:{openTimeMs}` → attach `L` to it.
3. Else a row whose ref is `blofin-demo-open:{positionId}:{openTimeMs}` (legacy
   tracker without the tag) → attach `L`, add the tag.
4. Else the existing `_claim_manual` match on a user's manual open row (unchanged
   rules: same symbol, direction, entry price, size; exactly one candidate).
5. Else create a new row.

For an open position `P` with `(positionId, openTimeMs)`: steps 2, 3, 5.

**Integrity checks (abort the write, keep the cycle marker non-ok):**
- one `historyId` tagged on two live rows;
- one lifetime key tagged on two live rows;
- a leg whose `positionId` matches a row but whose `openTimeMs` falls inside
  that row's open lifetime while not equal to its key (open-time mismatch, see §3.4).

These follow the existing `ambiguous` path: log, count, skip the write.

### 3.4 `positionId` reuse

BloFin does not document whether `positionId` is unique forever. The design
assumes it may be reused:

- Same `positionId`, different `openTimeMs` → a different lifetime and a
  different journal row. Logged as `blofin_demo_position_id_reused` (counts only,
  no ids in the log).
- A leg with a known `positionId` and an `openTimeMs` that matches no lifetime is
  a new lifetime, unless an open row for that `positionId` is still live on the
  venue with a different `createTime`. That case is the mismatch in §3.3 and is
  withheld, because it would mean `positions.createTime` and
  `positions-history.createTime` disagree for one position.
- The dry-run (§7) reports, from the stored legacy open trackers, how often
  `positions.createTime` equals the matching `positions-history.createTime`. If
  they ever differ, A7 stops and this section is revised before any write.

## 4. Partial closes (K1)

One journal row per lifetime. Each venue history record is one leg.

| Field | Rule |
| --- | --- |
| `size` | Σ `closePositions × contractValue` over owned legs. While still open: closed legs plus the live `positions` quantity × `contractValue`. |
| `entry_price` | Size-weighted mean of leg `openAveragePrice`. While open with no legs: `positions.averagePrice`. |
| `exit_price` | Size-weighted mean of leg `closeAveragePrice`. |
| `entry_time` | `openTimeMs`. |
| `exit_time` | Latest leg `updateTime`. |
| `gross_pnl` | Σ leg price P&L. Each leg must match its own `realizedPnl` (today's `price_matches` rule, per leg). |
| `fees` | Σ leg `fee` (a negative fee stays a rebate, as today). |
| `funding` | Σ leg funding from today's `_assign_funding`, which already gives each leg the bills after the previous leg's close. |
| `net_pnl` | `gross − fees − funding`, only when every leg is verified. |
| `result` | From `net_pnl`, once `CLOSED`. `OPEN` before that. |
| `status` | `OPEN` while the lifetime is on `positions`; `CLOSED` once it is gone and at least one leg is owned. |
| `exit_reason` | `closed` when Σ `closePositions` = max `maxPositions` across legs; else `partially_closed_incomplete`, and the row is unverified. |

While a lifetime is open with some legs closed, the row stays `OPEN` with
`net_pnl` null. Realized-so-far is reported in the dry-run only. A lifetime that
is open and has a leg older than the window is unverified, as today
(`opened_at < window_begin`).

A row is **verified** only when every owned leg is verified and the
`closePositions` sum check passes. Otherwise it is tagged `pnl-unverified`, and
the dashboard headline stays withheld under the existing rule.

## 5. Fees and funding

- **Fees.** `positions-history.fee` per leg is the source of record, as today.
  When the activity fact store holds fills for the leg's orders, A7 compares
  Σ fill `fee` with Σ leg `fee` and reports mismatches in the dry-run. A mismatch
  makes the row unverified; it never replaces the leg fee.
- **Funding.** `GET /api/v1/account/funding-fees` covers at most seven days. A leg
  whose funding window starts before the oldest funding bill we can read gets
  `funding = null` and stays unverified. Funding is never assumed zero.

## 6. Ghost rows: gated cleanup (K1)

**Definition.** A ghost is a mirror row that no longer stands for one live or
closed venue lifetime:

| Class | What it is | Today |
| --- | --- | --- |
| G1 | Cancelled tracker (`blofin-demo-open:`, `flat_on_venue`) whose lifetime has closed legs on other rows | The duplicate described in §2.1 |
| G2 | Extra legacy closed leg rows of one lifetime (`partially_closed`) | Counted as separate trades |
| G3 | Cancelled tracker with no history leg found in any read window | Unresolved |
| G4 | `blofin-demo-open:{positionId}:na` rows | Unkeyable |

**Rules.**

1. The backfill script defaults to `--dry-run` and only lists G1–G4 with the
   proposed action. Nothing is written in dry-run.
2. **No deletes, ever.** The proposed actions are updates only:
   - G1 and G2 → merge into one survivor. The survivor keeps a
     `blofin-demo-history:` ref of one of its legs (the row with user notes or
     lessons if exactly one has them, else the earliest closing leg; two or more
     annotated rows → listed for a manual call, no action). The others become
     `status=CANCELLED`, `exit_reason="superseded"`, tag
     `blofin-superseded-by:{survivor ref}`. Superseded rows own no legs.
   - G3 and G4 → listed only. No action is proposed.
3. Any update from the list runs only after Sofien approves that dry-run output in
   his own message. The applied run takes the approved dry-run's digest and
   refuses if the computed plan differs.
4. In the live cycle, A7 stops creating new ghosts: a tracker whose lifetime has
   closed legs becomes the closed row (§3.3) instead of being cancelled. A tracker
   whose position is gone with no leg found keeps today's reversible
   `CANCELLED` / `flat_on_venue` status so it does not count as open exposure.
   Griller to confirm this live-cycle status change is acceptable without the
   per-run approval in rule 3; if not, A7 leaves those rows untouched and only
   reports them.

A7 must confirm, with a test, that `CANCELLED` rows are excluded from journal
statistics and the daily trade count before it relies on rule 2.

## 7. The 7-day window, backfill, and the dry-run diff

### 7.1 Windows

- The live cycle keeps `_WINDOW` (7 days). That is set by the funding route.
- **The 30-day figure is our own config ceiling, not BloFin retention.**
  `BloFinActivitySettings.lookback_days` is `Field(default=7, ge=1, le=30)`
  (`blofin_activity_config.py:21`). BloFin's retention for
  `positions-history`, fills, and orders is not documented. The plan's
  "Oct 8 could age out around Nov 7" estimate came from that config ceiling and is
  not a venue fact. Retention is treated as unknown, so the dry-run runs as early
  as possible.

### 7.2 Backfill path

`backend/scripts/blofin_demo_journal_backfill.py` (A7), `--dry-run` by default:

- `--since YYYY-MM-DD` (default `2026-10-01`), walked in 7-day chunks up to now
  for `positions-history`, fills, and orders; funding for whatever the route
  returns.
- Same `drafts_from_raw` → lifetime grouping as the live cycle, so a rerun of
  either path converges on the same rows.
- For legs older than funding coverage: exit, gross P&L, and fees are written;
  funding and net stay null; tag `pnl-unverified`. (The Oct 8 AC: exit, P&L,
  and fees present; funding only if the venue returns it.)
- `--capture PATH` writes the raw venue pages read (no headers, no keys, no UID)
  so an approved apply can replay the same data even if the venue later drops it.
  This is the retention hedge.
- Paper-only guard and the read pin run first, as in `apply`.

### 7.3 Dry-run diff format

JSON Lines to stdout (or `--out`), then one summary object.

```json
{"action":"create|attach|close|merge|supersede|list_ghost|skip",
 "lifetime":"<positionId>:<openTimeMs>",
 "journal_trade_id":"<uuid or null>",
 "ref_before":"<ref or null>","ref_after":"<ref or null>",
 "legs":["<historyId>", "..."],
 "fields":{"status":["open","closed"],"size":["..",".."],"net_pnl":[null,".."]},
 "verified":false,"reasons":["funding_before_coverage","fee_mismatch_fills"],
 "ghost_class":"G1|G2|G3|G4|null"}
```

```json
{"summary":{"window":["2026-10-01T00:00:00Z","..."],"legs_read":0,"lifetimes":0,
 "creates":0,"updates":0,"merges":0,"supersedes":0,"ghosts":{"G1":0,"G2":0,"G3":0,"G4":0},
 "position_id_reused":0,"open_time_mismatch":0,"fee_mismatch":0,
 "legs_per_lifetime":{"1":0,"2":0,"3+":0},"plan_digest":"sha256:..."}}
```

Field values are the economics only. No API key, secret, passphrase, account UID,
or email appears in the output or logs.

## 8. Flag and env state

- `blofin_demo_manual_mirror_enabled` (`BLOFIN_DEMO_MANUAL_MIRROR_ENABLED`)
  defaults to `False` (`core/config.py:334`) and is not set in `render.yaml`.
  The mirror also needs `BLOFIN_DEMO_SNAPSHOT_SYNC_ENABLED` and an armed paper
  worker (`docs/blofin_demo_manual_mirror.md`).
- Turning the flag on in staging, adding the backfill credentials there, or running
  the applied backfill are env/approval gates: Sofien's own message, not an agent.
- The dry-run itself is read-only, but it uses BloFin demo credentials and the
  staging database, so who runs it is also Sofien's call.

## 9. Timeline

- **Before Nov 1, 2026:** this doc lands (Griller GO), then A7a (dry-run + capture
  only, read-only, no journal writes) lands and the dry-run runs with `--capture`.
- After Sofien approves the diff: A7b (live-cycle grouping and the gated apply).
  The A7b apply (`--apply --plan-digest sha256:...`) re-plans in one transaction,
  refuses a digest mismatch, and only inserts `create` lines of closed lifetimes
  through the live cycle's `_apply_venue`. A plan with `attach`, `close`, `merge`
  or `supersede` lines is refused whole (those updates stay a later, separately
  approved step). A statement guard refuses any `DELETE` and any `UPDATE` of
  `journal_trades`; the venue client is GET-only; the #304 read pin runs first,
  also with `--replay`. After the inserts the plan is rebuilt and must show 0
  creates and 0 updates before commit.

## 10. Tests A7 must add

- Rerun of the same venue data → 0 creates, 0 updates (live cycle and backfill).
- Open → partial close → full close: one row throughout; size, P&L, and fee sums.
- Two lifetimes of one `positionId` → two rows; `position_id_reused` counted.
- Missing `createTime` → skipped, cycle incomplete, no `na` ref.
- `historyId` on two live rows → write aborted, marker not ok.
- Legacy G1/G2 data → merge plan in dry-run; zero writes without `--apply`;
  `--apply` refuses a digest mismatch; no `DELETE` issued (assert on SQL).
- Leg older than funding coverage → funding null, `pnl-unverified`.
- `CANCELLED` rows excluded from journal statistics and daily trade count.

## 11. Open questions for Griller

1. Option B refs (§3.2) versus a new prefix.
2. Live-cycle `flat_on_venue` for unmatched trackers without per-run approval (§6 rule 4).
3. Whether backfilled rows with null funding should stay outside the headline
   win rate forever (current rule) or get a separate "before funding" figure.
