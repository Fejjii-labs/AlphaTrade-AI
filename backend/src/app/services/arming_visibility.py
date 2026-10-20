"""Make a refused paper arming visible outside the worker log.

The exit monitor and the autonomous loops already refuse to arm when the
loss cooldown is not a positive finite number of seconds, or when the cycle
is longer than the smallest configured timeframe. This module records that
refusal where ``GET /health`` and ``GET /health/paper-worker`` can read it,
and sends one deduplicated Telegram alert. It does not turn a flag on and
it does not attach the monitor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from app.core.config import Settings
from app.persistence.runtime_status import (
    PAPER_ARMING_COMPONENT,
    RuntimeStatusWrite,
    publish_runtime_status,
)
from app.schemas.health import ArmingRefusalObservation
from app.services.arming_gates import (
    COOLDOWN_INVALID,
    CYCLE_INTERVAL_EXCEEDS_TIMEFRAME,
    paper_arming_refusal,
)
from app.services.autonomous_trading import resolve_autonomous_mode
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.safety_alerts import deliver_safety_alert
from app.services.worker_heartbeat import (
    HeartbeatAlertLedger,
    RuntimeStatusHeartbeatLedger,
    delete_exit_monitor_heartbeat,
    publish_exit_monitor_heartbeat,
    raise_prepared_alert,
)

logger = structlog.get_logger(__name__)

ARMING_ALERT_COMPONENT = "paper_arming_alert"
_MONITOR_RUNNING = "monitor_running"
_ARMING_REASONS = frozenset({COOLDOWN_INVALID, CYCLE_INTERVAL_EXCEEDS_TIMEFRAME})
_REASON_TOKEN = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


@dataclass(frozen=True, slots=True)
class BootArmingPosture:
    """What boot decided. ``reason`` is empty when nothing refused."""

    reason: str
    source: str
    exit_monitor_attached: bool

    @property
    def refused(self) -> bool:
        return self.reason != ""


def describe_boot_arming(
    settings: Settings,
    *,
    exit_monitor_attached: bool,
) -> BootArmingPosture:
    """Refusal reason and which loop refused. No database and no alert."""

    from app.core.disarmed_worker_boot import settings_are_disarmed_paper_worker

    sources: list[str] = []
    reason = ""
    if settings.paper_exit_monitor_enabled and not settings_are_disarmed_paper_worker(settings):
        refusal = paper_arming_refusal(settings)
        if refusal is not None:
            sources.append("exit_monitor")
            reason = refusal
    resolution = resolve_autonomous_mode(settings)
    if resolution.refusal in _ARMING_REASONS:
        sources.append("autonomous")
        if not reason:
            reason = str(resolution.refusal)
    if exit_monitor_attached and "exit_monitor" not in sources:
        sources.append("exit_monitor")
    # Attachment is the caller's fact. An autonomous-loop refusal must not
    # pretend the exit-monitor thread is gone.
    return BootArmingPosture(
        reason=reason,
        source="+".join(sorted(sources)),
        exit_monitor_attached=exit_monitor_attached,
    )


def publish_boot_arming_posture(
    settings: Settings,
    *,
    exit_monitor_attached: bool,
    now: datetime | None = None,
) -> BootArmingPosture | None:
    """Persist the boot decision and send one alert when arming was refused.

    A disarmed worker returns before any session. A database failure is
    logged and does not raise. The Telegram attempt still runs, so a
    database outage does not hide the refusal.
    """

    from app.core.disarmed_worker_boot import settings_are_disarmed_paper_worker

    if settings_are_disarmed_paper_worker(settings):
        return None
    posture = describe_boot_arming(settings, exit_monitor_attached=exit_monitor_attached)
    moment = now or datetime.now(UTC)
    try:
        _persist_posture(settings, posture, now=moment)
    except Exception as exc:
        if type(exc) is AssertionError:
            raise
        logger.error("paper_arming_posture_unpublished", reason=posture.reason)
    if posture.refused:
        logger.warning(
            "paper_arming_refused",
            reason=posture.reason,
            source=posture.source,
        )
        try:
            from app.services.worker_heartbeat import _session_factory

            raise_arming_refusal_alert(
                settings,
                project_arming_refusal(_row_view(posture, now=moment)),
                now=moment,
                ledger=RuntimeStatusHeartbeatLedger(
                    _session_factory(),
                    component=ARMING_ALERT_COMPONENT,
                ),
            )
        except Exception as exc:
            if type(exc) is AssertionError:
                raise
            logger.error("paper_arming_alert_failed", reason=posture.reason)
    return posture


def project_arming_refusal(row: object | None) -> ArmingRefusalObservation:
    """Read the boot row. Anything other than a refused token stays quiet."""

    if row is None or str(getattr(row, "activation_state", "")) != "refused":
        return ArmingRefusalObservation()
    reason = _safe_token(str(getattr(row, "last_error_code", "")))
    source = _safe_source(str(getattr(row, "last_scan_reason", "")))
    if not reason:
        return ArmingRefusalObservation()
    return ArmingRefusalObservation(refused=True, reason=reason, source=source)


def exit_monitor_expected(row: object | None) -> bool:
    """True when the exit-monitor thread is attached.

    A refused autonomous loop does not clear this. Boot records
    ``monitor_running`` on the arming row when the thread is attached, and
    that marker counts even if ``activation_state`` is ``refused``. A row
    marked ``armed`` whose source includes ``exit_monitor`` also counts, so
    a thread that was attached and then wrote no heartbeat is ``missing``.
    """

    if row is None:
        return False
    raw = str(getattr(row, "last_scan_reason", ""))
    if _MONITOR_RUNNING in raw.split("+"):
        return True
    if str(getattr(row, "activation_state", "")) != "armed":
        return False
    return "exit_monitor" in _safe_source(raw).split("+")


def arming_refusal_alert(observation: ArmingRefusalObservation) -> ExitMonitorAlert | None:
    """One page for a boot refusal. None when arming was not refused."""

    if not observation.refused or not observation.reason:
        return None
    return ExitMonitorAlert(
        code="arming_refused",
        severity="critical",
        message=(
            "Paper automation refused to arm. The exit monitor or the autonomous "
            "loop stayed off because the arming gate failed."
        ),
        fields={
            "reason": observation.reason,
            "source": observation.source,
            "component": PAPER_ARMING_COMPONENT,
            "refusal": observation.reason,
            "dedup_key": f"arming_refused:{observation.reason}",
        },
    )


def raise_arming_refusal_alert(
    settings: Settings,
    observation: ArmingRefusalObservation,
    *,
    now: datetime,
    ledger: HeartbeatAlertLedger | None = None,
) -> bool:
    """Send the arming refusal once per reason. False when nothing is sent."""

    alert = arming_refusal_alert(observation)
    if alert is None:
        return False
    if ledger is None:
        return _deliver_without_ledger(settings, alert, now=now)
    return raise_prepared_alert(settings, alert, now=now, ledger=ledger)


def _deliver_without_ledger(
    settings: Settings,
    alert: ExitMonitorAlert,
    *,
    now: datetime,
) -> bool:
    """Boot path. Dedup lives in the Telegram sender. A failure is logged."""

    try:
        return deliver_safety_alert(settings, alert, now=now)
    except Exception as exc:
        from app.guardrails.redaction import redact_text

        logger.error(
            "paper_arming_alert_failed",
            reason=alert.fields.get("reason", ""),
            error=redact_text(str(exc))[:200],
        )
        return False


def _persist_posture(settings: Settings, posture: BootArmingPosture, *, now: datetime) -> None:
    from app.db.session import get_session_factory

    factory = get_session_factory()
    if posture.refused:
        state = "refused"
    elif posture.exit_monitor_attached:
        state = "armed"
    else:
        state = "clear"
    publish_runtime_status(
        factory,
        RuntimeStatusWrite(
            component=PAPER_ARMING_COMPONENT,
            worker_id=settings.watcher_paper_worker_id[:80],
            heartbeat_at=now,
            activation_state=state,
            last_scan_reason=_arming_scan_reason(posture)[:64],
            last_error_code=posture.reason[:64],
            market_source="paper_arming",
        ),
    )
    # Keep the heartbeat when the thread is attached. A loop refusal must
    # not delete it, or a later hang is reported as disabled.
    if posture.exit_monitor_attached:
        publish_exit_monitor_heartbeat(
            settings,
            now=now,
            status="starting",
            session_factory=factory,
        )
        return
    delete_exit_monitor_heartbeat(factory)


def _arming_scan_reason(posture: BootArmingPosture) -> str:
    """Public source tokens, plus ``monitor_running`` when the thread is up."""

    if not posture.exit_monitor_attached:
        return posture.source
    if not posture.source:
        return _MONITOR_RUNNING
    return f"{posture.source}+{_MONITOR_RUNNING}"


def _row_view(posture: BootArmingPosture, *, now: datetime) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        activation_state="refused" if posture.refused else "clear",
        last_error_code=posture.reason,
        last_scan_reason=posture.source,
        heartbeat_at=now,
    )


def _safe_token(value: str) -> str:
    token = value.strip()
    if not token or len(token) > 64:
        return ""
    if any(char not in _REASON_TOKEN for char in token):
        return ""
    return token


def _safe_source(value: str) -> str:
    allowed = {"exit_monitor", "autonomous"}
    parts = [part for part in value.split("+") if _safe_token(part) in allowed]
    return "+".join(sorted(set(parts)))
