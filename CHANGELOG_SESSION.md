# Session changelog

Generated At UTC: 2026-10-19T10:42:00Z
Timezone: Etc/UTC
Source File SHA256: 17bde1625df2f6a84003ce67a17786d3ab9657ef61df82e9a3eac31bfac7fb5c

## 2026-10-19

- Commit `b1d31b7198b77b4ceee33aae58493b6c1cca71c4` on `cursor/blofin-demo-manual-journal-c6b5` limits `journal_mirror_economics()` to source `blofin_demo_manual` or the server tag `blofin-reconciled`. A `blofin-demo-` external ref alone does not exclude a row.
- Generic journal create and update reject a client `blofin-demo-` external ref and a client change to `blofin-reconciled` with HTTP 422. `external_ref` is a venue fact. Import and venue correction reject the reserved identity as well.
- `test_generic_update_cannot_clear_a_paper_loss` keeps a `paper_execution` loss in the daily lock, realized P&L, and the cooldown. Tagged mirror rows stay out. A prefix-only `manual_demo_test` loss still counts.
- Statistics still classify by source. Promotion still sums `PaperTrade.net_pnl`. Daily realized P&L, exposure, and the cooldown use the same server-marker predicate.
- Local checks before the one push: ruff format, ruff check, and mypy on the changed modules passed. Pytest of the mirror, snapshot-worker, journal-statistics, and journal-import files plus the unreconciled manual-demo daily-risk test and the append-only venue-correction test exited 0.
- CI run 39236932539 on `b1d31b71` succeeded.
- Grok review 5570350418: VERDICT CHANGES_REQUIRED. REVIEWED_HEAD_SHA `b1d31b7198b77b4ceee33aae58493b6c1cca71c4`. The mirror candidate query is organization-wide and can stamp `blofin-reconciled` on another member's matching open row.
- PR 282 remains a draft. Flags stay off. The pull request was not merged. No workflow_dispatch.
