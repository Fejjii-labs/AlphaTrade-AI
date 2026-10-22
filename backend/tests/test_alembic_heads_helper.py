"""The shared Alembic head helper resolves one head and refuses ambiguity."""

from __future__ import annotations

import pytest
from alembic.script import ScriptDirectory

from tests.support.alembic_heads import (
    alembic_ancestry,
    alembic_script_directory,
    single_alembic_head,
)


def test_alembic_has_exactly_one_head() -> None:
    script = alembic_script_directory()
    heads = script.get_heads()
    assert len(heads) == 1
    assert heads == [single_alembic_head(script)]


def test_single_head_matches_the_script_directory() -> None:
    script = alembic_script_directory()
    assert script.get_heads() == [single_alembic_head(script)]


def test_ancestry_reaches_base_from_the_single_head() -> None:
    ancestry = alembic_ancestry()
    assert single_alembic_head() in ancestry
    assert "a4knowledge001" in ancestry


@pytest.mark.parametrize("heads", [[], ["rev_a", "rev_b"]])
def test_zero_or_several_heads_fail_loudly(
    monkeypatch: pytest.MonkeyPatch, heads: list[str]
) -> None:
    monkeypatch.setattr(ScriptDirectory, "get_heads", lambda _self: heads)
    with pytest.raises(AssertionError, match="expected exactly one Alembic head"):
        single_alembic_head()
