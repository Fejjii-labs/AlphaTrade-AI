"""Dry-run plan for BloFin demo trades in the journal (A6 §7, A7a).

Usage:
  cd backend
  uv run python scripts/blofin_demo_journal_backfill.py --org <uuid> --user <uuid>
  uv run python scripts/blofin_demo_journal_backfill.py --org <uuid> --user <uuid> \\
      --since 2026-10-01 --capture venue.json --out plan.jsonl
  uv run python scripts/blofin_demo_journal_backfill.py --org <uuid> --user <uuid> \\
      --replay venue.json

Read-only. Venue access is GET through the read-only client after the paper
guard and the #304 read pin. The journal is read inside a READ ONLY
transaction that is rolled back. There is no apply mode in this script; the
applied run is A7b and needs Sofien's approval of this plan's digest.
Output is JSON Lines (one per proposed action) and one summary object. It
holds economics only: no key, secret, passphrase, account UID or email.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from app.core.config import ExecutionMode, get_settings
from app.db.session import get_session_factory
from app.services.blofin_demo_journal_backfill import (
    VenuePages,
    build_plan,
    read_venue,
    stored_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", type=uuid.UUID, required=True)
    parser.add_argument("--user", type=uuid.UUID, required=True)
    parser.add_argument("--since", default="2026-10-01", help="YYYY-MM-DD, UTC")
    parser.add_argument("--capture", type=Path, help="Write the raw venue pages read here.")
    parser.add_argument("--replay", type=Path, help="Plan from a capture; no venue read.")
    parser.add_argument("--out", type=Path, help="Write the plan here instead of stdout.")
    args = parser.parse_args()

    settings = get_settings()
    if settings.enable_real_trading or settings.execution_mode is not ExecutionMode.PAPER:
        print("Refused: the BloFin demo backfill stays paper-only.", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    if args.replay:
        pages = VenuePages.replay(json.loads(args.replay.read_text()))
    else:
        from app.core.blofin_readonly_access import get_readonly_client
        from app.services.blofin_demo_manual_mirror import BloFinDemoManualMirror

        client = get_readonly_client(settings, activity_history=True, paper_internal_snapshot=True)
        # Same #304 org pin and UID check the live mirror runs before any account read.
        BloFinDemoManualMirror(client, now=now)._require_internal_read_pin(
            organization_id=args.org, settings=settings
        )
        since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
        pages = read_venue(client, since=since, now=now)
        if args.capture:
            args.capture.write_text(json.dumps(pages.capture(), indent=1, sort_keys=True))

    with get_session_factory()() as session:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
        rows, annotated = stored_rows(session, organization_id=args.org, user_id=args.user)
        lines, summary = build_plan(pages, rows, annotated=annotated, user_id=args.user)
        session.rollback()

    output = "\n".join(json.dumps(line, sort_keys=True) for line in [*lines, {"summary": summary}])
    if args.out:
        args.out.write_text(output + "\n")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
