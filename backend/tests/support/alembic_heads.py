"""Shared Alembic head lookup for migration tests.

Tests must not pin the current head revision id: every new migration would
turn them red without any behavioural change. Assert history facts (a given
revision's ``down_revision``, ancestry) explicitly, and resolve "the head"
from the script directory here, which also enforces a single head.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = BACKEND_ROOT / "src/app/db/migrations"


def alembic_script_directory() -> ScriptDirectory:
    """Script directory for the backend migrations, independent of the cwd."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(config)


def single_alembic_head(script: ScriptDirectory | None = None) -> str:
    """Return the only Alembic head; fail if the history has zero or several heads."""
    heads = (script or alembic_script_directory()).get_heads()
    assert len(heads) == 1, f"expected exactly one Alembic head, found {sorted(heads)}"
    return heads[0]


def alembic_ancestry(head: str | None = None) -> set[str]:
    """All revision ids reachable from ``head`` (default: the single head)."""
    script = alembic_script_directory()
    target = head or single_alembic_head(script)
    return {revision.revision for revision in script.walk_revisions(base="base", head=target)}
