# Session changelog — 2026-10-20

## PR 288 — snapshot audit IDs + reconciled journal detail

- BloFin demo snapshot worker: unique `blofin-snapshot-{uuid}` audit `request_id` per sync run; test added.
- Journal trade detail: reconciled manual / manual_demo_test rows show badge, origin, close reason, venue history id; strategy annotation preserved.
- Focused tests: backend 2 passed, frontend 14 passed on cloud VM.
- Draft PR: https://github.com/Fejjii/AlphaTrade-AI/pull/288 (head `ffb3e858`).
