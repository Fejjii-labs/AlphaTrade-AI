"""Declarative required-environment manifest and fail-fast startup validation.

Incident (2026-10-25): BLOFIN_ACTIVITY_ORGANIZATION_ID / _EXPECTED_UID existed on
the staging paper worker but not on the API, so manual demo refresh failed with
``blofin_fallback_sync_refused reason=activity_organization_unset``; and enabling
BLOFIN_ACTIVITY_ENABLED without the read-only key set crash-looped the worker.

Errors and logs name missing keys only, never values.
Emergency bypass: ``ENV_VALIDATION_ENFORCE=false``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

ENFORCE_FLAG = "ENV_VALIDATION_ENFORCE"
ENFORCED_ENVIRONMENTS = frozenset({"staging", "production"})
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class Role(StrEnum):
    API = "api"
    PAPER_WORKER = "paper_worker"


@dataclass(frozen=True)
class ConditionalRule:
    """When ``flag`` is truthy, every key in ``requires`` must be non-empty."""

    flag: str
    requires: tuple[str, ...]
    roles: frozenset[Role]


ACTIVITY_PINS = ("BLOFIN_ACTIVITY_ORGANIZATION_ID", "BLOFIN_ACTIVITY_EXPECTED_UID")
READONLY_KEYS = (
    "BLOFIN_READONLY_API_KEY",
    "BLOFIN_READONLY_API_SECRET",
    "BLOFIN_READONLY_API_PASSPHRASE",
)

# Always required per role (in enforced environments).
ALWAYS_REQUIRED: Mapping[Role, tuple[str, ...]] = {
    # The API serves manual demo refresh -> fallback sync needs the account pins
    # (``_require_fallback_account_pin`` in blofin_sync_service.py).
    Role.API: ("ENVIRONMENT", "PROVIDER_MODE", *ACTIVITY_PINS),
    Role.PAPER_WORKER: ("ENVIRONMENT", "PROVIDER_MODE"),
}

CONDITIONAL_RULES: tuple[ConditionalRule, ...] = (
    ConditionalRule(
        flag="BLOFIN_ACTIVITY_ENABLED",
        requires=(*READONLY_KEYS, *ACTIVITY_PINS),
        roles=frozenset({Role.API, Role.PAPER_WORKER}),
    ),
)

# Non-secret settings whose values must be identical across api and worker.
# (Compared by hash in scripts/check_env_drift.py.)
SHARED_VALUE_KEYS: tuple[str, ...] = (
    *ACTIVITY_PINS,
    "ENVIRONMENT",
    "PROVIDER_MODE",
    "EXCHANGE_MODE",
    "BLOFIN_DEMO_REST_BASE_URL",
)


class MissingRequiredEnvError(RuntimeError):
    def __init__(self, role: Role, missing: list[str]) -> None:
        self.role = role
        self.missing = missing
        super().__init__(
            f"Missing required environment for role={role.value}: {', '.join(missing)} "
            f"(set them, or {ENFORCE_FLAG}=false for an emergency bypass)"
        )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE


def required_keys(role: Role, env: Mapping[str, str]) -> list[str]:
    keys: list[str] = list(ALWAYS_REQUIRED[role])
    for rule in CONDITIONAL_RULES:
        if role in rule.roles and _truthy(env.get(rule.flag)):
            keys.extend(rule.requires)
    return list(dict.fromkeys(keys))


def missing_keys(role: Role, env: Mapping[str, str]) -> list[str]:
    return [k for k in required_keys(role, env) if not (env.get(k) or "").strip()]


def should_enforce(env: Mapping[str, str]) -> bool:
    flag = (env.get(ENFORCE_FLAG) or "").strip().lower()
    if flag in _FALSE:
        return False
    if flag in _TRUE:
        return True
    return (env.get("ENVIRONMENT") or "").strip().lower() in ENFORCED_ENVIRONMENTS


def validate_required_env(role: Role, env: Mapping[str, str] | None = None) -> list[str]:
    """Raise MissingRequiredEnvError when enforced; return missing names otherwise."""
    source = os.environ if env is None else env
    missing = missing_keys(role, source)
    if missing and should_enforce(source):
        raise MissingRequiredEnvError(role, missing)
    if missing:
        import structlog

        structlog.get_logger(__name__).warning(
            "required_env_missing_not_enforced", role=role.value, missing=missing
        )
    return missing
