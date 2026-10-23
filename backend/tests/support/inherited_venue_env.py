"""Drop inherited venue credentials and staging database URLs during tests."""

from __future__ import annotations

import pytest

# Dedicated BloFin read-only credentials. Empty strings override a dotenv file
# so a staging shell cannot authenticate a test against the venue.
BLOFIN_READONLY_CREDENTIAL_ENV = (
    "BLOFIN_READONLY_API_KEY",
    "BLOFIN_READONLY_API_SECRET",
    "BLOFIN_READONLY_API_PASSPHRASE",
)

# Application and Alembic DSNs. Disposable ``*_POSTGRES_URL`` fixtures stay.
STAGING_DSN_ENV = (
    "DATABASE_URL",
    "ALEMBIC_DATABASE_URL",
    "STAGING_DATABASE_URL",
    "STAGING_DSN",
)


def clear_inherited_venue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove inherited BloFin read-only credentials and staging DSNs.

    Tests that need a value set it themselves after this runs. Disposable
    Postgres URLs used by the CI service are not touched.
    """
    for name in BLOFIN_READONLY_CREDENTIAL_ENV:
        monkeypatch.setenv(name, "")
    for name in STAGING_DSN_ENV:
        monkeypatch.delenv(name, raising=False)
