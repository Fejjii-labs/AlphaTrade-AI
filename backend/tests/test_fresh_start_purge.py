"""Classification and guard logic of scripts/fresh_start_purge.py (no database)."""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fresh_start_purge.py"
_spec = importlib.util.spec_from_file_location("fresh_start_purge", SCRIPT)
assert _spec and _spec.loader
purge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(purge)

STAGING = f"postgresql://u:pw@{purge.STAGING_HOST}.oregon-postgres.render.com/db"


def _metadata():  # type: ignore[no-untyped-def]
    import app.db as db_pkg

    for mod in pkgutil.iter_modules(db_pkg.__path__):
        if mod.name not in {"migrations", "session"}:
            importlib.import_module(f"app.db.{mod.name}")
    from app.db.base import Base

    return Base.metadata


def _orm_edges() -> list[tuple[str, str]]:
    return [
        (t.name, fk.column.table.name) for t in _metadata().tables.values() for fk in t.foreign_keys
    ]


def test_every_orm_table_is_classified_exactly_once() -> None:
    orm = set(_metadata().tables)
    assert not set(purge.PURGE_TABLES) & set(purge.KEEP_TABLES)
    assert orm - set(purge.PURGE_TABLES) - set(purge.KEEP_TABLES) == set()
    assert (set(purge.PURGE_TABLES) | set(purge.KEEP_TABLES)) - orm == {"alembic_version"}


def test_orm_classification_passes_guards() -> None:
    live = [*_metadata().tables, "alembic_version"]
    assert purge.classification_errors(live, _orm_edges()) == []


def test_purge_order_is_children_first() -> None:
    order = {name: i for i, name in enumerate(purge.PURGE_TABLES)}
    for child, parent in _orm_edges():
        if child in order and parent in order and child != parent:
            if {child, parent} == {"trade_proposals", "trade_plan_revisions"}:
                continue  # FK cycle; one TRUNCATE statement handles it
            assert order[child] < order[parent], (child, parent)


@pytest.mark.parametrize(
    "keep",
    [
        "users",
        "organizations",
        "user_strategies",
        "user_strategy_versions",
        "setup_definitions",
        "bot_risk_settings",
        "autonomous_trading_settings",
        "exchange_accounts",
        "kill_switch_states",
        "alembic_version",
        "risk_policy_versions",
        "telegram_security_bindings",
    ],
)
def test_owner_keep_list_is_kept(keep: str) -> None:
    assert keep in purge.KEEP_TABLES and keep not in purge.PURGE_TABLES


@pytest.mark.parametrize(
    "gone",
    [
        "canonical_candidates",
        "setup_detections",
        "market_watcher_scan_records",
        "watcher_scan_attempts",
        "exchange_orders",
        "exchange_fills",
        "orders",
        "positions",
        "journal_trades",
        "journals",
        "performance_snapshots",
        "blofin_demo_sync_snapshots",
        "telegram_paper_notifications",
        "paper_trades",
    ],
)
def test_owner_purge_list_is_purged(gone: str) -> None:
    assert gone in purge.PURGE_TABLES


def test_guard_rejects_unknown_table_and_keep_to_purge_fk() -> None:
    live = [*purge.PURGE_TABLES, *purge.KEEP_TABLES, "mystery_table"]
    errors = purge.classification_errors(live, [("users", "orders")])
    assert any("mystery_table" in e for e in errors)
    assert any("users references PURGE table orders" in e for e in errors)


def test_guard_rejects_missing_purge_table() -> None:
    live = [t for t in [*purge.PURGE_TABLES, *purge.KEEP_TABLES] if t != "orders"]
    assert any("orders" in e for e in purge.classification_errors(live, []))


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "not a url ::",
        "sqlite:///x.db",
        "postgresql://u:pw@dpg-other-a.oregon-postgres.render.com/db",
        f"postgresql://u:pw@evil-{purge.STAGING_HOST}.example.com/db",
        f"postgresql://u:pw@{purge.STAGING_HOST}x.render.com/db",
        "postgresql://u:pw@localhost/db",
    ],
)
def test_host_guard_refuses_and_hides_url(url: str | None) -> None:
    with pytest.raises(purge.PurgeRefusedError) as exc:
        purge.check_staging_url(url)
    assert "pw" not in str(exc.value) and "render.com" not in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        STAGING,
        f"postgres://u:pw@{purge.STAGING_HOST}/db",
        f"postgresql+psycopg://u:pw@{purge.STAGING_HOST}/db",
    ],
)
def test_host_guard_accepts_staging(url: str) -> None:
    assert purge.check_staging_url(url).startswith("postgresql+psycopg://")


def test_truncate_sql_only_purge_tables_no_cascade() -> None:
    sql = purge.truncate_sql(list(purge.PURGE_TABLES))
    assert sql.startswith("TRUNCATE TABLE ") and sql.endswith(" RESTRICT")
    assert "CASCADE" not in sql
    for bad in (["users"], ["orders", "users"], ['orders"; DROP TABLE users; --']):
        with pytest.raises(purge.PurgeRefusedError):
            purge.truncate_sql(bad)


def test_lock_sql_only_keep_tables() -> None:
    assert purge.lock_keep_sql(["users"]) == 'LOCK TABLE "users" IN SHARE MODE'
    with pytest.raises(purge.PurgeRefusedError):
        purge.lock_keep_sql(["orders"])


def test_count_errors_detect_keep_change_and_leftover_rows() -> None:
    before = {"users": 3, "orders": 5}
    assert purge.count_errors(before, {"users": 3, "orders": 0}, ["users"]) == []
    errors = purge.count_errors(before, {"users": 2, "orders": 1}, ["users"])
    assert any("users changed" in e for e in errors)
    assert any("orders not empty" in e for e in errors)


def test_execute_requires_exact_confirm(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    called = []
    monkeypatch.setattr(purge, "run", lambda **kw: called.append(kw) or 0)
    assert purge.main(["--execute"]) == 2
    assert purge.main(["--execute", "--confirm", "fresh-start-2026-11-22"]) == 2
    assert called == []
    assert purge.main([]) == 0 and called == [{"execute": False}]


def test_missing_env_refuses_without_connecting(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv(purge.URL_ENV, raising=False)
    assert purge.main([]) == 2
    assert "REFUSED" in capsys.readouterr().out
