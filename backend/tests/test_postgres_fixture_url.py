"""The Postgres fixture URL has no localhost default and is required in CI."""

from __future__ import annotations

import pytest

from tests.support.postgres_template import fixture_postgres_url

_VARS = ("PHASE1_POSTGRES_URL", "AT028_POSTGRES_URL", "CI")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_unset_outside_ci_means_no_fixture_database(clean_env: pytest.MonkeyPatch) -> None:
    assert fixture_postgres_url() is None


@pytest.mark.parametrize("value", ["true", "1", "TRUE"])
def test_unset_in_ci_fails_instead_of_skipping(clean_env: pytest.MonkeyPatch, value: str) -> None:
    clean_env.setenv("CI", value)
    with pytest.raises(RuntimeError, match="refusing to skip Postgres tests silently"):
        fixture_postgres_url()


def test_phase1_url_wins_over_at028(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CI", "true")
    clean_env.setenv("AT028_POSTGRES_URL", "postgresql+psycopg://a@/b")
    assert fixture_postgres_url() == "postgresql+psycopg://a@/b"
    clean_env.setenv("PHASE1_POSTGRES_URL", "postgresql+psycopg://p@/q")
    assert fixture_postgres_url() == "postgresql+psycopg://p@/q"
