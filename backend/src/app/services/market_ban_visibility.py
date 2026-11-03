"""Publish the Binance REST gate posture and page once when a ban starts.

The gate (``binance_rest_gate``) already refuses every Binance REST read
while banned, so callers fail closed. This module only makes that state
visible on ``GET /health/paper-worker`` and sends one Telegram ops alert per
ban. It never opens the gate and never raises into the watcher cycle.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.schemas.health import MarketDataBanObservation

logger = structlog.get_logger(__name__)

MARKET_GATE_COMPONENT = "market_data_gate"
MARKET_GATE_ALERT_COMPONENT = "market_data_gate_alert"


class MarketBanPublisher:
    """Called after each watcher cycle. Writes only when the posture changes."""

    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]) -> None:
        self._settings = settings
        self._factory = session_factory
        self._published: tuple[object, ...] | None = None
        self._alerted_ban: float | None = None

    def observe(self, *, now: datetime | None = None) -> None:
        try:
            self._observe(now or datetime.now(UTC))
        except Exception:
            logger.warning("market_ban_visibility_failed")

    def _observe(self, now: datetime) -> None:
        from app.market_contracts.adapters.binance_rest_gate import (
            process_binance_rest_gate_state,
        )

        state = process_binance_rest_gate_state()
        if state is None:
            return
        until = (
            None
            if state.until_epoch is None
            else datetime.fromtimestamp(round(state.until_epoch), tz=UTC)
        )
        key = (state.closed_kind, until, state.last_status_code)
        if key != self._published:
            self._publish(state.closed_kind, until, state.last_status_code, now)
            self._published = key
        if state.banned and state.ban_started_epoch != self._alerted_ban:
            self._alerted_ban = state.ban_started_epoch
            logger.error(
                "binance_rest_ban_started", until=None if until is None else until.isoformat()
            )
            self._alert(until, now)

    def _publish(
        self, kind: str | None, until: datetime | None, status: int | None, now: datetime
    ) -> None:
        from app.persistence.runtime_status import RuntimeStatusWrite, publish_runtime_status

        state = {"ban": "banned", "rate_limit": "rate_limited"}.get(kind or "", "open")
        publish_runtime_status(
            self._factory,
            RuntimeStatusWrite(
                component=MARKET_GATE_COMPONENT,
                worker_id=self._settings.watcher_paper_worker_id[:80],
                heartbeat_at=now,
                activation_state=state,
                lease_expires_at=until,
                last_error_code="" if status is None else f"http_{status}",
                market_source="binance_usdm",
            ),
        )

    def _alert(self, until: datetime | None, now: datetime) -> None:
        from app.services.paper_exit_monitor import ExitMonitorAlert
        from app.services.worker_heartbeat import RuntimeStatusHeartbeatLedger, raise_prepared_alert

        alert = ExitMonitorAlert(
            code="market_data_banned",
            severity="critical",
            message=(
                "Binance USD-M banned this worker's IP (HTTP 418). Market reads are paused "
                "and the Watcher makes no paper decisions until the ban ends."
            ),
            fields={
                "status": "banned",
                "reason": "http_418",
                "source": "binance_usdm",
                "until": "" if until is None else until.isoformat(),
                "dedup_key": "market_data_banned",
            },
        )
        ledger = RuntimeStatusHeartbeatLedger(self._factory, component=MARKET_GATE_ALERT_COMPONENT)
        raise_prepared_alert(self._settings, alert, now=now, ledger=ledger)


def project_market_ban(row: object | None, *, now: datetime) -> MarketDataBanObservation:
    """``banned`` only while the published deadline is still in the future."""

    if row is None:
        return MarketDataBanObservation()
    state = str(getattr(row, "activation_state", "")) or "unknown"
    until = getattr(row, "lease_expires_at", None)
    code = str(getattr(row, "last_error_code", ""))
    status = int(code[5:]) if code.startswith("http_") and code[5:].isdigit() else None
    live = until is not None and until > now
    return MarketDataBanObservation(
        banned=state == "banned" and live,
        state=state if live or state == "open" else "open",
        until=until,
        last_status_code=status,
        observed_at=getattr(row, "heartbeat_at", None),
    )
