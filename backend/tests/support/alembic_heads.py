"""Shared Alembic head lookup for migration tests.

Tests must not pin the current head revision id: every new migration would
turn them red without any behavioural change. Assert history facts (a given
revision's ``down_revision``, ancestry) explicitly, and resolve "the head"
from the script directory here, which also enforces a single head.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise
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


def assert_down_revision_chain(script: ScriptDirectory, newer_to_older: Sequence[str]) -> None:
    """Assert each revision's ``down_revision`` is the next id.

    The current head is not part of the chain. A later linear migration can
    sit above the newest id here without changing these links.
    """

    assert len(newer_to_older) >= 2
    for newer, older in pairwise(newer_to_older):
        revision = script.get_revision(newer)
        assert revision is not None, newer
        assert revision.down_revision == older, (newer, revision.down_revision, older)


def migrations_from_head_through(script: ScriptDirectory, anchor: str) -> list[Path]:
    """Migration files from the single head down through ``anchor``, inclusive."""

    current = single_alembic_head(script)
    paths: list[Path] = []
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        revision = script.get_revision(current)
        assert revision is not None, current
        assert revision.path
        paths.append(Path(revision.path))
        if current == anchor:
            return paths
        parent = revision.down_revision
        assert isinstance(parent, str), (current, parent)
        current = parent
    raise AssertionError(f"{anchor} is not on the linear head chain")
