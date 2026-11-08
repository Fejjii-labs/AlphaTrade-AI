"""TradingView signal intake and lifecycle (AT-037).

Secure webhook intake with signature/replay protection, idempotent storage,
validation, and optional paper-validation candidate creation. Never creates
live orders or executable proposals.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Environment, Settings, get_settings
from app.core.errors import AuthError, NotFoundError, ServiceUnavailableError, ValidationAppError
from app.db.models import Organization, SetupDefinition, UserStrategy, UserStrategyVersion
from app.db.models import PaperValidationAlert as AlertModel
from app.db.models import PaperValidationCandidate as CandidateModel
from app.db.models import PaperValidationDraft as DraftModel
from app.db.models import TradingViewSignal as SignalModel
from app.guardrails.redaction import redact_value
from app.repositories.paper_validation_candidate import PaperValidationCandidateRepository
from app.repositories.paper_validation_draft import PaperValidationDraftRepository
from app.repositories.tradingview_signal import TradingViewSignalRepository
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AlertDeliveryChannel,
    AlertDeliveryStatus,
    AuditEventType,
    AuditResult,
    AuditSeverity,
    PaperAlertSeverity,
    PaperAlertType,
    PaperValidationCandidateStatus,
    PaperValidationDraftPrepStatus,
    PaperValidationDraftRiskMode,
    PaperValidationDraftStatus,
    PromotionSource,
    SetupAlertReviewStatus,
    TradingViewSignalStatus,
)
from app.schemas.paper_validation_draft import PaperValidationDraftChecklist
from app.schemas.tradingview_signal import (
    CREATE_TRADINGVIEW_CANDIDATE_CONFIRM,
    TradingViewSignalCreateCandidateRequest,
    TradingViewSignalCreateCandidateResult,
    TradingViewSignalIntakeResult,
    TradingViewSignalItem,
    TradingViewSignalLinks,
    TradingViewSignalListResponse,
    TradingViewSignalWebhookPayload,
)
from app.security.tradingview_webhook import verify_tradingview_signature
from app.services.audit_service import AuditService

logger = structlog.get_logger(__name__)

_COMPLETE_CHECKLIST = PaperValidationDraftChecklist(
    trend_checked=True,
    support_resistance_checked=True,
    volume_checked=True,
    risk_reward_checked=True,
    invalidation_checked=True,
    higher_timeframe_checked=True,
    news_or_funding_checked=True,
)

_ADVISORY_NOTE = "Advisory TradingView intake only. Never creates live orders."


class TradingViewSignalService:
    """Inbound TradingView webhook processing and inbox queries."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._signals = TradingViewSignalRepository(session)
        self._drafts = PaperValidationDraftRepository(session)
        self._candidates = PaperValidationCandidateRepository(session)
        self._audit = AuditService(session)

    def _require_bound_organization(self, organization_id: uuid.UUID) -> None:
        """Accept only the org the global secret is bound to; one generic refusal.

        Unknown, unbound and mismatched orgs share the same error so the
        endpoint is not an organization-existence oracle (audit M16).
        """
        refusal = ValidationAppError(
            "TradingView webhook organization is not accepted.",
            code="tradingview_webhook_organization_refused",
        )
        bound = self._settings.tradingview_webhook_organization_id.strip()
        if bound:
            try:
                bound_id = uuid.UUID(bound)
            except ValueError:
                raise refusal from None
            if organization_id != bound_id:
                raise refusal
        elif self._settings.environment is not Environment.LOCAL:
            raise refusal
        if self._session.get(Organization, organization_id) is None:
            raise refusal

    def intake_webhook(
        self,
        raw_body: bytes,
        *,
        signature_header: str | None,
        timestamp_header: str | None,
        request_id: str | None = None,
    ) -> TradingViewSignalIntakeResult:
        if not self._settings.tradingview_webhook_enabled:
            raise ServiceUnavailableError(
                "TradingView webhook intake is disabled.",
                code="tradingview_webhook_disabled",
            )
        if not verify_tradingview_signature(
            raw_body,
            signature_header=signature_header,
            timestamp_header=timestamp_header,
            secret=self._settings.tradingview_webhook_secret,
            max_skew_seconds=self._settings.tradingview_webhook_max_skew_seconds,
        ):
            logger.warning("tradingview_webhook_invalid_signature")
            raise AuthError("Invalid TradingView webhook signature.")

        try:
            raw_json = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationAppError("Invalid TradingView webhook JSON.") from exc
        if not isinstance(raw_json, dict):
            raise ValidationAppError("TradingView webhook body must be a JSON object.")

        try:
            payload = TradingViewSignalWebhookPayload.model_validate(raw_json)
        except ValidationError as exc:
            raise ValidationAppError(
                "TradingView webhook payload failed validation.",
                details={"errors": _serialize_pydantic_errors(exc)},
            ) from exc

        self._require_bound_organization(payload.organization_id)

        assert payload.idempotency_key is not None
        payload_hash = hashlib.sha256(raw_body).hexdigest()
        existing = self._signals.get_by_idempotency(
            organization_id=payload.organization_id,
            idempotency_key=payload.idempotency_key,
        )
        if existing is None:
            existing = self._signals.get_by_alert_id(
                organization_id=payload.organization_id,
                external_alert_id=payload.alert_id,
            )
        if existing is not None:
            if existing.payload_hash != payload_hash:
                self._record_audit(
                    AuditEventType.TRADINGVIEW_SIGNAL_REJECTED,
                    organization_id=payload.organization_id,
                    resource_id=str(existing.id),
                    request_id=request_id,
                    metadata={
                        "reason": "idempotency_payload_mismatch",
                        "alert_id": payload.alert_id,
                    },
                    result=AuditResult.FAILURE,
                )
                raise ValidationAppError(
                    "Idempotency key or alert_id reused with a different payload.",
                    details={
                        "signal_id": str(existing.id),
                        "idempotency_key": payload.idempotency_key,
                    },
                )
            self._record_audit(
                AuditEventType.TRADINGVIEW_SIGNAL_RECEIVED,
                organization_id=payload.organization_id,
                resource_id=str(existing.id),
                request_id=request_id,
                metadata={
                    "action": "duplicate_converged",
                    "alert_id": payload.alert_id,
                    "status": existing.status,
                },
            )
            return TradingViewSignalIntakeResult(
                signal=self._to_item(existing),
                already_exists=True,
                duplicate=True,
            )

        redacted = redact_value(raw_json)
        if not isinstance(redacted, dict):
            redacted = {}
        now = datetime.now(UTC)
        linkage_errors, safe_strategy_id, safe_version_id = self._resolve_strategy_links(
            organization_id=payload.organization_id,
            strategy_id=payload.strategy_id,
            strategy_version_id=payload.strategy_version_id,
        )
        # Only persist FKs that exist to avoid integrity failures on reject paths.
        row = SignalModel(
            organization_id=payload.organization_id,
            external_alert_id=payload.alert_id,
            idempotency_key=payload.idempotency_key,
            status=TradingViewSignalStatus.RECEIVED.value,
            symbol=payload.symbol.upper(),
            timeframe=payload.timeframe,
            direction=payload.direction,
            setup_name=payload.setup_name,
            setup_version=payload.setup_version,
            strategy_id=safe_strategy_id,
            strategy_version_id=safe_version_id,
            trigger_level=payload.trigger_level,
            invalidation_level=payload.invalidation_level,
            take_profit_level=payload.take_profit_level,
            stop_loss_level=payload.stop_loss_level,
            confidence=payload.confidence,
            source_metadata=payload.source,
            raw_payload_redacted=redacted,
            payload_hash=payload_hash,
            received_at=now,
            occurred_at=payload.occurred_at,
            backtest_run_id=None,
            journal_trade_id=None,
        )
        try:
            with self._session.begin_nested():
                self._signals.add(row)
        except IntegrityError:
            # A concurrent signed retry may win either organization-scoped unique key.
            # Recover only a proven duplicate; unrelated FK/constraint failures still fail.
            winner = self._signals.get_by_idempotency(
                organization_id=payload.organization_id, idempotency_key=payload.idempotency_key
            ) or self._signals.get_by_alert_id(
                organization_id=payload.organization_id, external_alert_id=payload.alert_id
            )
            if winner is None:
                raise
            return self.intake_webhook(
                raw_body,
                signature_header=signature_header,
                timestamp_header=timestamp_header,
                request_id=request_id,
            )
        self._record_audit(
            AuditEventType.TRADINGVIEW_SIGNAL_RECEIVED,
            organization_id=payload.organization_id,
            resource_id=str(row.id),
            request_id=request_id,
            metadata={
                "action": "received",
                "alert_id": payload.alert_id,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "direction": row.direction,
            },
        )
        self._validate_and_update(row, precomputed_errors=linkage_errors)
        if (
            self._settings.tradingview_auto_create_candidate
            and row.status == TradingViewSignalStatus.VALIDATED.value
        ):
            self._create_candidate_internal(
                row,
                organization_id=payload.organization_id,
                user_id=None,
                request_id=request_id,
            )
        return TradingViewSignalIntakeResult(
            signal=self._to_item(row),
            already_exists=False,
            duplicate=False,
        )

    def list_signals(
        self,
        *,
        organization_id: uuid.UUID,
        status: TradingViewSignalStatus | None = None,
        symbol: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> TradingViewSignalListResponse:
        rows, total = self._signals.list_for_org(
            organization_id=organization_id,
            status=status.value if status is not None else None,
            symbol=symbol.upper() if symbol else None,
            limit=min(max(limit, 1), 100),
            offset=max(offset, 0),
        )
        return TradingViewSignalListResponse(
            items=[self._to_item(row) for row in rows],
            total=total,
            limit=min(max(limit, 1), 100),
            offset=max(offset, 0),
        )

    def get_signal(
        self,
        signal_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> TradingViewSignalItem:
        row = self._signals.get_for_org(signal_id, organization_id=organization_id)
        if row is None:
            raise NotFoundError("TradingView signal not found.")
        return self._to_item(row)

    def create_candidate(
        self,
        signal_id: uuid.UUID,
        payload: TradingViewSignalCreateCandidateRequest,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> TradingViewSignalCreateCandidateResult:
        if payload.confirm != CREATE_TRADINGVIEW_CANDIDATE_CONFIRM:
            raise ValidationAppError(
                "Exact confirmation required to create a paper validation candidate.",
                details={"required_confirm": CREATE_TRADINGVIEW_CANDIDATE_CONFIRM},
            )
        row = self._signals.get_for_org(signal_id, organization_id=organization_id, for_update=True)
        if row is None:
            raise NotFoundError("TradingView signal not found.")
        if row.candidate_id is not None:
            candidate = self._candidates.get_for_org(
                row.candidate_id, organization_id=organization_id
            )
            if candidate is not None:
                return TradingViewSignalCreateCandidateResult(
                    signal=self._to_item(row),
                    candidate_id=candidate.id,
                    draft_id=row.draft_id or candidate.draft_id,
                    source_alert_id=row.source_alert_id or candidate.source_alert_id,
                    already_exists=True,
                )
        if row.status not in {
            TradingViewSignalStatus.VALIDATED.value,
            TradingViewSignalStatus.CANDIDATE_CREATED.value,
        }:
            raise ValidationAppError(
                "Only validated TradingView signals can create paper candidates.",
                details={"status": row.status, "rejection_reason": row.rejection_reason},
            )
        candidate = self._create_candidate_internal(
            row,
            organization_id=organization_id,
            user_id=user_id,
            request_id=request_id,
        )
        assert row.draft_id is not None
        assert row.source_alert_id is not None
        return TradingViewSignalCreateCandidateResult(
            signal=self._to_item(row),
            candidate_id=candidate.id,
            draft_id=row.draft_id,
            source_alert_id=row.source_alert_id,
            already_exists=False,
        )

    def _resolve_strategy_links(
        self,
        *,
        organization_id: uuid.UUID,
        strategy_id: uuid.UUID | None,
        strategy_version_id: uuid.UUID | None,
    ) -> tuple[list[str], uuid.UUID | None, uuid.UUID | None]:
        """Validate strategy FKs and return only persistable ids."""
        errors: list[str] = []
        safe_strategy_id: uuid.UUID | None = None
        safe_version_id: uuid.UUID | None = None

        if strategy_id is not None:
            strategy = self._session.get(UserStrategy, strategy_id)
            if strategy is None or strategy.organization_id != organization_id:
                errors.append("strategy_id does not belong to this organization.")
            else:
                safe_strategy_id = strategy_id

        if strategy_version_id is not None:
            if safe_strategy_id is None and strategy_id is None:
                errors.append("strategy_version_id requires strategy_id.")
            else:
                version = self._session.get(UserStrategyVersion, strategy_version_id)
                if version is None:
                    errors.append("strategy_version_id not found.")
                elif safe_strategy_id is not None and version.strategy_id != safe_strategy_id:
                    errors.append("strategy_version_id does not match strategy_id.")
                elif safe_strategy_id is None:
                    errors.append("strategy_version_id requires a valid strategy_id.")
                else:
                    safe_version_id = strategy_version_id

        return errors, safe_strategy_id, safe_version_id

    def _validate_and_update(
        self,
        row: SignalModel,
        *,
        precomputed_errors: list[str] | None = None,
    ) -> None:
        errors: list[str] = list(precomputed_errors or [])
        if row.setup_name is not None and row.setup_version is not None:
            setup = self._session.scalars(
                select(SetupDefinition).where(
                    SetupDefinition.name == row.setup_name,
                    SetupDefinition.version == row.setup_version,
                )
            ).first()
            if setup is not None:
                row.setup_definition_id = setup.id
        if row.direction not in {"long", "short"}:
            errors.append("direction must be long or short.")
        if row.confidence is not None and not (0.0 <= row.confidence <= 1.0):
            errors.append("confidence must be between 0 and 1.")

        if errors:
            row.status = TradingViewSignalStatus.REJECTED.value
            row.validation_errors = errors
            row.rejection_reason = errors[0]
            self._record_audit(
                AuditEventType.TRADINGVIEW_SIGNAL_REJECTED,
                organization_id=row.organization_id,
                resource_id=str(row.id),
                metadata={"errors": errors[:10]},
                result=AuditResult.FAILURE,
            )
            return

        row.status = TradingViewSignalStatus.VALIDATED.value
        row.validated_at = datetime.now(UTC)
        row.validation_errors = None
        row.rejection_reason = None

    def _create_candidate_internal(
        self,
        row: SignalModel,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        request_id: str | None,
    ) -> CandidateModel:
        if row.candidate_id is not None:
            existing = self._candidates.get_for_org(
                row.candidate_id, organization_id=organization_id
            )
            if existing is not None:
                return existing

        alert = AlertModel(
            organization_id=organization_id,
            user_id=user_id,
            alert_type=PaperAlertType.TRADINGVIEW_SIGNAL,
            severity=PaperAlertSeverity.INFO,
            strategy_id=row.strategy_id,
            message=(
                f"TradingView signal {row.external_alert_id}: "
                f"{row.direction} {row.symbol} {row.timeframe}."
            ),
            dedup_key=f"tradingview_signal:{row.id}",
            metadata_json={
                "source": "tradingview",
                "tradingview_signal_id": str(row.id),
                "alert_id": row.external_alert_id,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "direction": row.direction,
                "setup_name": row.setup_name,
                "setup_version": row.setup_version,
                "condition": "tradingview_signal",
            },
            delivery_status=AlertDeliveryStatus.DISABLED,
            delivery_channel=AlertDeliveryChannel.IN_APP,
            review_status=SetupAlertReviewStatus.IMPORTANT.value,
        )
        self._session.add(alert)
        self._session.flush()

        thesis = (
            f"TradingView alert {row.external_alert_id} for {row.symbol} "
            f"{row.timeframe} ({row.direction}). Paper validation only."
        )
        entry = "Validate the TradingView setup levels and thesis in paper mode."
        invalidation = (
            "Invalidate if price closes beyond the provided invalidation/stop "
            "level or if data freshness degrades."
        )
        risk_notes = (
            "TradingView-origin candidate. Paper-only. Does not authorize real "
            "trading or place exchange orders."
        )
        draft = DraftModel(
            organization_id=organization_id,
            source_alert_id=alert.id,
            symbol=row.symbol,
            timeframe=row.timeframe,
            condition="tradingview_signal",
            direction=row.direction,
            confidence=row.confidence,
            reason=thesis,
            trigger_level=row.trigger_level,
            invalidation_level=row.invalidation_level or row.stop_loss_level,
            review_status=SetupAlertReviewStatus.IMPORTANT.value,
            risk_mode=PaperValidationDraftRiskMode.CONSERVATIVE.value,
            status=PaperValidationDraftStatus.DRAFT.value,
            created_by=user_id,
            thesis=thesis,
            entry_criteria=entry,
            invalidation_criteria=invalidation,
            risk_notes=risk_notes,
            checklist_status=_COMPLETE_CHECKLIST.model_dump(),
            prep_status=PaperValidationDraftPrepStatus.READY_FOR_VALIDATION.value,
        )
        self._drafts.add(draft)

        snapshot: dict[str, Any] = {
            "tradingview_signal_id": str(row.id),
            "external_alert_id": row.external_alert_id,
            "setup_name": row.setup_name,
            "setup_version": row.setup_version,
            "take_profit_level": row.take_profit_level,
            "stop_loss_level": row.stop_loss_level,
            "source_metadata": row.source_metadata,
        }
        candidate = CandidateModel(
            organization_id=organization_id,
            draft_id=draft.id,
            source_alert_id=alert.id,
            symbol=row.symbol,
            timeframe=row.timeframe,
            condition="tradingview_signal",
            direction=row.direction,
            confidence=row.confidence,
            trigger_level=row.trigger_level,
            invalidation_level=row.invalidation_level or row.stop_loss_level,
            thesis=thesis,
            entry_criteria=entry,
            invalidation_criteria=invalidation,
            risk_notes=risk_notes,
            checklist_snapshot=_COMPLETE_CHECKLIST.model_dump(),
            risk_mode=PaperValidationDraftRiskMode.CONSERVATIVE.value,
            candidate_status=PaperValidationCandidateStatus.QUEUED.value,
            created_by=user_id,
            promotion_source=PromotionSource.TRADINGVIEW_SIGNAL.value,
            strategy_id=row.strategy_id,
            strategy_version_id=row.strategy_version_id,
            backtest_run_id=row.backtest_run_id,
            evidence_snapshot=snapshot,
        )
        self._candidates.add(candidate)

        row.source_alert_id = alert.id
        row.draft_id = draft.id
        row.candidate_id = candidate.id
        row.status = TradingViewSignalStatus.CANDIDATE_CREATED.value
        self._session.flush()

        self._record_audit(
            AuditEventType.TRADINGVIEW_SIGNAL_CANDIDATE_CREATED,
            organization_id=organization_id,
            user_id=user_id,
            resource_id=str(row.id),
            request_id=request_id,
            metadata={
                "candidate_id": str(candidate.id),
                "draft_id": str(draft.id),
                "source_alert_id": str(alert.id),
            },
        )
        return candidate

    def _to_item(self, row: SignalModel) -> TradingViewSignalItem:
        links = TradingViewSignalLinks(
            setup_definition_id=row.setup_definition_id,
            strategy_id=row.strategy_id,
            strategy_version_id=row.strategy_version_id,
            source_alert_id=row.source_alert_id,
            draft_id=row.draft_id,
            candidate_id=row.candidate_id,
            journal_trade_id=row.journal_trade_id,
            backtest_run_id=row.backtest_run_id,
            paper_candidate_path=(
                f"/paper-validation/candidates/{row.candidate_id}"
                if row.candidate_id is not None
                else None
            ),
            strategy_path=(
                f"/strategy-lab?strategy_id={row.strategy_id}"
                if row.strategy_id is not None
                else None
            ),
            journal_path=(
                f"/journal?trade_id={row.journal_trade_id}"
                if row.journal_trade_id is not None
                else None
            ),
        )
        status = (
            row.status
            if isinstance(row.status, TradingViewSignalStatus)
            else TradingViewSignalStatus(str(row.status))
        )
        errors = None
        if isinstance(row.validation_errors, list):
            errors = [str(item) for item in row.validation_errors]
        return TradingViewSignalItem(
            id=row.id,
            organization_id=row.organization_id,
            external_alert_id=row.external_alert_id,
            idempotency_key=row.idempotency_key,
            status=status,
            symbol=row.symbol,
            timeframe=row.timeframe,
            direction=row.direction,
            setup_name=row.setup_name,
            setup_version=row.setup_version,
            setup_definition_id=row.setup_definition_id,
            strategy_id=row.strategy_id,
            strategy_version_id=row.strategy_version_id,
            trigger_level=row.trigger_level,
            invalidation_level=row.invalidation_level,
            take_profit_level=row.take_profit_level,
            stop_loss_level=row.stop_loss_level,
            confidence=row.confidence,
            source_metadata=row.source_metadata if isinstance(row.source_metadata, dict) else None,
            validation_errors=errors,
            rejection_reason=row.rejection_reason,
            received_at=row.received_at,
            validated_at=row.validated_at,
            occurred_at=row.occurred_at,
            duplicate_of_signal_id=row.duplicate_of_signal_id,
            links=links,
            note=_ADVISORY_NOTE,
        )

    def _record_audit(
        self,
        event_type: AuditEventType,
        *,
        organization_id: uuid.UUID,
        resource_id: str,
        request_id: str | None = None,
        user_id: uuid.UUID | None = None,
        metadata: dict[str, object] | None = None,
        result: AuditResult = AuditResult.SUCCESS,
    ) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id=request_id or f"tradingview-{resource_id}",
                trace_id=str(uuid.uuid4()),
                user_id=user_id,
                organization_id=organization_id,
                event_type=event_type,
                resource_type="tradingview_signal",
                resource_id=resource_id,
                actor_type=ActorType.SYSTEM if user_id is None else ActorType.USER,
                result=result,
                severity=AuditSeverity.INFO,
                metadata=metadata or {},
            )
        )


def _serialize_pydantic_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """Convert Pydantic errors into JSON-safe dicts for API details."""
    out: list[dict[str, Any]] = []
    for item in exc.errors(include_url=False)[:20]:
        out.append(
            {
                "type": str(item.get("type", "")),
                "loc": [str(part) for part in item.get("loc", ())],
                "msg": str(item.get("msg", "")),
            }
        )
    return out
