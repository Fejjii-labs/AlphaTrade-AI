"""BloFin demo trades in the journal: dry-run plan (A6 §7, A7a) and gated apply (A7b).

Usage:
  cd backend
  uv run python scripts/blofin_demo_journal_backfill.py --org <uuid> --user <uuid>
  uv run python scripts/blofin_demo_journal_backfill.py --org <uuid> --user <uuid> \\
      --since 2026-10-01 --capture venue.json --out plan.jsonl
  uv run python scripts/blofin_demo_journal_backfill.py --org <uuid> --user <uuid> \\
      --replay venue.json
  uv run python scripts/blofin_demo_journal_backfill.py --org <uuid> --user <uuid> \\
      --replay venue.json --apply --plan-digest sha256:<approved digest>

Without ``--apply`` it is read-only: the journal is read inside a READ ONLY
transaction that is rolled back. Venue access is GET-only (a wrapper refuses
any other method) after the paper guard and the #304 read pin.

``--apply`` (A7b) needs ``--plan-digest`` from a dry-run Sofien approved. It
re-plans in one transaction, refuses any digest mismatch, inserts only the
planned closed-lifetime creates, never deletes or updates existing rows,
re-plans again (must show 0 creates and 0 updates) and only then commits.
The #304 read pin runs before an apply even with ``--replay``. A rerun of the
same apply is refused (digest changes) and a fresh dry-run shows 0 creates.
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
    ApplyRefusedError,
    GetOnlyClient,
    VenuePages,
    apply_plan,
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
    parser.add_argument("--apply", action="store_true", help="A7b: insert the approved plan.")
    parser.add_argument("--plan-digest", help="sha256:... digest of the approved dry-run.")
    args = parser.parse_args()
    if args.apply and not args.plan_digest:
        parser.error("--apply requires --plan-digest from the approved dry-run")
    if args.plan_digest and not args.apply:
        parser.error("--plan-digest is only used with --apply")

    settings = get_settings()
    if settings.enable_real_trading or settings.execution_mode is not ExecutionMode.PAPER:
        print("Refused: the BloFin demo backfill stays paper-only.", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    client: GetOnlyClient | None = None
    if not args.replay or args.apply:
        from app.core.blofin_readonly_access import get_readonly_client
        from app.services.blofin_demo_manual_mirror import BloFinDemoManualMirror

        client = GetOnlyClient(
            get_readonly_client(settings, activity_history=True, paper_internal_snapshot=True)
        )
    if args.replay:
        pages = VenuePages.replay(json.loads(args.replay.read_text()))
        if args.apply:
            assert client is not None
            # The #304 org pin and UID check still gate any write.
            BloFinDemoManualMirror(client, now=now)._require_internal_read_pin(
                organization_id=args.org, settings=settings
            )
    else:
        assert client is not None
        since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
        pages = read_venue(
            client, since=since, now=now, organization_id=args.org, settings=settings
        )  # runs the #304 org pin and UID check before any account read
        if args.capture:
            args.capture.write_text(json.dumps(pages.capture(), indent=1, sort_keys=True))

    if args.apply:
        with get_session_factory()() as session:
            try:
                report = apply_plan(
                    session,
                    pages,
                    organization_id=args.org,
                    user_id=args.user,
                    approved_digest=args.plan_digest,
                )
                session.commit()
            except ApplyRefusedError as refused:
                session.rollback()
                print(json.dumps({"apply": "refused", "code": refused.code}), file=sys.stderr)
                return 3
            except BaseException:
                session.rollback()
                raise
        result = {
            "apply": "committed",
            "plan_digest": report.plan_digest,
            "created": report.created,
            "created_refs": list(report.created_refs),
            "rerun_creates": report.rerun_creates,
            "rerun_updates": report.rerun_updates,
        }
        print(json.dumps(result, sort_keys=True))
        return 0

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
