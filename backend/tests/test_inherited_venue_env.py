"""Inherited BloFin credentials and staging DSNs never reach a test."""

import os

import pytest

from tests.support.inherited_venue_env import (
    BLOFIN_READONLY_CREDENTIAL_ENV,
    STAGING_DSN_ENV,
    clear_inherited_venue_env,
)

_STAGING_DSN = "postgresql+psycopg://staging.example.invalid/alphatrade"


def test_clear_drops_readonly_credentials_and_staging_dsns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in BLOFIN_READONLY_CREDENTIAL_ENV:
        monkeypatch.setenv(name, "inherited-secret")
    for name in STAGING_DSN_ENV:
        monkeypatch.setenv(name, _STAGING_DSN)
    monkeypatch.setenv(
        "EXPERIMENT_TEST_POSTGRES_URL",
        "postgresql+psycopg://localhost/alphatrade_test",
    )
    clear_inherited_venue_env(monkeypatch)
    for name in BLOFIN_READONLY_CREDENTIAL_ENV:
        assert os.environ[name] == ""
    for name in STAGING_DSN_ENV:
        assert name not in os.environ
    assert os.environ["EXPERIMENT_TEST_POSTGRES_URL"].endswith("/alphatrade_test")


def test_autouse_fixture_clears_credentials_before_the_test() -> None:
    for name in BLOFIN_READONLY_CREDENTIAL_ENV:
        assert os.environ[name] == ""
    for name in STAGING_DSN_ENV:
        assert name not in os.environ
