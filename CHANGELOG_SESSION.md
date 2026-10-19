# Session changelog

Generated At UTC: 2026-10-19T09:41:20Z
Timezone: Etc/UTC
Source File SHA256: 126701c8b5f3ac699ad0760e014df919f96ae37682e2e5bfe36469b2a691f8ad

## 2026-10-19

- Merged PR 280 final head `00c5baafb2f7be309f3354b2d9e49288a6543403` into `cursor/blofin-demo-manual-journal-c6b5` with `git merge`, not a rebase. Merge commit `81fe061a`. Parents `77a507eb` and `00c5baaf`.
- Snapshot decisions in `.ai/DECISIONS.md` are only AT-ADR-113, AT-ADR-114, and AT-ADR-115. The leftover snapshot AT-ADR-109 heading is gone. The mirror decision remains AT-ADR-112 and now cites AT-ADR-114 for the activity withhold.
- Docs state that the mirror requires `BLOFIN_DEMO_SNAPSHOT_SYNC_ENABLED`, an armed worker, and the `BLOFIN_READONLY_*` setup. Trading-key tests still expect `readonly_credentials_required`.
- A fresh degraded snapshot is reported as degraded without another BloFin call. The mirror still runs on that path and before an unhealthy snapshot backs off.
- Local tests before the push: pytest 29 mirror, 17 snapshot, 19 journal statistics, all passed. Vitest JournalTradeScreen and BloFinDemoAccountCard: 36 passed.
- First push `81fe061a` CI run 39229289762 failed ruff format. Format-only commit `69ed701d` CI run 39230228183 succeeded.
- Grok review 5569572016 on `69ed701dac2d37de638ede681c7432388bddcc93`: VERDICT CHANGES_REQUIRED. Required corrections: statistics must not KeyError on a closed reconciled `manual_demo_test` row, and that venue net must not enter daily realized P&L or the daily lock.
- PR 282 remains a draft. The body no longer says "Draft. Do not merge". Flags stay off. The pull request was not merged. No workflow_dispatch.
