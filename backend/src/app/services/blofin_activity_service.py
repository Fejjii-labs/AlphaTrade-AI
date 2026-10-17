"""Bounded, disarmed native history ingestion and authenticated stored reads."""

import base64
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import Engine, select, text, tuple_
from sqlalchemy.orm import Session

from app.core.blofin_readonly_access import get_readonly_client
from app.core.config import Settings
from app.core.errors import ExchangeDemoInactiveError, NotFoundError, ValidationAppError
from app.db.blofin_activity import BloFinActivityAccount, BloFinActivityCursor, BloFinActivityFact
from app.db.models import (
    AuditLog,
    ExecutionAccount,
    ExecutionCommand,
    TradePlanRevision,
    VenueSubmitEffect,
)
from app.providers.exchange.blofin_activity import ActivityIdentityError, BloFinActivityProvider
from app.providers.exchange.errors import ExchangeError, ExchangeRateLimitError
from app.repositories.blofin_activity import ActivityConflictError, BloFinActivityRepository
from app.schemas.blofin_activity import (
    ActivityCoverage,
    ActivityItem,
    ActivityPage,
    NativeActivityFact,
)
from app.services.blofin_activity_config import (
    ActivityScope,
    BloFinActivitySettings,
    configured_scope,
    credential_binding,
)
from app.services.canonical_serialization import canonical_sha256
from app.services.manual_demo_history import current_native_receipt

LIMITATIONS = [
    "history_retention_not_documented",
    "completed_normal_orders_only_no_pending_tpsl_algo_spot_or_copytrading",
    "funding_not_collected",
    "history_fee_currency_unavailable_unless_returned_explicitly",
    "instrument_metadata_is_observed_current_metadata_not_historical_conversion_proof",
    "exhausted_window_is_endpoint_coverage_not_complete_account_performance",
    "orders_reconciled_without_time_filter_retention_and_sweep_latency_not_guaranteed",
    "command_link_requires_verified_execution_uid_and_compatible_native_receipt",
]


class ActivityStoppedError(Exception):
    """Cooperative deadline/shutdown; the committed cursor remains resumable."""


@dataclass(frozen=True)
class ActivitySyncResult:
    status: str
    pages_committed: int = 0
    error_code: str | None = None


def activity_provider(settings: Settings) -> BloFinActivityProvider:
    # A slower, separately capped read-only client does not alter execution rate settings.
    safe = settings.model_copy(
        update={
            "blofin_rate_limit_requests_per_second": 1,
            "blofin_max_retries": min(settings.blofin_max_retries, 2),
            "blofin_request_timeout_seconds": min(settings.blofin_request_timeout_seconds, 10),
        }
    )
    return BloFinActivityProvider(get_readonly_client(safe, activity_history=True))


def _window(
    repo: BloFinActivityRepository,
    kind: Literal["order", "fill"],
    config: BloFinActivitySettings,
    now: datetime,
) -> BloFinActivityCursor:
    cursor = repo.cursor(kind)
    end = int(now.timestamp() * 1000)
    # Orders use recurring cursor sweeps: begin/end semantics are undocumented.
    # These bounds describe observation time, not a claimed order-event interval.
    floor = 0 if kind == "order" else max(0, end - config.lookback_days * 86400000)
    if cursor is None:
        cursor = BloFinActivityCursor(
            organization_id=repo.scope.organization_id,
            environment=repo.scope.environment,
            account_uid=repo.scope.account_uid,
            kind=kind,
            window_begin_ms=floor,
            window_end_ms=end,
            seen_cursors=[],
            window_complete=False,
            gap_detected=False,
        )
        repo.session.add(cursor)
        repo.session.flush()
    elif cursor.window_complete:
        begin = max(0, cursor.window_end_ms - config.overlap_seconds * 1000)
        cursor.gap_detected = cursor.gap_detected or floor > cursor.window_end_ms
        cursor.window_begin_ms = 0 if kind == "order" else max(begin, floor)
        cursor.window_end_ms = max(end, cursor.window_end_ms)
        cursor.native_cursor = None
        cursor.seen_cursors = []
        cursor.window_complete = False
    return cursor


def run_activity_sync(
    engine: Engine,
    settings: Settings,
    config: BloFinActivitySettings,
    *,
    provider: BloFinActivityProvider | None = None,
    shutdown: Callable[[], bool] = lambda: False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
) -> ActivitySyncResult:
    """One bounded worker tick. No scheduling, activation, submission or simulator IO.

    A session advisory lock serializes ticks for this organization/environment/UID.
    Every fact page and checkpoint commits together on a held PostgreSQL connection.
    """
    if not config.enabled:
        return ActivitySyncResult("disabled")
    scope = configured_scope(config)
    binding = credential_binding(settings)
    if engine.dialect.name != "postgresql":
        raise ValueError("Native activity synchronization requires PostgreSQL.")
    provider = provider or activity_provider(settings)
    deadline = monotonic() + config.budget_seconds

    def guard() -> None:
        if shutdown() or monotonic() >= deadline:
            raise ActivityStoppedError

    lock_id = int.from_bytes(hashlib.sha256(str(scope.key()).encode()).digest()[:8], "big") >> 1
    with engine.connect() as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id})
        connection.commit()
        if not acquired:
            return ActivitySyncResult("busy")
        try:
            with Session(bind=connection, expire_on_commit=False) as session:
                repo = BloFinActivityRepository(session, scope)
                pages = 0
                kind: Literal["order", "fill"] = "order"
                try:
                    guard()
                    cursors = [repo.cursor("order"), repo.cursor("fill")]
                    retries = [c.next_retry_at for c in cursors if c and c.next_retry_at]
                    if retries and max(retries) > clock():
                        return ActivitySyncResult("backoff")
                    provider.verify_identity(scope.account_uid, guard)
                    repo.verify_binding(binding, clock())
                    session.commit()
                    try:
                        metadata = provider.metadata(guard)
                    except ExchangeRateLimitError:
                        raise
                    except (ExchangeError, ExchangeDemoInactiveError):
                        metadata = {}  # Native units stay usable; conversion remains unknown.
                    # Alternate streams each tick so a long backfill cannot starve fills.
                    order = repo.cursor("order")
                    fill = repo.cursor("fill")
                    kinds: tuple[Literal["order", "fill"], ...] = (
                        ("fill", "order")
                        if order
                        and (
                            not fill
                            or (
                                order.last_attempt_at
                                and (
                                    not fill.last_attempt_at
                                    or order.last_attempt_at > fill.last_attempt_at
                                )
                            )
                        )
                        else ("order", "fill")
                    )
                    for index in range(config.max_pages):
                        kind = kinds[index % 2]
                        guard()
                        cursor = _window(repo, kind, config, clock())
                        # Window setup is durable even if the first page is interrupted.
                        session.commit()
                        facts = provider.page(
                            kind,
                            begin_ms=cursor.window_begin_ms,
                            end_ms=cursor.window_end_ms,
                            after=cursor.native_cursor,
                            limit=config.page_size,
                            expected_uid=scope.account_uid,
                            before_send=guard,
                        )
                        guard()
                        repo.persist_page(cursor, facts, metadata, clock())
                        session.commit()
                        pages += 1
                    return ActivitySyncResult("bounded", pages)
                except ActivityStoppedError:
                    session.rollback()
                    return ActivitySyncResult("interrupted", pages)
                except (ExchangeError, ExchangeDemoInactiveError, ActivityConflictError) as exc:
                    session.rollback()
                    code = (
                        "identity_unverified"
                        if isinstance(exc, ActivityIdentityError)
                        else "native_identity_conflict"
                        if isinstance(exc, ActivityConflictError)
                        else "rate_limited"
                        if isinstance(exc, ExchangeRateLimitError)
                        else "provider_unavailable"
                    )
                    now = clock()
                    account = session.get(BloFinActivityAccount, scope.key())
                    if account is None:
                        account = BloFinActivityAccount(
                            organization_id=scope.organization_id,
                            environment=scope.environment,
                            account_uid=scope.account_uid,
                            credential_binding=binding,
                            identity_verified_at=None,
                            identity_error=code,
                        )
                        session.add(account)
                        session.flush()
                    elif code == "identity_unverified":
                        account.identity_error = code
                    cursor = _window(repo, kind, config, now)
                    cursor.last_attempt_at = now
                    cursor.last_error_code = code
                    cursor.next_retry_at = now + timedelta(
                        seconds=300 if code == "rate_limited" else 60
                    )
                    session.commit()
                    return ActivitySyncResult("failed", pages, code)
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
            connection.commit()


def _command_link(
    session: Session, repo: BloFinActivityRepository, fact: NativeActivityFact
) -> UUID | None:
    order = fact
    account = session.get(BloFinActivityAccount, repo.scope.key())
    if account is None or account.identity_verified_at is None or account.identity_error:
        return None
    if fact.kind == "fill":
        row = session.get(BloFinActivityFact, (*repo.scope.key(), "order", fact.order_id))
        if row is None:
            return None
        order = NativeActivityFact.model_validate(row.payload)
    if not order.client_order_id:
        return None
    candidates = list(
        session.scalars(
            select(ExecutionCommand)
            .join(VenueSubmitEffect, VenueSubmitEffect.command_id == ExecutionCommand.id)
            .join(TradePlanRevision, TradePlanRevision.id == ExecutionCommand.revision_id)
            .join(ExecutionAccount, ExecutionAccount.id == ExecutionCommand.account_id)
            .where(
                ExecutionCommand.organization_id == repo.scope.organization_id,
                TradePlanRevision.organization_id == repo.scope.organization_id,
                TradePlanRevision.account_id == ExecutionCommand.account_id,
                TradePlanRevision.content_hash == ExecutionCommand.plan_content_hash,
                TradePlanRevision.user_id == ExecutionCommand.user_id,
                ExecutionAccount.organization_id == ExecutionCommand.organization_id,
                ExecutionAccount.user_id == ExecutionCommand.user_id,
                TradePlanRevision.execution_venue == "BLOFIN_DEMO",
                TradePlanRevision.execution_instrument == order.instrument,
                TradePlanRevision.semantic_payload["side"].as_string() == order.side.upper(),
                VenueSubmitEffect.client_order_id == order.client_order_id,
            )
            .limit(2)
        )
    )
    matches = []
    for command in candidates:
        # Evidence captured before dispatch binds the command's original execution
        # connection. A later reconciliation with replacement keys cannot assign it.
        attestations = list(
            session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.organization_id == command.organization_id,
                    AuditLog.user_id == command.user_id,
                    AuditLog.resource_type == "manual_demo_test",
                    AuditLog.resource_id == str(command.id),
                    AuditLog.redacted_metadata["operation"].as_string()
                    == "manual_demo_execution_account_verified",
                )
                .limit(2)
            )
        )
        if len(attestations) != 1:
            continue
        identity = dict(attestations[0].redacted_metadata)
        identity_digest = identity.pop("evidence_hash", None)
        identity.pop("operation", None)
        if (
            canonical_sha256(identity) != identity_digest
            or identity.get("plan_content_hash") != command.plan_content_hash
            or identity.get("environment") != repo.scope.environment
            or identity.get("native_account_uid") != repo.scope.account_uid
            or identity.get("execution_account_id") != str(command.account_id)
        ):
            continue
        receipt = current_native_receipt(session, command)
        if receipt is None:
            continue
        proof = dict(receipt.redacted_metadata)
        digest = proof.pop("receipt_hash", None)
        proof.pop("operation", None)
        if (
            canonical_sha256(proof) == digest
            and proof.get("plan_content_hash") == command.plan_content_hash
            and proof.get("environment") == repo.scope.environment
            and proof.get("native_account_uid") == repo.scope.account_uid
            and proof.get("execution_account_id") == str(command.account_id)
            and proof.get("venue_order_id") == order.order_id
            and proof.get("client_order_id") == order.client_order_id
            and proof.get("instrument") == order.instrument
        ):
            matches.append(command.id)
    if len(matches) > 1:
        raise ActivityConflictError("Ambiguous AlphaTrade native command linkage.")
    return matches[0] if matches else None


def _scope_digest(scope: ActivityScope) -> str:
    return hashlib.sha256(str(scope.key()).encode()).hexdigest()


def _encode_cursor(scope: ActivityScope, kind: str, at: int, native_id: str) -> str:
    return base64.urlsafe_b64encode(
        json.dumps([_scope_digest(scope), kind, at, native_id], separators=(",", ":")).encode()
    ).decode()


def _decode_cursor(scope: ActivityScope, kind: str, value: str) -> tuple[int, str]:
    try:
        if len(value) > 512:
            raise ValueError
        data = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if (
            not isinstance(data, list)
            or len(data) != 4
            or data[:2] != [_scope_digest(scope), kind]
            or type(data[2]) is not int
            or not 0 <= data[2] <= 253402300799999
            or not isinstance(data[3], str)
            or not 0 < len(data[3]) <= 128
        ):
            raise ValueError
        return data[2], data[3]
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise ValidationAppError(
            "Activity cursor does not belong to this account and stream."
        ) from exc


def read_activity(
    session: Session,
    *,
    scope: ActivityScope,
    binding: str,
    kind: Literal["order", "fill"],
    limit: int = 50,
    cursor: str | None = None,
    stale_seconds: int = 300,
    now: datetime | None = None,
) -> ActivityPage:
    if not 1 <= limit <= 100:
        raise ValidationAppError("Activity page size must be between 1 and 100.")
    now = now or datetime.now(UTC)
    repo = BloFinActivityRepository(session, scope)
    position = _decode_cursor(scope, kind, cursor) if cursor else None
    account = session.get(BloFinActivityAccount, scope.key())
    if (
        not account
        or account.credential_binding != binding
        or account.identity_error
        or account.identity_verified_at is None
    ):
        return ActivityPage(
            organization_id=scope.organization_id,
            account_uid=scope.account_uid,
            identity_status="unverified",
            identity_error_code=(
                account.identity_error
                if account and account.credential_binding == binding
                else "credentials_not_verified"
            ),
            items=[],
            coverage=[],
            freshness="unverified",
            limitations=LIMITATIONS,
            generated_at=now,
        )
    query = select(BloFinActivityFact).where(
        *repo.filters(BloFinActivityFact), BloFinActivityFact.kind == kind
    )
    if position:
        query = query.where(
            tuple_(BloFinActivityFact.occurred_at_ms, BloFinActivityFact.native_id) < position
        )
    rows = list(
        session.scalars(
            query.order_by(
                BloFinActivityFact.occurred_at_ms.desc(), BloFinActivityFact.native_id.desc()
            ).limit(limit + 1)
        )
    )
    items = []
    for row in rows[:limit]:
        fact = NativeActivityFact.model_validate(row.payload)
        link = _command_link(session, repo, fact)
        items.append(
            ActivityItem(
                **fact.model_dump(),
                origin="alphatrade_matched" if link else "native",
                command_id=link,
                **(row.instrument_metadata or {}),
                metadata_observed_at=row.metadata_observed_at,
            )
        )
    coverage = []
    for stream in ("order", "fill"):
        checkpoint = repo.cursor(stream)
        coverage.append(
            ActivityCoverage(
                kind=stream, selection="cursor_sweep" if stream == "order" else "time_window"
            )
            if checkpoint is None
            else ActivityCoverage(
                kind=stream,
                selection="cursor_sweep" if stream == "order" else "time_window",
                window_begin_ms=str(checkpoint.window_begin_ms) if stream == "fill" else None,
                window_end_ms=str(checkpoint.window_end_ms) if stream == "fill" else None,
                native_cursor=checkpoint.native_cursor,
                window_complete=checkpoint.window_complete,
                covered_begin_ms=str(checkpoint.covered_begin_ms)
                if checkpoint.covered_begin_ms is not None and stream == "fill"
                else None,
                covered_end_ms=str(checkpoint.covered_end_ms)
                if checkpoint.covered_end_ms is not None and stream == "fill"
                else None,
                gap_detected=checkpoint.gap_detected,
                last_successful_sync=checkpoint.last_successful_sync,
                last_attempt_at=checkpoint.last_attempt_at,
                last_error_code=checkpoint.last_error_code,
                next_retry_at=checkpoint.next_retry_at,
            )
        )
    successes = [c.last_successful_sync for c in coverage]
    freshness: Literal["fresh", "stale", "never_synced", "unverified"] = "never_synced"
    if any(successes):
        freshness = "stale"
        if all(
            s is not None and 0 <= (now - s).total_seconds() < stale_seconds for s in successes
        ) and not any(
            c.last_error_code
            or (
                c.kind == "fill"
                and (
                    c.window_end_ms is None
                    or not 0
                    <= int(now.timestamp() * 1000) - int(c.window_end_ms)
                    < stale_seconds * 1000
                )
            )
            for c in coverage
        ):
            freshness = "fresh"
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = _encode_cursor(scope, kind, last.occurred_at_ms, last.native_id)
    return ActivityPage(
        organization_id=scope.organization_id,
        account_uid=scope.account_uid,
        identity_status="verified",
        identity_verified_at=account.identity_verified_at,
        items=items,
        next_cursor=next_cursor,
        coverage=coverage,
        freshness=freshness,
        limitations=LIMITATIONS,
        generated_at=now,
    )


def require_organization(scope: ActivityScope, organization_id: UUID) -> None:
    if scope.organization_id != organization_id:
        raise NotFoundError("BloFin activity connection not found.")
