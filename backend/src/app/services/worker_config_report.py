"""Boot config report for the staging paper worker.

A pure function compares Settings with what the armed staging paper posture
needs and returns expected vs actual per flag. Values are booleans or public
mode tokens, never secrets. Boot publishes the report on the runtime-status
table for ``GET /health/paper-worker``, logs it at error level when staging
is degraded or disarmed, and sends one deduplicated Telegram ops alert.
Nothing here turns a flag on; a failure to publish or alert never raises.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from app.core.config import Environment, Settings
from app.schemas.health import WorkerConfigCheck, WorkerConfigObservation

logger = structlog.get_logger(__name__)

WORKER_CONFIG_COMPONENT = "worker_config"
WORKER_CONFIG_ALERT_COMPONENT = "worker_config_alert"
WORKER_CONFIG_ALERT_COOLDOWN_SECONDS = 6 * 3600

# Order is the bit position in the published failure mask. Append only.
_EXPECTED: dict[str, str] = {
    "watcher_orchestration_enabled": "true",
    "watcher_paper_staging_activation": "true",
    "controlled_watcher_arm": "true",
    "exchange_mode": "paper_internal|paper_exchange_demo",
    "perpetual_evidence_activation": "active",
    "perpetual_evidence_source": "binance_usdm",
    "real_trading_disabled": "true",
    "autonomy_mode": "internal_paper|blofin_demo",
    "telegram_projection": "projection",
    "telegram_boot_conflict": "none",
    "telegram_bot_token_present": "true",
    "telegram_chat_id_present": "true",
}
_ORDER = tuple(_EXPECTED)
# A failing critical check means the Watcher arm is off: disarmed, not degraded.
_CRITICAL = frozenset(_ORDER[:5]) | {"real_trading_disabled"}
_ALERTABLE = frozenset({"degraded", "disarmed_unexpected"})


@dataclass(frozen=True, slots=True)
class WorkerConfigReport:
    status: str
    checks: tuple[WorkerConfigCheck, ...]

    @property
    def failing(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks if not check.ok)

    @property
    def failure_mask(self) -> int:
        return sum(1 << _ORDER.index(name) for name in self.failing)


def build_worker_config_report(
    settings: Settings,
    *,
    boot_degradations: Sequence[str] = (),
) -> WorkerConfigReport:
    """Expected vs actual for the armed staging posture. Pure; no IO."""

    if settings.environment is not Environment.STAGING:
        return WorkerConfigReport("not_applicable", ())
    checks = tuple(_check(name, value) for name, value in _actuals(settings, boot_degradations))
    failing = {check.name for check in checks if not check.ok}
    if failing & _CRITICAL:
        status = "disarmed_unexpected"
    elif failing:
        status = "degraded"
    else:
        status = "ok"
    return WorkerConfigReport(status, checks)


def _actuals(settings: Settings, boot_degradations: Sequence[str]) -> list[tuple[str, str]]:
    from app.controlled_activation.profile import (
        _paper_exchange_allowed,
        controlled_telegram_projection,
        telegram_enrollment_runtime,
    )
    from app.market_activation.profile import (
        activation_state,
        canonicalize_perpetual_evidence_source,
        controlled_watcher_arm,
    )
    from app.services.autonomous_trading import resolve_autonomous_mode

    exchange = settings.exchange_mode.value
    if not _paper_exchange_allowed(settings):
        exchange = f"{exchange}_ungoverned"
    autonomy = resolve_autonomous_mode(settings)
    if controlled_telegram_projection(settings):
        telegram = "projection"
    elif telegram_enrollment_runtime(settings):
        telegram = "enrollment"
    else:
        telegram = "off"
    conflicts = [item for item in boot_degradations if item.startswith("telegram")]
    return [
        ("watcher_orchestration_enabled", _flag(settings.watcher_orchestration_enabled)),
        ("watcher_paper_staging_activation", _flag(settings.watcher_paper_staging_activation)),
        ("controlled_watcher_arm", _flag(controlled_watcher_arm(settings))),
        ("exchange_mode", exchange),
        ("perpetual_evidence_activation", activation_state(settings)),
        (
            "perpetual_evidence_source",
            canonicalize_perpetual_evidence_source(settings.perpetual_evidence_source),
        ),
        (
            "real_trading_disabled",
            _flag(not (settings.enable_real_trading or settings.real_trading_enabled)),
        ),
        ("autonomy_mode", "off" if autonomy.refusal else autonomy.mode.value),
        ("telegram_projection", telegram),
        ("telegram_boot_conflict", conflicts[0][:64] if conflicts else "none"),
        ("telegram_bot_token_present", _flag(bool(settings.telegram_bot_token.strip()))),
        ("telegram_chat_id_present", _flag(bool(settings.telegram_chat_id.strip()))),
    ]


def _check(name: str, actual: str) -> WorkerConfigCheck:
    expected = _EXPECTED[name]
    return WorkerConfigCheck(
        name=name, expected=expected, actual=actual, ok=actual in expected.split("|")
    )


def _flag(value: bool) -> str:
    return "true" if value else "false"


def publish_worker_config_report(
    settings: Settings,
    *,
    boot_degradations: Sequence[str] = (),
    now: datetime | None = None,
) -> WorkerConfigReport:
    """Log, persist, and alert once. Never raises, so boot always continues."""

    report = build_worker_config_report(settings, boot_degradations=boot_degradations)
    moment = now or datetime.now(UTC)
    log = logger.error if report.status in _ALERTABLE else logger.info
    log(
        "paper_worker_config_report",
        status=report.status,
        failing=list(report.failing),
        checks=[check.model_dump() for check in report.checks],
    )
    if report.status == "not_applicable":
        return report
    try:
        _persist(settings, report, now=moment)
    except Exception:
        logger.error("paper_worker_config_report_unpublished", status=report.status)
    if report.status in _ALERTABLE:
        try:
            _alert(settings, report, now=moment)
        except Exception:
            logger.error("paper_worker_config_alert_failed", status=report.status)
    return report


def _persist(settings: Settings, report: WorkerConfigReport, *, now: datetime) -> None:
    from app.db.session import get_session_factory
    from app.persistence.runtime_status import RuntimeStatusWrite, publish_runtime_status

    actual = {check.name: check.actual for check in report.checks}
    publish_runtime_status(
        get_session_factory(),
        RuntimeStatusWrite(
            component=WORKER_CONFIG_COMPONENT,
            worker_id=settings.watcher_paper_worker_id[:80],
            heartbeat_at=now,
            activation_state=report.status,
            request_count=report.failure_mask,
            last_scan_reason=actual["exchange_mode"],
            market_source=actual["perpetual_evidence_source"],
            telegram_runtime_state=actual["telegram_projection"],
            inbound_mode=actual["autonomy_mode"],
            last_error_code=actual["telegram_boot_conflict"],
        ),
    )


def _alert(settings: Settings, report: WorkerConfigReport, *, now: datetime) -> None:
    from app.services.paper_exit_monitor import ExitMonitorAlert
    from app.services.worker_heartbeat import (
        HeartbeatAlertLedger,
        MemoryHeartbeatAlertLedger,
        RuntimeStatusHeartbeatLedger,
        _session_factory,
        raise_prepared_alert,
    )

    ledger: HeartbeatAlertLedger
    try:
        ledger = RuntimeStatusHeartbeatLedger(
            _session_factory(),
            component=WORKER_CONFIG_ALERT_COMPONENT,
            cooldown_seconds=WORKER_CONFIG_ALERT_COOLDOWN_SECONDS,
        )
    except Exception:
        ledger = MemoryHeartbeatAlertLedger(cooldown_seconds=WORKER_CONFIG_ALERT_COOLDOWN_SECONDS)
    alert = ExitMonitorAlert(
        code="worker_config_degraded",
        severity="critical" if report.status == "disarmed_unexpected" else "warning",
        message=(
            f"Staging paper worker booted {report.status}. The listed features stay off "
            "until the env is fixed; trade-safety gates are unchanged."
        ),
        fields={
            "status": report.status,
            "reason": "+".join(report.failing),
            "component": WORKER_CONFIG_COMPONENT,
            "dedup_key": f"worker_config:{report.status}:{report.failure_mask}",
        },
    )
    raise_prepared_alert(settings, alert, now=now, ledger=ledger)


def project_worker_config(row: object | None) -> WorkerConfigObservation:
    """Rebuild the report from the published row. Unknown when absent."""

    if row is None:
        return WorkerConfigObservation()
    status = str(getattr(row, "activation_state", ""))
    if status not in {"ok", "degraded", "disarmed_unexpected", "not_applicable"}:
        return WorkerConfigObservation()
    mask = int(getattr(row, "request_count", 0) or 0)
    stored = {
        "exchange_mode": str(getattr(row, "last_scan_reason", "")),
        "perpetual_evidence_source": str(getattr(row, "market_source", "")),
        "telegram_projection": str(getattr(row, "telegram_runtime_state", "")),
        "autonomy_mode": str(getattr(row, "inbound_mode", "")),
        "telegram_boot_conflict": str(getattr(row, "last_error_code", "")) or "none",
    }
    checks: list[WorkerConfigCheck] = []
    for index, name in enumerate(_ORDER):
        ok = not mask & (1 << index)
        expected = _EXPECTED[name]
        if name in stored:
            actual = stored[name]
        elif expected == "true":
            actual = "true" if ok else "false"
        else:
            actual = expected if ok else "mismatch"
        checks.append(WorkerConfigCheck(name=name, expected=expected, actual=actual, ok=ok))
    return WorkerConfigObservation(
        status=status,
        reported_at=getattr(row, "heartbeat_at", None),
        checks=checks,
    )
