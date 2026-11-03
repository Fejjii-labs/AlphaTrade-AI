"""Operator CLI: export staging trading settings, import into production.

Export (run against staging, e.g. as a Render one-off job):
    python scripts/settings_mirror.py export --org "<org name>" --email <user email> > bundle.json
Import (dry run by default; add --apply to write):
    python scripts/settings_mirror.py import --org "<org name>" --email <user email> bundle.json

Uses DATABASE_URL from the environment. The bundle contains no ids or secrets.
Risk policy is filed as a proposal; apply it in the production app.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.operations.settings_mirror import export_bundle, import_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "import"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--org", required=True)
        cmd.add_argument("--email", required=True)
    sub.choices["import"].add_argument("bundle", type=Path)
    sub.choices["import"].add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    with get_session_factory()() as session:
        if args.command == "export":
            bundle = export_bundle(
                session, settings=settings, org_name=args.org, user_email=args.email
            )
            json.dump(bundle, sys.stdout, indent=2, sort_keys=True, default=str)
            sys.stdout.write("\n")
            return 0
        bundle = json.loads(args.bundle.read_text())
        report = import_bundle(
            session,
            bundle,
            settings=settings,
            org_name=args.org,
            user_email=args.email,
            apply=args.apply,
        )
        if args.apply:
            session.commit()
        else:
            session.rollback()
        json.dump(report.as_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
