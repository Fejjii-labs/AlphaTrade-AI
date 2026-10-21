"""Send safety alerts through the existing Telegram delivery path.

Kill-switch arming, backfill and stale-loss alerts, repeated degraded or
refused cycles, clock skew, an arming refusal, and a missing worker or
exit-monitor heartbeat all use ``telegram_bot_token`` and
``telegram_chat_id``. No new secret is added. The message never includes a
secret or a P&L amount. A repeated dedup key inside the cooldown does not
send again.

Delivery is queued on the caller's session and flushed after commit, so a
rolled-back close does not notify and a Telegram call does not hold the
transaction open. A rollback logs the dropped alert at error. An observed
monitor alert (not a kill-switch activation) can still be sent after that
rollback, because the condition already happened. Global-setting and
fail-closed pages from ``KillSwitchService.evaluate`` are not sent while a
transaction is open. They are sent after that transaction ends, including
after rollback, because the condition already happened. A committed
activation whose Telegram send fails is logged at error and left marked
unsent. The next health check or worker boot retries that version once
it can be sent, and does not send the same version again. The HTTP send
timeout is at most 5 seconds.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.guardrails.redaction import redact_text
from app.providers.alert_delivery.base import AlertDeliveryPayload
from app.providers.alert_delivery.telegram import TelegramAlertDeliveryProvider
from app.services.paper_exit_monitor import ExitMonitorAlert

logger = structlog.get_logger(__name__)

_QUEUE_KEY = "alphatrade_safety_alerts"
_HOOKS_KEY = "alphatrade_safety_alert_hooks"
_SAFE_FIELDS = frozenset(
    {
        "account_id",
        "code",
        "command_id",
        "component",
        "cycles",
        "dedup_key",
        "elapsed_seconds",
        "error",
        "exit_origin",
        "health_state",
        "heartbeat_age_seconds",
        "net_loss",
        "organization_id",
        "reason",
        "refusal",
        "refused",
        "severity",
        "skew_seconds",
        "source",
        "status",
        "threshold_seconds",
        "trusted_source",
    }
)
_KILL_SWITCH_SOURCES = frozenset(
    {
        "manual",
        "global_setting",
        "drawdown",
        "breaker",
        "fail_closed",
        "exit_monitor",
        "demo_protection",
        "system",
    }
)
_COOLDOWN_SECONDS = {
    "backfill_loss": 86400,
    "stale_live_loss": 86400,
    "degraded_repeat": 900,
    "clock_skew": 900,
    "clock_untrusted": 900,
    "worker_heartbeat_missing": 900,
    "worker_heartbeat_stale": 900,
    "arming_refused": 900,
    "kill_switch_activated": 900,
    "exit_monitor_heartbeat_missing": 900,
    "exit_monitor_heartbeat_stale": 900,
}
_DEFAULT_COOLDOWN_SECONDS = 900
TELEGRAM_SAFETY_MANDATORY_CODES: frozenset[str] = frozenset(
    {
        "kill_switch_activated",
        "backfill_loss",
        "stale_live_loss",
    }
)
TELEGRAM_SAFETY_DEFAULT_CODES: frozenset[str] = frozenset(
    {
        "worker_heartbeat_missing",
        "worker_heartbeat_stale",
        "exit_monitor_heartbeat_missing",
        "exit_monitor_heartbeat_stale",
        "stop_hit",
        "tp_hit",
    }
)
TELEGRAM_SAFETY_LOGS_ONLY_CODES: frozenset[str] = frozenset(
    {
        "degraded_repeat",
        "clock_skew",
        "clock_untrusted",
        "arming_refused",
    }
)
TELEGRAM_SAFETY_OPTIONAL_CODES: frozenset[str] = frozenset(
    {
        "paper_trade_opened",
        "paper_trade_closed",
    }
)
TELEGRAM_SAFETY_KNOWN_CODES: frozenset[str] = (
    TELEGRAM_SAFETY_MANDATORY_CODES
    | TELEGRAM_SAFETY_DEFAULT_CODES
    | TELEGRAM_SAFETY_LOGS_ONLY_CODES
    | TELEGRAM_SAFETY_OPTIONAL_CODES
)
_AMOUNT = re.compile(
    r"(?i)\b(?:net[_\s-]?pnl|pnl|p&l|profit|loss amount)\b\s*[:=]?\s*-?\d+(?:\.\d+)?"
)

_LOCK = threading.Lock()
_SENT: dict[str, datetime] = {}


def reset_safety_alert_dedup() -> None:
    """Forget in-process dedup state. Tests call this between cases."""

    with _LOCK:
        _SENT.clear()
    from app.services.risk.kill_switch import reset_kill_switch_retry_window

    reset_kill_switch_retry_window()


def safety_alert_message(
    *,
    code: str,
    message: str,
    fields: Mapping[str, str],
) -> str:
    """Text safe to hand to Telegram. Unknown fields and amounts are dropped."""

    lines = [f"Code: {code}", redact_text(message)[:500]]
    for key in sorted(fields):
        if key not in _SAFE_FIELDS:
            continue
        value = fields[key]
        if not isinstance(value, str):
            continue
        lines.append(f"{key}: {redact_text(value)[:160]}")
    return _AMOUNT.sub("[redacted]", "\n".join(lines))


def telegram_safety_configured(settings: Settings) -> bool:
    """True when the existing bot token and chat id are both set."""

    return bool(settings.telegram_bot_token.strip() and settings.telegram_chat_id.strip())


def effective_telegram_safety_allowlist(settings: Settings) -> frozenset[str]:
    """Mandatory and default codes plus case-normalized env extras."""

    extras = {item.lower() for item in settings.telegram_safety_alert_allowlist}
    return TELEGRAM_SAFETY_MANDATORY_CODES | TELEGRAM_SAFETY_DEFAULT_CODES | extras


def validate_telegram_safety_extra_allowlist(codes: list[str]) -> None:
    """Reject unknown or logs-only codes in ``TELEGRAM_SAFETY_ALERT_ALLOWLIST``."""

    for code in codes:
        normalized = code.lower()
        if normalized not in TELEGRAM_SAFETY_KNOWN_CODES:
            raise ValueError(f"Unknown telegram safety alert code: {code}")
        if normalized in TELEGRAM_SAFETY_LOGS_ONLY_CODES:
            raise ValueError(
                f"Telegram safety alert code {code} is logs-only and cannot be allowlisted"
            )


def safety_alert_telegram_allowed(settings: Settings, code: str) -> bool:
    """True when this safety-alert code may be sent to Telegram."""

    normalized = code.lower()
    if normalized in TELEGRAM_SAFETY_LOGS_ONLY_CODES:
        return False
    return normalized in effective_telegram_safety_allowlist(settings)


def deliver_safety_alert(
    settings: Settings,
    alert: ExitMonitorAlert,
    *,
    now: datetime | None = None,
    failure_log: Literal["warning", "error"] = "warning",
) -> bool:
    """Send one alert. False when it is deduped, unconfigured, or refused.

    The provider reuses ``telegram_bot_token`` and ``telegram_chat_id``.
    The enable flags are bypassed so a safety alert is not dropped only
    because the general Telegram product flag is off. A missing token or
    chat id still skips the send.
    """

    moment = now or datetime.now(UTC)
    key = alert.fields.get("dedup_key") or alert.code
    if _recently_sent(key, moment, _cooldown(alert.code)):
        return False
    if not safety_alert_telegram_allowed(settings, alert.code):
        logger.info("safety_alert_telegram_suppressed", code=alert.code)
        return False
    if not telegram_safety_configured(settings):
        logger.warning("safety_alert_telegram_unconfigured", code=alert.code)
        return False
    if alert.telegram_verbatim:
        text = alert.message
    else:
        text = safety_alert_message(code=alert.code, message=alert.message, fields=alert.fields)
    payload = AlertDeliveryPayload(
        alert_id=key[:120],
        organization_id=alert.fields.get("organization_id") or "system",
        alert_type=alert.code[:80],
        severity=alert.severity,
        message=text,
        dedup_key=key[:200],
        use_message_verbatim=alert.telegram_verbatim,
    )
    result = TelegramAlertDeliveryProvider(settings).deliver(payload, bypass_enable_check=True)
    if not result.success:
        log = logger.error if failure_log == "error" else logger.warning
        log(
            "safety_alert_telegram_failed",
            code=alert.code,
            skipped=result.skipped,
        )
        return False
    _remember(key, moment)
    return True


def kill_switch_source(source: str) -> str:
    """A source token safe to put in a Telegram message."""

    token = source.strip().lower()
    if token in _KILL_SWITCH_SOURCES:
        return token
    return "system"


def build_kill_switch_alert(
    *,
    source: str,
    organization_id: str,
    dedup_key: str,
) -> ExitMonitorAlert:
    """One kill-switch page. The text has no secret and no P&L amount.

    ``source`` is a fixed token (manual, global setting, drawdown, the other
    breaker and demo paths, or fail-closed). The caller's free-text reason is
    not copied. It can contain an amount or a secret.
    """

    token = kill_switch_source(source)
    if token == "global_setting":
        message = "Global kill switch is active. New entries are refused."
    elif token == "fail_closed":
        message = "Kill switch state could not be read. New entries are refused."
    else:
        message = "Kill switch activated. New entries stay blocked until a human deactivates it."
    org = organization_id.strip()
    if not org:
        org = "system"
    return ExitMonitorAlert(
        code="kill_switch_activated",
        severity="critical",
        message=message,
        fields={
            "source": token,
            "organization_id": org[:64],
            "component": "kill_switch",
            "refusal": "kill_switch",
            "dedup_key": dedup_key[:200],
        },
    )


def notify_kill_switch_activation(
    settings: Settings,
    *,
    source: str,
    organization_id: str,
    version: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Send one kill-switch page outside a database transaction.

    Global-setting and fail-closed observations use this. A successful
    ``activate`` queues instead, and sends only after commit.
    """

    token = kill_switch_source(source)
    org = organization_id.strip() or "system"
    if token == "global_setting":
        key = "kill_switch_activated:global_setting"
    elif version is None:
        key = f"kill_switch_activated:{token}:{org}"
    else:
        key = f"kill_switch_activated:{token}:{org}:{version}"
    alert = build_kill_switch_alert(source=token, organization_id=org, dedup_key=key)
    moment = now or datetime.now(UTC)
    if not telegram_safety_configured(settings):
        if not _recently_sent(key, moment, _cooldown(alert.code)):
            logger.warning("safety_alert_telegram_unconfigured", code=alert.code)
            _remember(key, moment)
        return False
    return deliver_safety_alert(settings, alert, now=moment)


def queue_kill_switch_activation(
    session: Session,
    settings: Settings,
    *,
    source: str,
    organization_id: str,
    version: int,
) -> None:
    """Queue a page for one committed activation. A rollback must not send it."""

    token = kill_switch_source(source)
    org = organization_id.strip() or "system"
    key = f"kill_switch_activated:{token}:{org}:{version}"
    queue_safety_alert(
        session,
        settings,
        build_kill_switch_alert(source=token, organization_id=org, dedup_key=key),
    )


def queue_safety_alert(session: Session, settings: Settings, alert: ExitMonitorAlert) -> None:
    """Hold one alert until the caller commits. A rollback must not send it."""

    pending = session.info.setdefault(_QUEUE_KEY, [])
    if isinstance(pending, list):
        pending.append((settings, alert))
    _bind_safety_alert_hooks(session)


def deliver_queued_safety_alerts(session: Session) -> None:
    """Send alerts queued on this session. Call this only after commit."""

    pending = session.info.pop(_QUEUE_KEY, [])
    if not isinstance(pending, list):
        return
    for item in pending:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        settings, alert = item
        if not isinstance(settings, Settings) or not isinstance(alert, ExitMonitorAlert):
            continue
        sent = False
        try:
            sent = deliver_safety_alert(settings, alert, failure_log="error")
        except Exception as exc:
            logger.error(
                "safety_alert_telegram_failed",
                code=alert.code,
                error=redact_text(str(exc))[:200],
            )
        _note_committed_kill_switch_delivery(session, alert, delivered=sent)


def discard_queued_safety_alerts(session: Session) -> None:
    """Drop queued alerts after a rollback and log each one at error.

    The log is the record that a database failure did not send the page.
    This function does not send. A rolled-back kill-switch activation did
    not happen. Observed monitor alerts are sent by
    :func:`deliver_observed_alert_outside_transaction`.
    """

    pending = session.info.pop(_QUEUE_KEY, [])
    if not isinstance(pending, list):
        return
    for item in pending:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        _settings, alert = item
        if not isinstance(alert, ExitMonitorAlert):
            continue
        logger.error("safety_alert_discarded_with_rollback", code=alert.code)


def deliver_observed_alert_outside_transaction(
    settings: Settings,
    alert: ExitMonitorAlert,
) -> bool:
    """Send an alert whose database write rolled back.

    Use this for a condition that already happened, such as a repeated
    degraded exit-monitor cycle. Do not use it for a kill-switch activation
    that rolled back with the transaction.
    """

    logger.error("safety_alert_unpersisted", code=alert.code)
    try:
        return deliver_safety_alert(settings, alert)
    except Exception as exc:
        logger.error(
            "safety_alert_telegram_failed",
            code=alert.code,
            error=redact_text(str(exc))[:200],
        )
        return False


def _bind_safety_alert_hooks(session: Session) -> None:
    """Send after commit. Log at error after rollback. Bind once per session."""

    if not isinstance(session, Session):
        return
    if session.info.get(_HOOKS_KEY):
        return
    session.info[_HOOKS_KEY] = True

    def _after_commit(sess: Session) -> None:
        try:
            deliver_queued_safety_alerts(sess)
        except Exception as exc:
            logger.error(
                "safety_alert_telegram_failed",
                error=redact_text(str(exc))[:200],
            )

    def _after_rollback(sess: Session) -> None:
        discard_queued_safety_alerts(sess)

    event.listen(session, "after_commit", _after_commit)
    event.listen(session, "after_rollback", _after_rollback)


def _cooldown(code: str) -> timedelta:
    return timedelta(seconds=_COOLDOWN_SECONDS.get(code, _DEFAULT_COOLDOWN_SECONDS))


def kill_switch_page_recently_sent(key: str, now: datetime) -> bool:
    """True when this process already sent this kill-switch version."""

    return _recently_sent(key, now, _cooldown("kill_switch_activated"))


def _kill_switch_dedup_parts(alert: ExitMonitorAlert) -> tuple[str, int] | None:
    key = str(alert.fields.get("dedup_key") or "")
    prefix = "kill_switch_activated:"
    if not key.startswith(prefix):
        return None
    try:
        _source, organization_id, version_text = key[len(prefix) :].split(":", 2)
    except ValueError:
        return None
    if not organization_id or not version_text.isdigit():
        return None
    return organization_id, int(version_text)


def _note_committed_kill_switch_delivery(
    session: Session,
    alert: ExitMonitorAlert,
    *,
    delivered: bool,
) -> None:
    """Mark a committed activation sent after Telegram accepts it.

    A failed send stays ``unsent`` so the next check can retry that version.
    A send this process already recorded is marked sent too, so a restart
    after the cooldown does not page the same version again.
    """

    if alert.code != "kill_switch_activated":
        return
    moment = datetime.now(UTC)
    key = str(alert.fields.get("dedup_key") or "")
    if not delivered and not _recently_sent(key, moment, _cooldown(alert.code)):
        return
    parsed = _kill_switch_dedup_parts(alert)
    if parsed is None:
        return
    organization_id, version = parsed
    from app.services.risk.kill_switch import mark_kill_switch_alert_sent

    mark_kill_switch_alert_sent(
        session,
        organization_id=organization_id,
        version=version,
        now=moment,
    )


def _recently_sent(key: str, now: datetime, cooldown: timedelta) -> bool:
    moment = _aware(now)
    with _LOCK:
        previous = _SENT.get(key)
    if previous is None:
        return False
    return moment - previous < cooldown


def _remember(key: str, now: datetime) -> None:
    with _LOCK:
        _SENT[key] = _aware(now)


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
