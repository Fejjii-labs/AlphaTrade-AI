"""Report table sizes and run the runtime-log retention policies by hand (H5, L11).

Dry-run by default: prints what each policy would delete and the size of the
largest tables. ``--apply`` deletes in bounded batches. Uses the operator's
DATABASE_URL; it prints no connection details. Never run ``--apply`` against
a shared database without the owner's explicit OK (AGENTS.md approval gates).

    uv run python scripts/db_retention.py            # dry-run + sizes
    uv run python scripts/db_retention.py --apply    # delete
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from sqlalchemy import text

_SIZE_SQL = """
SELECT relname, n_live_tup, pg_total_relation_size(relid)
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT :limit
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="delete (default: dry-run)")
    parser.add_argument("--top", type=int, default=15, help="largest tables to list")
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.db.session import get_session_factory
    from app.services.db_retention import run_retention

    settings = get_settings()
    factory = get_session_factory()
    with factory() as session:
        if session.get_bind().dialect.name == "postgresql":
            total = session.execute(text("SELECT pg_database_size(current_database())"))
            print(f"database_size_bytes={int(total.scalar_one())}")
            for name, rows, size in session.execute(text(_SIZE_SQL), {"limit": args.top}):
                print(f"table={name} live_rows={rows} total_bytes={size}")
    results = run_retention(factory, settings, now=datetime.now(UTC), dry_run=not args.apply)
    for result in results:
        print(
            f"policy={result.policy} table={result.table} cutoff={result.cutoff.isoformat()} "
            f"matched={result.matched} deleted={result.deleted} "
            f"dry_run={result.dry_run} complete={result.complete}"
        )
    if args.apply:
        print("Space returns to Postgres for reuse; run VACUUM (ANALYZE) to update stats.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
