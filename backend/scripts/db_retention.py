"""Report table sizes and run the retention policies by hand (H5, data policy).

Dry-run by default: prints what each policy would delete (and how many
candidates it would summarize) plus the largest tables. ``--apply`` deletes
in bounded batches, writing candidate summaries first. ``--vacuum`` runs
``VACUUM (ANALYZE)`` on each table that lost rows. ``--reclaim`` runs
``VACUUM (FULL, ANALYZE)`` instead, returning disk space to the OS: it takes an
ACCESS EXCLUSIVE lock per table, so use it once, for the first big cleanup, in
a quiet window. Without ``--apply`` it reclaims every retention table.

Uses the operator's DATABASE_URL; it prints no connection details. Never run
``--apply`` or ``--reclaim`` against a shared database without the owner's
explicit OK (AGENTS.md approval gates).

    uv run python scripts/db_retention.py                       # dry-run + sizes
    uv run python scripts/db_retention.py --apply --vacuum      # delete, then VACUUM (ANALYZE)
    uv run python scripts/db_retention.py --apply --reclaim     # first big cleanup
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


def _print_sizes(factory, top: int) -> None:  # type: ignore[no-untyped-def]
    with factory() as session:
        if session.get_bind().dialect.name != "postgresql":
            return
        total = session.execute(text("SELECT pg_database_size(current_database())"))
        print(f"database_size_bytes={int(total.scalar_one())}")
        for name, rows, size in session.execute(text(_SIZE_SQL), {"limit": top}):
            print(f"table={name} live_rows={rows} total_bytes={size}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="delete (default: dry-run)")
    parser.add_argument("--vacuum", action="store_true", help="VACUUM (ANALYZE) pruned tables")
    parser.add_argument(
        "--reclaim", action="store_true", help="VACUUM FULL pruned tables (one-off, locks)"
    )
    parser.add_argument("--top", type=int, default=15, help="largest tables to list")
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.db.session import get_session_factory
    from app.services.db_retention import (
        pruned_tables,
        run_retention,
        tables_with_deletes,
        vacuum_tables,
    )

    settings = get_settings()
    factory = get_session_factory()
    _print_sizes(factory, args.top)
    results = run_retention(factory, settings, now=datetime.now(UTC), dry_run=not args.apply)
    for result in results:
        print(
            f"policy={result.policy} table={result.table} cutoff={result.cutoff.isoformat()} "
            f"matched={result.matched} deleted={result.deleted} "
            f"summarized={result.summarized} dry_run={result.dry_run} "
            f"complete={result.complete}"
        )
    if args.reclaim or args.vacuum:
        tables = (
            tables_with_deletes(results) if args.apply else tuple(sorted(pruned_tables(settings)))
        )
        for table, outcome in vacuum_tables(factory, settings, tables, full=args.reclaim).items():
            print(f"vacuum table={table} full={args.reclaim} result={outcome}")
        _print_sizes(factory, args.top)
    elif args.apply:
        print("Space returns to Postgres for reuse; add --vacuum (or --reclaim once) to compact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
