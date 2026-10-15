"""One migrated PostgreSQL schema per process, cloned for each fixture.

``CREATE DATABASE ... TEMPLATE`` copies tables, functions, and triggers without
replaying DDL. URL-stable refresh replaces the database named by a fixture URL
(so ``Settings.database_url`` and the fixture stay on the same name). Side clones
are separate databases for fixtures that previously created a private schema.

Alembic tests keep using their own empty schema on the fixture URL. They do not
call these helpers. xdist workers get distinct template and database names.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.pool import NullPool

FULL_SCHEMA_MARKER = "alphatrade-fixture-template-full"
PERSISTENCE_SCHEMA_MARKER = "alphatrade-fixture-template-persistence"

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_WORKER = re.compile(r"^(master|gw\d+)$")
_PROTECTED = frozenset({"postgres", "template0", "template1"})
_LOCK = threading.Lock()
_PREPARERS: dict[str, Callable[[Engine], None]] = {}
_READY: set[tuple[tuple[object, ...], str]] = set()
_ADMINS: dict[tuple[object, ...], Engine] = {}
_TemplatePreparer = Callable[[Engine], None]


@dataclass(frozen=True)
class _SideClone:
    name: str
    server: URL
    retain: bool


_CLONES: dict[str, _SideClone] = {}


def quote_ident(name: str) -> str:
    """Quote a database identifier this module generated or validated."""
    if not _IDENT.fullmatch(name):
        raise ValueError(f"unsafe PostgreSQL identifier: {name!r}")
    return f'"{name}"'


def current_worker() -> str:
    """xdist worker id, or ``master`` when the suite runs in one process."""
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    if not _WORKER.fullmatch(worker):
        raise ValueError(f"unsupported pytest-xdist worker id: {worker!r}")
    return worker


def worker_database_name(database: str, worker: str) -> str:
    """Suffix a fixture database name so xdist workers do not share it."""
    if not _IDENT.fullmatch(database):
        raise ValueError(f"unsafe PostgreSQL database name: {database!r}")
    if not _WORKER.fullmatch(worker):
        raise ValueError(f"unsupported pytest-xdist worker id: {worker!r}")
    if worker == "master" or database.endswith("_" + worker):
        return database
    renamed = f"{database}_{worker}"
    if len(renamed) > 63:
        raise ValueError("worker database name exceeds 63 characters")
    return renamed


def register_template(kind: str, prepare: _TemplatePreparer) -> None:
    """Register the one-time schema build for ``kind`` (``full`` or ``persistence``)."""
    if not _IDENT.fullmatch(kind):
        raise ValueError(f"unsafe template kind: {kind!r}")
    _PREPARERS[kind] = prepare


def assign_worker_database(worker: str) -> None:
    """Point fixture URLs at a private database before tests capture them.

    Called from ``pytest_configure`` only when ``PYTEST_XDIST_WORKER`` is set.
    The renamed database is created empty so connectivity checks succeed; the
    first fixture refresh replaces it from the worker's template.
    """
    if worker == "master":
        return
    renamed: set[str] = set()
    for key, value in list(os.environ.items()):
        if "POSTGRES" not in key and not key.endswith("DATABASE_URL"):
            continue
        if not value.startswith("postgresql"):
            continue
        updated = _retarget_url(value, worker)
        os.environ[key] = updated
        renamed.add(updated)
    for url in renamed:
        _ensure_database_exists(url)
    module = sys.modules.get("tests.support.postgres_persistence")
    phase1 = os.environ.get("PHASE1_POSTGRES_URL")
    if module is not None and phase1:
        module.POSTGRES_URL = phase1


def refresh_database(url: str, *, kind: str) -> Engine:
    """Replace ``url``'s database with a template clone and return an engine to it.

    The database name stays the same, matching ``DROP SCHEMA public`` + ``create_all``
    isolation: committed rows from the previous fixture call are gone, and any
    settings that still point at this URL see the fresh schema.
    """
    target = make_url(url)
    name = target.database
    if name is None or name in _PROTECTED or name.startswith(("template", "at_tpl_")):
        raise RuntimeError(f"refusing to replace PostgreSQL database {name!r}")
    quote_ident(name)
    template = _ensure_template(target, kind)
    admin = _admin_engine(target)
    with _LOCK:
        _terminate_and_drop(admin, name)
        _create_from_template(admin, name, template)
    return _engine(target)


def clone_database(url: str, *, kind: str = "full", retain: bool = False) -> Engine:
    """Create a new database from the template. Caller must ``drop_clone`` it.

    ``retain`` keeps a module-scoped clone alive across per-test cleanup.
    """
    target = make_url(url)
    template = _ensure_template(target, kind)
    name = f"atclone_test_{current_worker()}_{uuid4().hex[:12]}"
    quote_ident(name)
    admin = _admin_engine(target)
    with _LOCK:
        _create_from_template(admin, name, template)
    server = target.set(database="postgres", query={})
    _CLONES[name] = _SideClone(name=name, server=server, retain=retain)
    return _engine(target.set(database=name))


def drop_clone(engine: Engine) -> None:
    """Drop a side clone created by :func:`clone_database` and dispose ``engine``."""
    name = engine.url.database
    engine.dispose()
    if name is None or name not in _CLONES:
        return
    _drop_named(name)


def release_ephemeral_clones() -> None:
    """Drop side clones created for the test that just finished."""
    for name in [name for name, clone in _CLONES.items() if not clone.retain]:
        _drop_named(name)


def prebuild_fixture_templates() -> None:
    """Build full and persistence templates once, before the first test pays for it."""
    server = _default_server_url()
    _ensure_template(server, "full")
    _ensure_template(server, "persistence")


def cleanup_owned_databases() -> None:
    """Drop this process's side clones and templates. Never drops the fixture URL."""
    release_ephemeral_clones()
    for name in [name for name, clone in _CLONES.items() if clone.retain]:
        _drop_named(name)
    try:
        admin = _admin_engine(_default_server_url())
    except Exception:
        _READY.clear()
        return
    prefix = _like_literal(f"atclone_test_{current_worker()}_")
    template_prefix = _like_literal(f"at_tpl_{current_worker()}_")
    try:
        with admin.connect() as conn:
            names = list(
                conn.scalars(
                    text(
                        "SELECT datname FROM pg_database "
                        "WHERE datname LIKE :side ESCAPE '!' "
                        "OR datname LIKE :templates ESCAPE '!'"
                    ),
                    {"side": prefix, "templates": template_prefix},
                )
            )
        for name in names:
            if isinstance(name, str) and _IDENT.fullmatch(name):
                _allow_and_drop(admin, name)
    except Exception:
        return
    finally:
        _READY.clear()


@contextmanager
def cloned_postgres_engine(url: str, *, kind: str = "full") -> Iterator[Engine]:
    """Yield a side-clone engine and drop the database on exit."""
    engine = clone_database(url, kind=kind)
    try:
        yield engine
    finally:
        drop_clone(engine)


def _retarget_url(url: str, worker: str) -> str:
    parsed = make_url(url)
    if parsed.database is None:
        return url
    renamed = worker_database_name(parsed.database, worker)
    return parsed.set(database=renamed).render_as_string(hide_password=False)


def _ensure_database_exists(url: str) -> None:
    parsed = make_url(url)
    name = parsed.database
    if name is None:
        return
    quote_ident(name)
    admin = _admin_engine(parsed)
    with admin.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": name},
        )
        if exists is None:
            conn.execute(text(f"CREATE DATABASE {quote_ident(name)}"))


def _load_preparers() -> None:
    if _PREPARERS:
        return
    import tests.support.postgres_persistence  # noqa: F401  — registers template builders


def _server_key(url: URL) -> tuple[object, ...]:
    return (url.host, url.port, url.username)


def _default_server_url() -> URL:
    raw = os.environ.get(
        "PHASE1_POSTGRES_URL",
        os.environ.get(
            "AT028_POSTGRES_URL",
            "postgresql+psycopg://alphatrade:alphatrade@127.0.0.1:5432/alphatrade_test",
        ),
    )
    return make_url(raw)


def _admin_engine(url: URL) -> Engine:
    key = _server_key(url)
    cached = _ADMINS.get(key)
    if cached is not None:
        return cached
    admin_url = url.set(database="postgres", query={})
    engine = create_engine(
        admin_url,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
        future=True,
        connect_args={"connect_timeout": 2},
    )
    _ADMINS[key] = engine
    return engine


def _engine(url: URL) -> Engine:
    return create_engine(url, poolclass=NullPool, future=True)


def _template_name(kind: str) -> str:
    return f"at_tpl_{current_worker()}_{kind}"


def _ensure_template(url: URL, kind: str) -> str:
    _load_preparers()
    if kind not in _PREPARERS:
        raise RuntimeError(f"unknown PostgreSQL fixture template: {kind}")
    key = (_server_key(url), kind)
    name = _template_name(kind)
    if key in _READY:
        return name
    with _LOCK:
        if key in _READY:
            return name
        _build_template(url, kind, name)
        _READY.add(key)
    return name


def _build_template(url: URL, kind: str, name: str) -> None:
    admin = _admin_engine(url)
    _terminate_and_drop(admin, name)
    _create_database(admin, name)
    engine = _engine(url.set(database=name, query={}))
    try:
        _PREPARERS[kind](engine)
    except Exception:
        engine.dispose()
        _terminate_and_drop(admin, name)
        raise
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": name},
        )
        try:
            conn.execute(
                text("UPDATE pg_database SET datallowconn = false WHERE datname = :name"),
                {"name": name},
            )
        except Exception:
            # Non-superuser fixture roles can still clone a database they own.
            return


def _terminate_and_drop(admin: Engine, name: str) -> None:
    quote_ident(name)
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": name},
        )
        conn.execute(text(f"DROP DATABASE IF EXISTS {quote_ident(name)} WITH (FORCE)"))


def _create_database(admin: Engine, name: str) -> None:
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {quote_ident(name)}"))


def _create_from_template(admin: Engine, name: str, template: str) -> None:
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": template},
        )
        conn.execute(text(f"CREATE DATABASE {quote_ident(name)} TEMPLATE {quote_ident(template)}"))


def _allow_and_drop(admin: Engine, name: str) -> None:
    with admin.connect() as conn:
        conn.execute(
            text("UPDATE pg_database SET datallowconn = true WHERE datname = :name"),
            {"name": name},
        )
    _terminate_and_drop(admin, name)


def _drop_named(name: str) -> None:
    clone = _CLONES.pop(name, None)
    if clone is None:
        return
    try:
        admin = _admin_engine(clone.server)
        with _LOCK:
            _terminate_and_drop(admin, name)
    except Exception:
        _CLONES.setdefault(name, clone)
        raise


def _like_literal(prefix: str) -> str:
    escaped = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return escaped + "%"
