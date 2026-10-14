"""Paper, mock, and credential guards for the hang-isolation backend run.

Loaded only by ``scripts/run_backend_release.py --diagnostic``. Outbound sockets
are disabled on that command (localhost and 127.0.0.1 stay open for Postgres).
That matters for modules that sit next to external clients, especially
``test_mvp_readiness_smoke.py`` and ``test_watcher_five_symbol_watchlist.py``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import pytest

_BROKER_PREFIXES = ("BLOFIN_", "OPENAI_", "BROKER_")
_CREDENTIAL_PARTS = frozenset({"KEY", "SECRET", "TOKEN", "PASSWORD", "PASSPHRASE"})
_SETTINGS_CREDENTIALS = (
    "openai_api_key",
    "blofin_api_key",
    "blofin_api_secret",
    "blofin_api_passphrase",
    "blofin_readonly_api_key",
    "blofin_readonly_api_secret",
    "blofin_readonly_api_passphrase",
)


def forbidden_credential_names(environ: Mapping[str, str]) -> list[str]:
    """Return env names that carry a broker or model credential. Values stay hidden."""
    found: list[str] = []
    for name, value in environ.items():
        if not value or not str(value).strip():
            continue
        upper = name.upper()
        if not upper.startswith(_BROKER_PREFIXES):
            continue
        if set(upper.split("_")) & _CREDENTIAL_PARTS:
            found.append(name)
    return sorted(found)


def assert_hang_isolation_safety() -> None:
    """Fail the session unless this process is paper, mock, and free of broker keys."""
    forbidden = forbidden_credential_names(os.environ)
    if forbidden:
        raise AssertionError(
            "hang isolation refused broker credentials in the environment: " + ", ".join(forbidden)
        )
    from app.core.config import Settings

    try:
        settings = Settings()
    except Exception as exc:
        raise AssertionError(
            "hang isolation could not load Settings for the paper/mock guard: " + type(exc).__name__
        ) from None
    problems: list[str] = []
    mode = settings.execution_mode.value
    if mode != "paper":
        problems.append(f"execution_mode={mode}")
    if settings.enable_real_trading is not False:
        problems.append("enable_real_trading is not false")
    if settings.provider_mode != "mock":
        problems.append(f"provider_mode={settings.provider_mode}")
    if settings.market_data_provider != "mock":
        problems.append(f"market_data_provider={settings.market_data_provider}")
    for name in _SETTINGS_CREDENTIALS:
        if str(getattr(settings, name) or "").strip():
            problems.append(f"{name} is set")
    if problems:
        raise AssertionError(
            "hang isolation requires paper mode, enable_real_trading false, "
            "provider_mode mock, and market_data_provider mock; " + "; ".join(problems)
        )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Stop the diagnostic session once, before a test can open a socket.

    The argument name is part of pytest's hook spec and cannot be renamed.
    """
    assert session.config is not None
    assert_hang_isolation_safety()


@pytest.fixture(scope="session", autouse=True)
def _hang_isolation_paper_and_mock_mode() -> None:
    """Session guard so a diagnostic run cannot trade or call a live provider."""
    assert_hang_isolation_safety()
