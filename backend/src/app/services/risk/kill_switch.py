"""Authoritative kill-switch enforcement for execution adapters (AT-014).

Organization state is persisted in PostgreSQL. An optional process-level
``Settings.global_kill_switch_active`` provides an emergency global gate for
ops (no platform-admin role exists in the current multi-tenant model).

Fail closed: if organization state cannot be read from the database, execution
is refused.
"""

from __future__ import annotations

import contextlib
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

import structlog
from sqlalchemy import event, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import ConflictError, TradingPolicyError, ValidationAppError
from app.db.models import KillSwitchState
from app.db.runtime_status import ControlledRuntimeStatusRow
from app.persistence.runtime_status import (
    PAPER_KILL_SWITCH_COMPONENT,
    paper_kill_switch_component,
)
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType, AuditResult, AuditSeverity
from app.schemas.health import PaperWorkerKillSwitch
from app.schemas.risk import KillSwitchMutationRequest, KillSwitchScope, KillSwitchStatus
from app.services.audit_service import AuditService

if TYPE_CHECKING:
    from app.services.safety_epoch import SafetyEpochService

logger = structlog.get_logger(__name__)

_ALERT_UNSENT = "unsent"
_ALERT_SENT = "sent"
_RETRY_MIN_INTERVAL_SECONDS = 60.0
_RETRY_LOCK = threading.Lock()
_RETRY_CLAIMED: dict[str, datetime] = {}


class ExecutionKillSwitch(Protocol):
    """Contract future sandbox/real adapters must honor before side effects."""

    def assert_execution_allowed(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> None:
        """Raise TradingPolicyError when execution must be refused."""
        ...

    def is_execution_blocked(self, *, organization_id: uuid.UUID) -> bool:
        """Return True when execution must be refused (fail closed on errors)."""
        ...


@dataclass(frozen=True)
class KillSwitchEvaluation:
    """Result of an authoritative kill-switch check."""

    blocked: bool
    reason_code: str
    organization_active: bool
    global_active: bool
    status: KillSwitchStatus | None = None


class KillSwitchService:
    """PostgreSQL-backed organization kill switch with fail-closed reads."""

    def __init__(
        self,
        session: Session,
        audit_service: AuditService,
        settings: Settings,
    ) -> None:
        self._session = session
        self._audit = audit_service
        self._settings = settings

    def get_status(self, *, organization_id: uuid.UUID) -> KillSwitchStatus:
        row = self._get_or_create_row(organization_id=organization_id)
        return self._to_status(row)

    def activate(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        payload: KillSwitchMutationRequest,
        actor_type: ActorType = ActorType.USER,
        source: str = "system",
    ) -> KillSwitchStatus:
        self._require_confirm(payload)
        row = self._get_or_create_row(organization_id=organization_id)
        self._check_version(row, payload.expected_version)
        if row.active:
            return self._to_status(row)

        now = datetime.now(UTC)
        reason = payload.reason.strip()
        current_version = int(row.version)
        # A system arm, such as the exit monitor, must not be stored as the
        # trade owner flipping the switch by hand.
        stored_user_id = None if actor_type is ActorType.SYSTEM else actor_user_id
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(KillSwitchState)
                .where(
                    KillSwitchState.organization_id == organization_id,
                    KillSwitchState.version == current_version,
                    KillSwitchState.active.is_(False),
                )
                .values(
                    active=True,
                    reason=reason,
                    activated_by=stored_user_id,
                    activated_at=now,
                    deactivated_by=None,
                    deactivated_at=None,
                    version=current_version + 1,
                )
            ),
        )
        if result.rowcount != 1:
            self._session.expire_all()
            refreshed = self._get_or_create_row(organization_id=organization_id)
            if refreshed.active:
                return self._to_status(refreshed)
            raise ConflictError(
                "Kill switch was modified concurrently; refresh and retry.",
                details={
                    "reason": "version_conflict",
                    "expected_version": str(current_version),
                    "current_version": str(refreshed.version),
                },
            )
        self._session.expire_all()
        row = self._get_or_create_row(organization_id=organization_id)
        self._safety_epochs().advance_for_kill_activation(
            organization_id=organization_id,
            at=now,
        )
        self._audit_mutation(
            organization_id=organization_id,
            actor_user_id=stored_user_id,
            event_type=AuditEventType.KILL_SWITCH_ACTIVATED,
            row=row,
            actor_type=actor_type,
        )
        self._record_kill_switch_posture(
            organization_id=organization_id,
            source=source,
            active=True,
            at=now,
            version=int(row.version),
        )
        # Queue after the savepoint. A nested commit emits after_commit, and
        # the page must stay attached to the outer commit.
        self._queue_activation_alert(
            organization_id=organization_id,
            source=source,
            version=int(row.version),
        )
        return self._to_status(row)

    def deactivate(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        payload: KillSwitchMutationRequest,
    ) -> KillSwitchStatus:
        self._require_confirm(payload)
        row = self._get_or_create_row(organization_id=organization_id)
        self._check_version(row, payload.expected_version)
        if not row.active:
            return self._to_status(row)

        now = datetime.now(UTC)
        reason = payload.reason.strip()
        current_version = int(row.version)
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(KillSwitchState)
                .where(
                    KillSwitchState.organization_id == organization_id,
                    KillSwitchState.version == current_version,
                    KillSwitchState.active.is_(True),
                )
                .values(
                    active=False,
                    reason=reason,
                    deactivated_by=actor_user_id,
                    deactivated_at=now,
                    version=current_version + 1,
                )
            ),
        )
        if result.rowcount != 1:
            self._session.expire_all()
            refreshed = self._get_or_create_row(organization_id=organization_id)
            if not refreshed.active:
                return self._to_status(refreshed)
            raise ConflictError(
                "Kill switch was modified concurrently; refresh and retry.",
                details={
                    "reason": "version_conflict",
                    "expected_version": str(current_version),
                    "current_version": str(refreshed.version),
                },
            )
        self._session.expire_all()
        row = self._get_or_create_row(organization_id=organization_id)
        self._safety_epochs().clear_blocking_for_kill_deactivation(
            organization_id=organization_id,
            at=now,
        )
        self._audit_mutation(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            event_type=AuditEventType.KILL_SWITCH_DEACTIVATED,
            row=row,
        )
        self._record_kill_switch_posture(
            organization_id=organization_id,
            source="",
            active=False,
            at=now,
            version=0,
        )
        return self._to_status(row)

    def evaluate(self, *, organization_id: uuid.UUID) -> KillSwitchEvaluation:
        """Read authoritative state and fail closed on storage errors.

        A global-setting or fail-closed page is not sent inside an open
        transaction. It is sent after that transaction ends. A rollback
        still pages, because the condition already happened.
        """
        global_active = bool(self._settings.global_kill_switch_active)
        try:
            status = self.get_status(organization_id=organization_id)
        except Exception:
            self._notify_activation(source="fail_closed", organization_id=organization_id)
            return KillSwitchEvaluation(
                blocked=True,
                reason_code="kill_switch_unavailable",
                organization_active=False,
                global_active=global_active,
                status=None,
            )

        org_active = bool(status.active)
        if global_active:
            self._notify_activation(source="global_setting", organization_id=organization_id)
            return KillSwitchEvaluation(
                blocked=True,
                reason_code="global_kill_switch_active",
                organization_active=org_active,
                global_active=True,
                status=status,
            )
        if org_active:
            return KillSwitchEvaluation(
                blocked=True,
                reason_code="kill_switch_active",
                organization_active=True,
                global_active=False,
                status=status,
            )
        return KillSwitchEvaluation(
            blocked=False,
            reason_code="ok",
            organization_active=False,
            global_active=False,
            status=status,
        )

    def is_execution_blocked(self, *, organization_id: uuid.UUID) -> bool:
        return self.evaluate(organization_id=organization_id).blocked

    def assert_execution_allowed(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> None:
        evaluation = self.evaluate(organization_id=organization_id)
        if not evaluation.blocked:
            return

        if evaluation.reason_code == "kill_switch_unavailable":
            self._audit_trigger(
                organization_id=organization_id,
                user_id=user_id,
                reason=evaluation.reason_code,
            )
            raise TradingPolicyError(
                "Kill switch state is unavailable; execution refused.",
                details={"reason": evaluation.reason_code},
            )

        self._audit_trigger(
            organization_id=organization_id,
            user_id=user_id,
            reason=evaluation.reason_code,
        )
        raise TradingPolicyError(
            "Kill switch is active; new execution is blocked.",
            details={"reason": evaluation.reason_code},
        )

    def _get_or_create_row(self, *, organization_id: uuid.UUID) -> KillSwitchState:
        row = self._session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == organization_id)
        )
        if row is not None:
            return row

        nested = self._session.begin_nested()
        try:
            row = KillSwitchState(
                organization_id=organization_id,
                active=False,
                version=1,
            )
            self._session.add(row)
            self._session.flush()
            nested.commit()
            return row
        except IntegrityError:
            nested.rollback()
            existing = self._session.scalar(
                select(KillSwitchState).where(KillSwitchState.organization_id == organization_id)
            )
            if existing is None:
                raise
            return existing

    def _to_status(self, row: KillSwitchState) -> KillSwitchStatus:
        global_active = bool(self._settings.global_kill_switch_active)
        return KillSwitchStatus(
            organization_id=row.organization_id,
            active=bool(row.active),
            reason=row.reason,
            activated_by=row.activated_by,
            activated_at=row.activated_at,
            deactivated_by=row.deactivated_by,
            deactivated_at=row.deactivated_at,
            version=int(row.version),
            scope=KillSwitchScope.ORGANIZATION,
            global_active=global_active,
            execution_blocked=bool(row.active) or global_active,
        )

    @staticmethod
    def _require_confirm(payload: KillSwitchMutationRequest) -> None:
        if not payload.confirm:
            raise ValidationAppError(
                "Kill switch changes require confirm=true.",
                details={"reason": "confirmation_required"},
            )
        if not payload.reason.strip():
            raise ValidationAppError(
                "A reason is required for kill switch changes.",
                details={"reason": "reason_required"},
            )

    @staticmethod
    def _check_version(row: KillSwitchState, expected: int | None) -> None:
        if expected is None:
            return
        if int(row.version) != int(expected):
            raise ConflictError(
                "Kill switch was modified concurrently; refresh and retry.",
                details={
                    "reason": "version_conflict",
                    "expected_version": str(expected),
                    "current_version": str(row.version),
                },
            )

    def _audit_mutation(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
        event_type: AuditEventType,
        row: KillSwitchState,
        actor_type: ActorType = ActorType.USER,
    ) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id="kill-switch",
                trace_id="kill-switch",
                event_type=event_type,
                resource_type="kill_switch",
                resource_id=str(row.id),
                organization_id=organization_id,
                user_id=actor_user_id,
                actor_type=actor_type,
                result=AuditResult.SUCCESS,
                severity=AuditSeverity.CRITICAL if row.active else AuditSeverity.HIGH,
                metadata={
                    "active": str(row.active),
                    "version": str(row.version),
                    "scope": KillSwitchScope.ORGANIZATION,
                    "reason": (row.reason or "")[:200],
                },
            )
        )

    def _safety_epochs(self) -> SafetyEpochService:
        from app.services.risk.settings_service import RiskSettingsService
        from app.services.safety_epoch import SafetyEpochService

        return SafetyEpochService(
            self._session,
            self._settings,
            RiskSettingsService(self._session, self._audit),
        )

    def _queue_activation_alert(
        self,
        *,
        organization_id: uuid.UUID,
        source: str,
        version: int,
    ) -> None:
        """Queue the page. The session hook sends it after commit."""

        try:
            from app.services.safety_alerts import queue_kill_switch_activation

            queue_kill_switch_activation(
                self._session,
                self._settings,
                source=source,
                organization_id=str(organization_id),
                version=version,
            )
        except Exception:
            logger.error("kill_switch_alert_failed", source="activation")

    def _notify_activation(self, *, source: str, organization_id: uuid.UUID) -> None:
        """Page for a global setting or a fail-closed read. No database write.

        When the caller still holds a transaction, the HTTP send waits until
        that transaction ends so it does not hold the connection.
        """

        session = self._session
        if isinstance(session, Session) and session.in_transaction():
            self._defer_activation_page(source=source, organization_id=organization_id)
            return
        self._send_activation_page(source=source, organization_id=organization_id)

    def _defer_activation_page(self, *, source: str, organization_id: uuid.UUID) -> None:
        """Send after the outermost transaction ends, including on rollback."""

        session = self._session
        pending = session.info.get("_deferred_kill_pages")
        if not isinstance(pending, list):
            pending = []
            session.info["_deferred_kill_pages"] = pending
        pending.append((source, organization_id))
        if session.info.get("_deferred_kill_pages_bound"):
            return
        session.info["_deferred_kill_pages_bound"] = True

        def _after(sess: Session, transaction: object) -> None:
            del transaction
            if sess.in_transaction():
                return
            pages = sess.info.pop("_deferred_kill_pages", [])
            if not isinstance(pages, list):
                return
            for item in pages:
                if not isinstance(item, tuple) or len(item) != 2:
                    continue
                page_source, page_org = item
                if not isinstance(page_source, str) or not isinstance(page_org, uuid.UUID):
                    continue
                self._send_activation_page(source=page_source, organization_id=page_org)

        event.listen(session, "after_transaction_end", _after)

    def _send_activation_page(self, *, source: str, organization_id: uuid.UUID) -> None:
        """Send one page. A failure is logged and does not raise."""

        try:
            from app.services.safety_alerts import notify_kill_switch_activation

            notify_kill_switch_activation(
                self._settings,
                source=source,
                organization_id=str(organization_id),
            )
        except Exception:
            logger.error("kill_switch_alert_failed", source=source)

    def _record_kill_switch_posture(
        self,
        *,
        organization_id: uuid.UUID,
        source: str,
        active: bool,
        at: datetime,
        version: int,
    ) -> None:
        """Remember a safe source token on this session. The reason is not stored.

        An activation marks its page ``unsent`` in the same transaction as
        the kill-switch row, inside a savepoint. The slot is one row per
        organization, written with an upsert so two first inserts of the
        same key cannot roll the activation back. The version is the dedup
        key. A later send marks that same version ``sent``.
        """

        try:
            from app.services.safety_alerts import kill_switch_source

            token = kill_switch_source(source) if active else ""
            alert_state = _ALERT_UNSENT if active else ""
            epoch = version if active else 0
            with self._session.begin_nested():
                _upsert_kill_switch_posture(
                    self._session,
                    organization_id=organization_id,
                    token=token,
                    active=active,
                    at=at,
                    epoch=epoch,
                    alert_state=alert_state,
                )
        except AssertionError:
            raise
        except Exception:
            logger.error("kill_switch_source_unrecorded")

    def _audit_trigger(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        reason: str,
    ) -> None:
        # Block path raises before the route commits — keep the trigger durable.
        self._audit.record_durable_isolated(
            AuditRecordCreate(
                request_id="kill-switch",
                trace_id="kill-switch",
                event_type=AuditEventType.KILL_SWITCH_TRIGGERED,
                resource_type="kill_switch",
                resource_id=str(organization_id),
                organization_id=organization_id,
                user_id=user_id,
                actor_type=ActorType.SYSTEM,
                result=AuditResult.BLOCKED,
                severity=AuditSeverity.CRITICAL,
                metadata={"reason": reason, "scope": KillSwitchScope.ORGANIZATION},
            )
        )


def read_paper_worker_kill_switch(settings: Settings) -> PaperWorkerKillSwitch:
    """Kill-switch posture for ``GET /health/paper-worker``.

    The global setting wins. Otherwise one active organization row is
    reported. An unreadable database fails closed. The free-text reason is
    not copied.
    """

    if bool(settings.global_kill_switch_active):
        return PaperWorkerKillSwitch(active=True, source="global_setting", since=None)
    try:
        from app.db.session import get_session_factory

        with get_session_factory()() as session:
            return _project_kill_switch(session, settings)
    except Exception:
        logger.error("paper_worker_kill_switch_unreadable")
        return PaperWorkerKillSwitch(active=True, source="fail_closed", since=None)


def _project_kill_switch(session: Session, settings: Settings) -> PaperWorkerKillSwitch:
    statement = select(KillSwitchState).where(KillSwitchState.active.is_(True))
    org_token = settings.watcher_paper_organization_id.strip()
    if org_token:
        statement = statement.where(KillSwitchState.organization_id == uuid.UUID(org_token))
    rows = list(session.scalars(statement).all())
    if not rows:
        return PaperWorkerKillSwitch()
    since_values = [row.activated_at for row in rows if row.activated_at is not None]
    since = min(since_values) if since_values else None
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    source = "organization"
    if len(rows) == 1:
        source = _recorded_kill_switch_source(session, rows[0].organization_id)
    return PaperWorkerKillSwitch(active=True, source=source, since=since)


def _recorded_kill_switch_source(session: Session, organization_id: uuid.UUID) -> str:
    from app.services.safety_alerts import kill_switch_source

    row = _kill_switch_posture_row(session, organization_id)
    if row is None or str(row.activation_state) != "active":
        return "organization"
    if str(row.worker_id) != str(organization_id):
        return "organization"
    token = str(row.last_scan_reason or "").strip().lower()
    if token and kill_switch_source(token) == token:
        return token
    return "organization"


def mark_kill_switch_alert_sent(
    session: Session,
    *,
    organization_id: str,
    version: int,
    now: datetime,
) -> None:
    """Record that this activation version reached Telegram.

    A newer version on the row is left alone. The write uses a new session
    on the same engine so the caller's committed transaction stays closed.
    """

    try:
        bind = session.get_bind()
        factory = sessionmaker(bind=bind, expire_on_commit=False)
        with factory() as fresh, fresh.begin():
            _apply_sent_marker(fresh, organization_id, version, now)
    except Exception:
        logger.error("kill_switch_alert_sent_unrecorded")


def reset_kill_switch_retry_window() -> None:
    """Forget the in-process retry limit. Tests call this between cases."""

    with _RETRY_LOCK:
        _RETRY_CLAIMED.clear()


def retry_unsent_kill_switch_alert(settings: Settings, *, now: datetime | None = None) -> bool:
    """Send committed kill-switch pages that never reached Telegram.

    Each organization has its own unsent slot. A failed send or a crash
    before the send leaves that marker. Worker boot calls this directly.
    ``GET /health/paper-worker`` schedules it after the response. A version
    is attempted at most once per 60 seconds, including a failed attempt.
    A version already sent is not sent again.
    """

    moment = now or datetime.now(UTC)
    try:
        pending = _load_unsent_kill_switch_alerts()
    except Exception:
        logger.error("kill_switch_alert_retry_failed")
        return False
    if not pending:
        return False
    try:
        from app.services.safety_alerts import telegram_safety_configured

        if not telegram_safety_configured(settings):
            return False
    except Exception:
        logger.error("kill_switch_alert_retry_failed")
        return False
    sent_any = False
    for source, organization_id, version in pending:
        if _deliver_unsent_kill_switch_alert(
            settings,
            source=source,
            organization_id=organization_id,
            version=version,
            now=moment,
        ):
            sent_any = True
    return sent_any


def _upsert_kill_switch_posture(
    session: Session,
    *,
    organization_id: uuid.UUID,
    token: str,
    active: bool,
    at: datetime,
    epoch: int,
    alert_state: str,
) -> None:
    """Insert or update one organization's slot. A concurrent first insert commits."""

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        dialect_insert = cast(Any, pg_insert)
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        dialect_insert = cast(Any, sqlite_insert)

    values: dict[str, object] = {
        "component": paper_kill_switch_component(organization_id),
        "worker_id": str(organization_id),
        "heartbeat_at": at,
        "activation_state": "active" if active else "clear",
        "lease_owner": "",
        "lease_epoch": epoch,
        "fence_held": False,
        "last_scan_reason": token,
        "market_source": "kill_switch",
        "telegram_runtime_state": "absent",
        "inbound_mode": "off",
        "outbox_pending": 0,
        "outbox_retryable": 0,
        "outbox_dead_letter": 0,
        "last_delivery_at": None,
        "last_error_code": alert_state,
        "kill_switch_active": False,
        "request_count": 0,
        "request_weight": 0,
        "rate_limited_count": 0,
        "cache_hits": 0,
    }
    statement = dialect_insert(ControlledRuntimeStatusRow).values(**values)
    assignments = {
        "worker_id": values["worker_id"],
        "heartbeat_at": values["heartbeat_at"],
        "activation_state": values["activation_state"],
        "lease_epoch": values["lease_epoch"],
        "last_scan_reason": values["last_scan_reason"],
        "last_error_code": values["last_error_code"],
        "market_source": values["market_source"],
    }
    if active:
        assignments["last_delivery_at"] = None
    session.execute(statement.on_conflict_do_update(index_elements=["component"], set_=assignments))
    session.flush()


def _kill_switch_posture_row(
    session: Session,
    organization_id: uuid.UUID,
) -> ControlledRuntimeStatusRow | None:
    per_org = session.get(
        ControlledRuntimeStatusRow,
        paper_kill_switch_component(organization_id),
    )
    if per_org is not None and str(per_org.worker_id) == str(organization_id):
        return per_org
    legacy = session.get(ControlledRuntimeStatusRow, PAPER_KILL_SWITCH_COMPONENT)
    if legacy is not None and str(legacy.worker_id) == str(organization_id):
        return legacy
    return None


def _claim_kill_switch_retry(organization_id: str, version: int, now: datetime) -> bool:
    moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    key = f"{organization_id}:{version}"
    with _RETRY_LOCK:
        previous = _RETRY_CLAIMED.get(key)
        if (
            previous is not None
            and (moment - previous).total_seconds() < _RETRY_MIN_INTERVAL_SECONDS
        ):
            return False
        _RETRY_CLAIMED[key] = moment
        return True


def _deliver_unsent_kill_switch_alert(
    settings: Settings,
    *,
    source: str,
    organization_id: str,
    version: int,
    now: datetime,
) -> bool:
    try:
        from app.services.safety_alerts import (
            build_kill_switch_alert,
            deliver_safety_alert,
            kill_switch_page_recently_sent,
        )

        key = f"kill_switch_activated:{source}:{organization_id}:{version}"
        if kill_switch_page_recently_sent(key, now):
            _mark_kill_switch_alert_sent(organization_id, version, now)
            return False
        if not _claim_kill_switch_retry(organization_id, version, now):
            return False
        alert = build_kill_switch_alert(
            source=source,
            organization_id=organization_id,
            dedup_key=key,
        )
        sent = deliver_safety_alert(settings, alert, now=now, failure_log="error")
    except Exception:
        logger.error("kill_switch_alert_retry_failed", source=source)
        return False
    if sent:
        _mark_kill_switch_alert_sent(organization_id, version, now)
    return sent


def _load_unsent_kill_switch_alerts() -> list[tuple[str, str, int]]:
    from app.db.session import get_session_factory

    factory = get_session_factory()
    pending: list[tuple[str, str, int]] = []
    with factory() as session:
        rows = session.scalars(
            select(ControlledRuntimeStatusRow).where(
                ControlledRuntimeStatusRow.market_source == "kill_switch",
                ControlledRuntimeStatusRow.activation_state == "active",
                ControlledRuntimeStatusRow.last_error_code == _ALERT_UNSENT,
            )
        ).all()
        for row in rows:
            version = int(row.lease_epoch or 0)
            organization_id = str(row.worker_id or "").strip()
            source = str(row.last_scan_reason or "").strip()
            if version < 1 or not organization_id or not source:
                continue
            try:
                organization = uuid.UUID(organization_id)
            except ValueError:
                continue
            state = session.scalar(
                select(KillSwitchState).where(KillSwitchState.organization_id == organization)
            )
            if state is None or not bool(state.active) or int(state.version) != version:
                continue
            pending.append((source, organization_id, version))
    return pending


def _apply_sent_marker(
    session: Session,
    organization_id: str,
    version: int,
    now: datetime,
) -> None:
    components = [PAPER_KILL_SWITCH_COMPONENT]
    with contextlib.suppress(ValueError):
        components.append(paper_kill_switch_component(uuid.UUID(organization_id)))
    rows = session.scalars(
        select(ControlledRuntimeStatusRow).where(
            ControlledRuntimeStatusRow.component.in_(components)
        )
    ).all()
    for row in rows:
        if str(row.worker_id) != organization_id:
            continue
        if int(row.lease_epoch) != version or str(row.last_error_code) == _ALERT_SENT:
            continue
        row.last_error_code = _ALERT_SENT
        row.last_delivery_at = now


def _mark_kill_switch_alert_sent(organization_id: str, version: int, now: datetime) -> None:
    from app.db.session import get_session_factory

    try:
        factory = get_session_factory()
        with factory() as session, session.begin():
            _apply_sent_marker(session, organization_id, version, now)
    except Exception:
        logger.error("kill_switch_alert_sent_unrecorded")
