"""Blueprint syncs must not reset operator settings (docs/staging_deployment_runbook.md).

Every env var in render.yaml is either a pin or an operator setting:

* Pins carry a literal ``value``. A Blueprint sync re-applies it on purpose:
  paper-only and real-money posture, auth and rate-limit security, and the
  staging market-evidence contract. Operators do not change these.
* Operator settings use ``sync: false`` and no value. Render keeps whatever is
  set in the dashboard across deploys and syncs. When unset, the code default
  in ``Settings`` applies, and every one of those defaults is off or
  conservative (checked below).

A new key must be added to one of the two sets, so it cannot silently become
a value a deploy resets.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]

PINNED: frozenset[str] = frozenset(
    {
        # Paper-only and real-money posture.
        "ENVIRONMENT",
        "EXECUTION_MODE",
        "ENABLE_REAL_TRADING",
        "BILLING_ENABLED",
        # Auth, cookies, rate limits, proxy trust, CORS, logging.
        "ACCESS_TOKEN_DENYLIST_ENABLED",
        "ACCESS_TOKEN_DENYLIST_FAIL_CLOSED",
        "ACCESS_TOKEN_DENYLIST_USE_REDIS",
        "AUTH_COOKIE_SAMESITE",
        "AUTH_COOKIE_SECURE",
        "AUTH_OMIT_REFRESH_FROM_BODY",
        "AUTH_REFRESH_COOKIE_ENABLED",
        "CORS_ORIGINS",
        "DEBUG",
        "LOG_JSON",
        "RATE_LIMIT_ALLOW_IN_MEMORY_FALLBACK",
        "RATE_LIMIT_USE_REDIS",
        "TRUSTED_PROXY_HOPS",
        "REQUIRE_EMAIL_VERIFIED",
        "DEMO_SEED_ENABLED",
        # Staging public market-evidence contract (unset would fall back to replay).
        "MARKET_DATA_ENABLED",
        "MARKET_DATA_FUTURES_BASE_URL",
        "BYBIT_PERPETUAL_BASE_URL",
        "PERPETUAL_EVIDENCE_SOURCE",
        "PERPETUAL_EVIDENCE_SECONDARY_SOURCE",
        "PERPETUAL_EVIDENCE_TIMEOUT_SECONDS",
        # Allocator arena cap for the 512 MiB worker.
        "MALLOC_ARENA_MAX",
    }
)

SECRETS: frozenset[str] = frozenset(
    {"DATABASE_URL", "REDIS_URL", "QDRANT_URL", "QDRANT_API_KEY", "JWT_SECRET", "OPENAI_API_KEY"}
)

# Operator settings and the Settings field whose code default applies when unset.
OPERATOR: dict[str, str] = {
    "PROVIDER_MODE": "provider_mode",
    "EXCHANGE_MODE": "exchange_mode",
    "LLM_MODEL": "llm_model",
    "EMBEDDINGS_MODEL": "embeddings_model",
    "ALERT_DELIVERY_ENABLED": "alert_delivery_enabled",
    "ALERT_WEBHOOK_ENABLED": "alert_webhook_enabled",
    "EMAIL_PROVIDER": "email_provider",
    "EMAIL_ALERTS_ENABLED": "email_alerts_enabled",
    "ENABLE_PAPER_SCHEDULER": "enable_paper_scheduler",
    "PAPER_SIGNAL_ORCHESTRATION_ENABLED": "paper_signal_orchestration_enabled",
    "TRADINGVIEW_WEBHOOK_ENABLED": "tradingview_webhook_enabled",
    "METRICS_ENABLED": "metrics_enabled",
    "MARKET_WATCHER_ENABLED": "market_watcher_enabled",
    "MARKET_WATCHER_BRIDGE_ENABLED": "market_watcher_bridge_enabled",
    "MARKET_WATCHER_BRIDGE_AUTO_TICK": "market_watcher_bridge_auto_tick",
    "WATCHER_ORCHESTRATION_ENABLED": "watcher_orchestration_enabled",
    "WATCHER_PAPER_STAGING_ACTIVATION": "watcher_paper_staging_activation",
    "WATCHER_PAPER_ORGANIZATION_ID": "watcher_paper_organization_id",
    "TELEGRAM_ALERTS_ENABLED": "telegram_alerts_enabled",
    "TELEGRAM_INTERACTION_ENABLED": "telegram_interaction_enabled",
    "AUTOMATIC_TELEGRAM_DELIVERY_ENABLED": "automatic_telegram_delivery_enabled",
    "TELEGRAM_PAPER_ACTIVATION_ARMED": "telegram_paper_activation_armed",
    "TELEGRAM_INBOUND_MODE": "telegram_inbound_mode",
    "TELEGRAM_NETWORK_PERMITTED": "telegram_network_permitted",
}

# Settings that turn something on must default to off when the dashboard has no value.
_OFF_WHEN_UNSET = {
    "alert_delivery_enabled",
    "alert_webhook_enabled",
    "email_alerts_enabled",
    "enable_paper_scheduler",
    "paper_signal_orchestration_enabled",
    "tradingview_webhook_enabled",
    "metrics_enabled",
    "market_watcher_enabled",
    "market_watcher_bridge_enabled",
    "market_watcher_bridge_auto_tick",
    "watcher_orchestration_enabled",
    "watcher_paper_staging_activation",
    "telegram_alerts_enabled",
    "telegram_interaction_enabled",
    "automatic_telegram_delivery_enabled",
    "telegram_paper_activation_armed",
    "telegram_network_permitted",
}


def _env_items() -> list[tuple[str, dict[str, object]]]:
    document = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    return [
        (str(service["name"]), item)
        for service in document["services"]
        for item in service["envVars"]
    ]


def test_every_blueprint_key_is_classified() -> None:
    known = PINNED | SECRETS | set(OPERATOR)
    unknown = sorted({str(item["key"]) for _, item in _env_items()} - known)
    assert unknown == [], f"classify these render.yaml keys as PINNED or OPERATOR: {unknown}"
    assert not PINNED & set(OPERATOR)


def test_operator_settings_and_secrets_are_never_reset_by_a_sync() -> None:
    for name, item in _env_items():
        key = str(item["key"])
        if key in OPERATOR or key in SECRETS:
            assert item.get("sync") is False, (name, key)
            assert "value" not in item, (name, key)


def test_pins_keep_a_literal_value() -> None:
    for name, item in _env_items():
        if item["key"] in PINNED:
            assert "value" in item, (name, item["key"])
            assert item.get("sync") is not False, (name, item["key"])


def test_real_money_pins_stay_hard_coded_off() -> None:
    for name, item in _env_items():
        if item["key"] == "ENABLE_REAL_TRADING":
            assert item["value"] is False or str(item["value"]).lower() == "false", name
        if item["key"] == "EXECUTION_MODE":
            assert item["value"] == "paper", name
        if item["key"] == "ENVIRONMENT":
            assert item["value"] == "staging", name


def test_unset_operator_settings_fall_back_to_safe_defaults() -> None:
    defaults = Settings(_env_file=None)
    for field in OPERATOR.values():
        assert hasattr(defaults, field), field
    for field in _OFF_WHEN_UNSET:
        assert getattr(defaults, field) is False, field
    assert defaults.exchange_mode.value == "paper_internal"
    assert defaults.telegram_inbound_mode.value == "off"
    assert defaults.email_provider == "mock"
    assert defaults.watcher_paper_organization_id == ""
    assert defaults.enable_real_trading is False
    assert defaults.real_trading_enabled is False
