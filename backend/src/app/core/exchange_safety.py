"""Exchange-connectivity safety checks (BloFin demo).

The exchange axis (``exchange_mode``) is independent from the trading-safety
axis (``execution_mode`` / ``enable_real_trading``). This module enforces, at
settings load time, that:

* ``trade_live`` can never be selected (it exists only as an explicit tombstone).
* ``paper_exchange_demo`` may only point at an allowlisted BloFin *demo* host,
  over TLS, with demo credentials present, while real trading stays disabled.

Failing fast here makes it structurally impossible to accidentally reach a
production venue or to enable live trading via an environment mistake.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.core.config import ExchangeMode, ExecutionMode, Settings

# Only these hosts are accepted when ``exchange_mode=paper_exchange_demo``.
# BloFin demo trading is served from a dedicated demo host; production trading
# hosts are deliberately excluded so a misconfiguration cannot reach real
# markets. Confirm exact demo host(s) before enabling (P0 blocker).
BLOFIN_DEMO_HOST_ALLOWLIST = frozenset(
    {
        "demo-trading-openapi.blofin.com",
    }
)

# Defense in depth: hosts that must never be used while in demo mode.
BLOFIN_PRODUCTION_HOSTS = frozenset(
    {
        "openapi.blofin.com",
        "api.blofin.com",
        "www.blofin.com",
        "blofin.com",
    }
)

_ALLOWED_SCHEMES = frozenset({"https", "wss"})


def _host_of(url: str) -> str:
    return (urlsplit(url.strip()).hostname or "").lower()


def is_allowlisted_demo_host(url: str) -> bool:
    """Return True when ``url`` targets an allowlisted BloFin demo host over TLS."""
    parts = urlsplit(url.strip())
    if parts.scheme not in _ALLOWED_SCHEMES:
        return False
    host = (parts.hostname or "").lower()
    return host in BLOFIN_DEMO_HOST_ALLOWLIST


def assert_demo_host(url: str) -> None:
    """Raise ``ValueError`` if ``url`` is not an allowlisted BloFin demo host.

    Intended for use by the BloFin client on every request as a last-line guard,
    independent of settings validation.
    """
    if _host_of(url) in BLOFIN_PRODUCTION_HOSTS:
        raise ValueError("Refusing to call a BloFin production host in demo mode.")
    if not is_allowlisted_demo_host(url):
        raise ValueError(f"BloFin URL is not an allowlisted demo host: {_host_of(url) or '<none>'}")


def validate_exchange_mode_settings(settings: Settings) -> None:
    """Raise ``ValueError`` on unsafe or ambiguous exchange configuration."""
    mode = settings.exchange_mode

    if mode is ExchangeMode.TRADE_LIVE:
        raise ValueError(
            "exchange_mode=trade_live is permanently disabled. Real exchange "
            "execution is not implemented and must never be enabled."
        )

    # Latent live switch: production hosts are unreachable in every mode.
    for attr in ("blofin_demo_rest_base_url", "blofin_demo_ws_url"):
        url = getattr(settings, attr).strip()
        if url and _host_of(url) in BLOFIN_PRODUCTION_HOSTS:
            raise ValueError(
                f"exchange safety check failed: {attr} must not point at a BloFin production host"
            )

    if mode is not ExchangeMode.PAPER_EXCHANGE_DEMO:
        _refuse_non_demo_internal_snapshot_host(settings)
        return

    errors: list[str] = []

    # The demo exchange axis must never coexist with the live-trading axis.
    if settings.execution_mode is not ExecutionMode.PAPER:
        errors.append("exchange_mode=paper_exchange_demo requires execution_mode=paper")
    if settings.enable_real_trading:
        errors.append("exchange_mode=paper_exchange_demo requires enable_real_trading=false")
    if settings.real_trading_enabled:
        errors.append("exchange_mode=paper_exchange_demo is incompatible with real trading")

    if not settings.blofin_demo_enabled:
        errors.append("exchange_mode=paper_exchange_demo requires blofin_demo_enabled=true")

    if not settings.blofin_demo_configured:
        errors.append(
            "exchange_mode=paper_exchange_demo requires BloFin demo credentials "
            "(blofin_api_key, blofin_api_secret, blofin_api_passphrase)"
        )

    rest_url = settings.blofin_demo_rest_base_url.strip()
    if not rest_url:
        errors.append("exchange_mode=paper_exchange_demo requires blofin_demo_rest_base_url")
    elif _host_of(rest_url) in BLOFIN_PRODUCTION_HOSTS:
        errors.append("blofin_demo_rest_base_url must not point at a BloFin production host")
    elif not is_allowlisted_demo_host(rest_url):
        errors.append(
            "blofin_demo_rest_base_url must be an allowlisted BloFin demo host over HTTPS"
        )

    ws_url = settings.blofin_demo_ws_url.strip()
    if ws_url:
        if _host_of(ws_url) in BLOFIN_PRODUCTION_HOSTS:
            errors.append("blofin_demo_ws_url must not point at a BloFin production host")
        elif not is_allowlisted_demo_host(ws_url):
            errors.append("blofin_demo_ws_url must be an allowlisted BloFin demo host over WSS")

    if errors:
        raise ValueError("exchange safety check failed: " + "; ".join(errors))


def _refuse_non_demo_internal_snapshot_host(settings: Settings) -> None:
    """Refuse a non-demo REST host when the paper_internal snapshot read is on.

    An empty URL is a missing runtime gate, not a startup error, so the
    worker can keep its other components up and skip the snapshot. A set URL
    that is not the allowlisted demo host is refused here. Production hosts
    are already refused for every mode above.
    """
    if not settings.blofin_demo_snapshot_internal_read_enabled:
        return
    rest_url = settings.blofin_demo_rest_base_url.strip()
    if not rest_url or is_allowlisted_demo_host(rest_url):
        return
    raise ValueError(
        "exchange safety check failed: blofin_demo_rest_base_url must be an "
        "allowlisted BloFin demo host over HTTPS"
    )
