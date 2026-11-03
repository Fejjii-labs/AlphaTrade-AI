"""Separate BloFin credential storage from loading and execution authority.

Environment variables may keep BloFin secrets while AlphaTrade runs paper
mode on public Binance USD-M evidence. Presence in storage is not access
and is not permission to build an authenticated client.

The only loader that returns secret values is
:func:`load_blofin_execution_credentials`. It opens only when the complete
demo execution gate is satisfied. Paper isolation
(``execution_mode=paper``, real trading off, ``exchange_mode=paper_internal``,
``blofin_demo_enabled=false``) keeps that gate closed. Live USD-M evidence
also keeps it closed unless the explicit staging API demo capability is on.
That capability never activates the paper Watcher. Real trading cannot open it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.config import ExchangeMode, ExecutionMode, Settings
from app.core.exchange_safety import is_allowlisted_demo_host

_LIVE_USD_M_SOURCES = frozenset(
    {"binance_usdm", "binance-usdm", "usdm", "bybit_usdt_perpetual", "bybit-usdt-perpetual"}
)


class CredentialAccessDeniedError(ValueError):
    """Stored BloFin secrets were requested without execution authority.

    The message never includes credential values.
    """


@dataclass(frozen=True, slots=True)
class BloFinExecutionCredentials:
    """Credential values released only after the execution gate opens."""

    api_key: str
    api_secret: str
    api_passphrase: str

    def __repr__(self) -> str:
        return "BloFinExecutionCredentials(redacted)"

    def __str__(self) -> str:
        return "BloFinExecutionCredentials(redacted)"


def paper_credential_isolation_active(settings: Settings) -> bool:
    """Hard guarantee that no BloFin execution client may become active.

    Stored secrets may exist. This profile forbids loading them.
    """
    return (
        settings.execution_mode is ExecutionMode.PAPER
        and not settings.enable_real_trading
        and not settings.real_trading_enabled
        and settings.exchange_mode is ExchangeMode.PAPER_INTERNAL
        and not settings.blofin_demo_enabled
    )


def stored_blofin_credentials_present(settings: Settings) -> bool:
    """Return whether all three BloFin secrets are stored. Values stay sealed."""
    return bool(
        settings.blofin_api_key.strip()
        and settings.blofin_api_secret.strip()
        and settings.blofin_api_passphrase.strip()
    )


def _live_usd_m_evidence(settings: Settings) -> bool:
    return settings.perpetual_evidence_source.strip().lower() in _LIVE_USD_M_SOURCES


def live_evidence_demo_access_requested(settings: Settings) -> bool:
    """Explicit staging API opt-in; does not authorize Watcher venue dispatch."""
    return (
        settings.blofin_live_evidence_demo_enabled
        and settings.paper_runtime_environment
        and not settings.watcher_orchestration_enabled
        and not settings.watcher_paper_staging_activation
        and not settings.worker_enabled
        and not settings.enable_paper_scheduler
        and not settings.paper_signal_orchestration_enabled
    )


def governed_demo_worker_access_requested(settings: Settings) -> bool:
    """Explicit staging worker arm, independently pinned from the API capability."""
    if not (
        settings.governed_blofin_demo_enabled
        and settings.paper_runtime_environment
        and settings.watcher_orchestration_enabled
        and settings.watcher_paper_staging_activation
    ):
        return False
    try:
        for value in (
            settings.governed_blofin_demo_organization_id,
            settings.governed_blofin_demo_user_id,
            settings.governed_blofin_demo_account_id,
        ):
            UUID(value)
    except ValueError:
        return False
    return True


def manual_demo_access_requested(settings: Settings) -> bool:
    """Separately armed, owner-pinned staging API capability; no strategy authority."""
    if not (settings.manual_blofin_demo_enabled and settings.paper_runtime_environment):
        return False
    try:
        for value in (
            settings.governed_blofin_demo_organization_id,
            settings.governed_blofin_demo_user_id,
            settings.governed_blofin_demo_account_id,
        ):
            UUID(value)
    except ValueError:
        return False
    return True


def blofin_execution_authorized(settings: Settings) -> bool:
    """Return whether an authenticated BloFin client may be constructed.

    Credential presence alone is never enough. The complete gate requires
    paper execution, real trading disabled, ``paper_exchange_demo``,
    ``blofin_demo_enabled``, stored credentials, and an allowlisted demo
    host. Live evidence additionally requires the explicit staging API capability.
    """
    if paper_credential_isolation_active(settings):
        return False
    if _live_usd_m_evidence(settings) and not (
        live_evidence_demo_access_requested(settings)
        or governed_demo_worker_access_requested(settings)
        or manual_demo_access_requested(settings)
    ):
        return False
    if settings.execution_mode is not ExecutionMode.PAPER:
        return False
    if settings.enable_real_trading or settings.real_trading_enabled:
        return False
    if settings.exchange_mode is not ExchangeMode.PAPER_EXCHANGE_DEMO:
        return False
    if not settings.blofin_demo_enabled:
        return False
    if not stored_blofin_credentials_present(settings):
        return False
    rest_url = settings.blofin_demo_rest_base_url.strip()
    if not is_allowlisted_demo_host(rest_url):
        return False
    ws_url = settings.blofin_demo_ws_url.strip()
    return not ws_url or is_allowlisted_demo_host(ws_url)


def load_blofin_execution_credentials(settings: Settings) -> BloFinExecutionCredentials:
    """Load BloFin secrets only when :func:`blofin_execution_authorized` is true.

    Raises:
        CredentialAccessDeniedError: the execution gate is closed. The error
            text does not contain secret values.
    """
    if not blofin_execution_authorized(settings):
        raise CredentialAccessDeniedError(
            "BloFin execution credentials are sealed. Stored secrets are not "
            "authorized until the complete demo execution gate is open."
        )
    return BloFinExecutionCredentials(
        api_key=settings.blofin_api_key.strip(),
        api_secret=settings.blofin_api_secret.strip(),
        api_passphrase=settings.blofin_api_passphrase.strip(),
    )
