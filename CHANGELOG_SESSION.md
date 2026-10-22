# Session changelog — 2026-10-22

## #289 Journal trade owner authorization

- Added `_load_owned_journal_trade` helper and applied owner checks to GET trade detail (all sources), POST evidence/rule-checks/observations, and attachment upload/list/download.
- Extended `test_at030_journal_trades.py` and `test_at033_journal_attachments.py` for cross-owner 404 and unchanged rows after refused writes.
- Draft PR: https://github.com/Fejjii/AlphaTrade-AI/pull/310 (head `3f498f33`).
