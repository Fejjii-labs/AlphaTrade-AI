"""Organization quota configuration and enforcement."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import OrganizationQuota as OrganizationQuotaModel
from app.db.voice_realtime import VoiceSessionReservation
from app.repositories.quota import QuotaRepository
from app.repositories.usage import UsageRepository
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import AuditEventType, AuditResult, AuditSeverity, CostSource
from app.schemas.usage import (
    OrganizationQuotaConfig,
    OrganizationQuotaUpdate,
    QuotaStatus,
    QuotaUsageSnapshot,
)
from app.services.audit_service import AuditService
from app.services.usage_service import day_start, month_start

logger = structlog.get_logger(__name__)
_ORGANIZATION_QUOTA_CONSTRAINT = "uq_organization_quotas_organization_id"

FEATURE_LIMIT_FIELDS: dict[str, str] = {
    "agent_chat": "limit_agent_chat",
    "rag_ingest": "limit_rag_ingest",
    "market_analyze": "limit_market_analyze",
    "agent_narrative": "limit_agent_narrative",
    "paper_execution": "limit_paper_execution",
}


@dataclass(frozen=True)
class QuotaCheckResult:
    allowed: bool
    soft_warning: bool
    hard_blocked: bool
    message: str
    feature: str
    warnings: tuple[str, ...] = ()


def _is_organization_quota_unique_violation(exc: IntegrityError) -> bool:
    """Return whether ``exc`` targets the authoritative per-organization quota key."""
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    diag = getattr(orig, "diag", None)
    if diag is not None:
        constraint = getattr(diag, "constraint_name", None)
        if constraint == _ORGANIZATION_QUOTA_CONSTRAINT:
            return True
    message = str(orig).lower()
    return (
        _ORGANIZATION_QUOTA_CONSTRAINT in message
        or "organization_quotas.organization_id" in message
    )


class QuotaService:
    """Manage organization quotas and enforce limits."""

    def __init__(
        self,
        session: Session,
        *,
        audit_service: AuditService | None = None,
    ) -> None:
        self._session = session
        self._quotas = QuotaRepository(session)
        self._usage = UsageRepository(session)
        self._audit = audit_service or AuditService(session)

    def get_or_create_quota(self, organization_id: uuid.UUID) -> OrganizationQuotaConfig:
        row = self._quotas.get_by_organization(organization_id)
        if row is not None:
            return _to_config(row)

        row = OrganizationQuotaModel(organization_id=organization_id)
        if self._session.get_bind().dialect.name == "sqlite":
            # SQLite may commit when releasing a SAVEPOINT that performed the
            # transaction's first write. Preserve the existing flush-only UoW;
            # PostgreSQL is the authoritative concurrent deployment target.
            self._quotas.add(row)
            return _to_config(row)

        nested = self._session.begin_nested()
        try:
            self._quotas.add(row)
        except IntegrityError as exc:
            nested.rollback()
            if not _is_organization_quota_unique_violation(exc):
                raise
            row = self._quotas.get_by_organization(organization_id)
            if row is None:
                raise
        else:
            nested.commit()
        return _to_config(row)

    def update_quota(
        self,
        organization_id: uuid.UUID,
        patch: OrganizationQuotaUpdate,
        *,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> OrganizationQuotaConfig:
        row = self._quotas.get_by_organization(organization_id)
        if row is None:
            row = OrganizationQuotaModel(organization_id=organization_id)
            self._quotas.add(row)

        changes: dict[str, object] = {}
        for field, value in patch.model_dump(exclude_unset=True).items():
            if value is not None:
                setattr(row, field, value)
                changes[field] = str(value) if isinstance(value, Decimal) else value

        self._session.flush()
        self._audit.record(
            AuditRecordCreate(
                request_id=request_id or "quota-update",
                trace_id=request_id or "quota-update",
                organization_id=organization_id,
                user_id=actor_user_id,
                event_type=AuditEventType.QUOTA_UPDATED,
                resource_type="organization_quota",
                resource_id=str(organization_id),
                metadata={"changes": changes},
                result=AuditResult.SUCCESS,
                severity=AuditSeverity.INFO,
            )
        )
        return _to_config(row)

    def get_status(self, organization_id: uuid.UUID) -> QuotaStatus:
        quota = self.get_or_create_quota(organization_id)
        usage = self._usage_snapshot(organization_id, quota)
        warnings: list[str] = []
        blocked: list[str] = []
        soft = False
        hard = False

        checks = [
            ("monthly_tokens", usage.monthly_tokens_pct, quota.monthly_token_limit > 0),
            ("monthly_cost", usage.monthly_cost_pct, quota.monthly_cost_limit > 0),
            ("daily_requests", usage.daily_requests_pct, quota.daily_request_limit > 0),
        ]
        for label, pct, enabled in checks:
            if not enabled:
                continue
            if pct >= float(quota.hard_block_threshold):
                hard = True
                blocked.append(label)
                warnings.append(f"{label} hard limit reached ({pct:.0%})")
            elif pct >= float(quota.soft_warning_threshold):
                soft = True
                warnings.append(f"{label} soft warning ({pct:.0%})")

        for feature, limit_field in FEATURE_LIMIT_FIELDS.items():
            limit = getattr(quota, limit_field)
            used = usage.feature_usage.get(feature, 0)
            if limit <= 0:
                continue
            pct = used / limit
            if pct >= float(quota.hard_block_threshold):
                hard = True
                blocked.append(feature)
                warnings.append(f"{feature} hard limit reached ({used}/{limit})")
            elif pct >= float(quota.soft_warning_threshold):
                soft = True
                warnings.append(f"{feature} soft warning ({used}/{limit})")

        return QuotaStatus(
            quota=quota,
            usage=usage,
            soft_limit_reached=soft,
            hard_limit_reached=hard,
            warnings=warnings,
            blocked_features=blocked,
        )

    def check_feature(
        self,
        organization_id: uuid.UUID,
        feature: str,
        *,
        request_id: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> QuotaCheckResult:
        """Evaluate quota for a feature before processing a request."""
        quota = self.get_or_create_quota(organization_id)
        usage = self._usage_snapshot(organization_id, quota)
        warnings: list[str] = []
        admitted = self._has_turn_admission(organization_id, user_id)

        if quota.daily_request_limit >= 0:
            if quota.daily_request_limit == 0:
                return self._block(
                    organization_id,
                    feature,
                    "Daily request quota exceeded (0/0).",
                    request_id=request_id,
                    user_id=user_id,
                )
            pct = usage.daily_requests_used / quota.daily_request_limit
            if not admitted and pct >= float(quota.hard_block_threshold):
                return self._block(
                    organization_id,
                    feature,
                    (
                        "Daily request quota exceeded "
                        f"({usage.daily_requests_used}/{quota.daily_request_limit})."
                    ),
                    request_id=request_id,
                    user_id=user_id,
                )
            if pct >= float(quota.soft_warning_threshold):
                warnings.append(
                    "Daily request soft warning "
                    f"({usage.daily_requests_used}/{quota.daily_request_limit})."
                )

        if quota.monthly_token_limit >= 0:
            if quota.monthly_token_limit == 0:
                return self._block(
                    organization_id,
                    feature,
                    "Monthly token quota exceeded (0/0).",
                    request_id=request_id,
                    user_id=user_id,
                )
            committed_tokens = usage.monthly_tokens_used + usage.monthly_tokens_reserved
            pct = committed_tokens / quota.monthly_token_limit
            if pct >= float(quota.hard_block_threshold):
                return self._block(
                    organization_id,
                    feature,
                    (
                        "Monthly token quota exceeded "
                        f"({committed_tokens}/{quota.monthly_token_limit}, including reservations)."
                    ),
                    request_id=request_id,
                    user_id=user_id,
                )
            if pct >= float(quota.soft_warning_threshold):
                warnings.append(
                    "Monthly token soft warning "
                    f"({committed_tokens}/{quota.monthly_token_limit}, including reservations)."
                )

        if quota.monthly_cost_limit > 0:
            committed_cost = usage.monthly_cost_used + usage.monthly_cost_reserved
            pct = float(committed_cost / quota.monthly_cost_limit)
            if pct >= float(quota.hard_block_threshold):
                return self._block(
                    organization_id,
                    feature,
                    (
                        "Monthly cost quota exceeded "
                        f"(${committed_cost}/${quota.monthly_cost_limit}, including reservations)."
                    ),
                    request_id=request_id,
                    user_id=user_id,
                )
            if pct >= float(quota.soft_warning_threshold):
                warnings.append(
                    "Monthly cost soft warning "
                    f"(${committed_cost}/${quota.monthly_cost_limit}, including reservations)."
                )

        limit_field = FEATURE_LIMIT_FIELDS.get(feature)
        if limit_field:
            limit = getattr(quota, limit_field)
            used = usage.feature_usage.get(feature, 0)
            if limit == 0:
                return self._block(
                    organization_id,
                    feature,
                    f"Feature quota exceeded for {feature} (0/0).",
                    request_id=request_id,
                    user_id=user_id,
                )
            if limit > 0 and not (admitted and feature == "agent_chat"):
                pct = used / limit
                if pct >= float(quota.hard_block_threshold):
                    return self._block(
                        organization_id,
                        feature,
                        f"Feature quota exceeded for {feature} ({used}/{limit}).",
                        request_id=request_id,
                        user_id=user_id,
                    )
                if pct >= float(quota.soft_warning_threshold):
                    warnings.append(f"{feature} soft warning ({used}/{limit}).")

        if warnings:
            self._warn(organization_id, feature, warnings, request_id=request_id, user_id=user_id)
            return QuotaCheckResult(
                allowed=True,
                soft_warning=True,
                hard_blocked=False,
                message="; ".join(warnings),
                feature=feature,
                warnings=tuple(warnings),
            )

        return QuotaCheckResult(
            allowed=True,
            soft_warning=False,
            hard_blocked=False,
            message="within quota",
            feature=feature,
        )

    def _has_turn_admission(self, organization_id: uuid.UUID, user_id: uuid.UUID | None) -> bool:
        """Only a durable, server-written admission reserves request capacity."""
        from app.db.models import UsageEvent
        from app.services.turn_context import current_turn

        turn = current_turn()
        if turn is None:
            return False
        admission = self._session.get(UsageEvent, uuid.uuid5(turn.turn_id, "admission"))
        return bool(
            admission is not None
            and admission.organization_id == organization_id
            and admission.user_id == user_id
            and admission.provider == "agent_turn_admission"
            and admission.feature == "agent_chat"
            and admission.request_id == str(turn.turn_id)
        )

    def record_request_usage(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        request_id: str,
        feature: str,
        provider: str = "internal",
    ) -> None:
        """Record a lightweight quota-counted request (non-LLM endpoints)."""
        from app.schemas.usage import UsageEventCreate
        from app.services.usage_service import UsageService

        UsageService(self._session).record(
            UsageEventCreate(
                request_id=request_id,
                organization_id=organization_id,
                user_id=user_id,
                feature=feature,
                provider=provider,
                input_tokens=0,
                output_tokens=0,
                provider_metadata={"cost_source": CostSource.UNAVAILABLE.value},
            )
        )

    def _usage_snapshot(
        self,
        organization_id: uuid.UUID,
        quota: OrganizationQuotaConfig,
    ) -> QuotaUsageSnapshot:
        month = month_start()
        today = day_start()
        monthly = self._usage.summarize(organization_id=organization_id, since=month)
        daily_count = self._usage.count_requests_since(organization_id=organization_id, since=today)

        feature_usage: dict[str, int] = {}
        for feature in FEATURE_LIMIT_FIELDS:
            feature_usage[feature] = self._usage.count_requests_since(
                organization_id=organization_id,
                since=month,
                feature=feature,
            )

        monthly_cost = monthly.total_cost
        # A socket/restart failure cannot release a promise of potentially billed
        # work. Outstanding holds span a month boundary until server evidence
        # closes them. Keep actual usage separate from these conservative holds.
        reservations = list(
            self._session.scalars(
                select(VoiceSessionReservation).where(
                    VoiceSessionReservation.organization_id == organization_id,
                    or_(
                        VoiceSessionReservation.status != "closed",
                        VoiceSessionReservation.accounted_cost.is_(None),
                    ),
                )
            )
        )
        reserved_cost = sum((row.reserved_cost for row in reservations), Decimal("0"))
        reserved_tokens = sum(row.reserved_tokens for row in reservations)
        token_pct = (
            (monthly.total_tokens + reserved_tokens) / quota.monthly_token_limit
            if quota.monthly_token_limit
            else 0.0
        )
        cost_pct = (
            float((monthly_cost + reserved_cost) / quota.monthly_cost_limit)
            if quota.monthly_cost_limit
            else 0.0
        )
        daily_pct = daily_count / quota.daily_request_limit if quota.daily_request_limit else 0.0

        return QuotaUsageSnapshot(
            monthly_tokens_used=monthly.total_tokens,
            monthly_tokens_reserved=reserved_tokens,
            monthly_tokens_limit=quota.monthly_token_limit,
            monthly_tokens_pct=min(token_pct, 1.0),
            monthly_cost_used=monthly_cost,
            monthly_cost_reserved=reserved_cost,
            monthly_cost_limit=quota.monthly_cost_limit,
            monthly_cost_pct=min(cost_pct, 1.0),
            daily_requests_used=daily_count,
            daily_requests_limit=quota.daily_request_limit,
            daily_requests_pct=min(daily_pct, 1.0),
            feature_usage=feature_usage,
        )

    def _block(
        self,
        organization_id: uuid.UUID,
        feature: str,
        message: str,
        *,
        request_id: str | None,
        user_id: uuid.UUID | None,
    ) -> QuotaCheckResult:
        self._audit.record_durable_isolated(
            AuditRecordCreate(
                request_id=request_id or "quota-block",
                trace_id=request_id or "quota-block",
                organization_id=organization_id,
                user_id=user_id,
                event_type=AuditEventType.QUOTA_BLOCK,
                resource_type="organization_quota",
                resource_id=str(organization_id),
                metadata={"feature": feature, "message": message},
                result=AuditResult.BLOCKED,
                severity=AuditSeverity.HIGH,
            )
        )
        logger.info("quota_block", organization_id=str(organization_id), feature=feature)
        return QuotaCheckResult(
            allowed=False,
            soft_warning=False,
            hard_blocked=True,
            message=message,
            feature=feature,
        )

    def _warn(
        self,
        organization_id: uuid.UUID,
        feature: str,
        warnings: list[str],
        *,
        request_id: str | None,
        user_id: uuid.UUID | None,
    ) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id=request_id or "quota-warning",
                trace_id=request_id or "quota-warning",
                organization_id=organization_id,
                user_id=user_id,
                event_type=AuditEventType.QUOTA_WARNING,
                resource_type="organization_quota",
                resource_id=str(organization_id),
                metadata={"feature": feature, "warnings": warnings},
                result=AuditResult.WARNING,
                severity=AuditSeverity.MEDIUM,
            )
        )


def _to_config(row: OrganizationQuotaModel) -> OrganizationQuotaConfig:
    return OrganizationQuotaConfig(
        organization_id=row.organization_id,
        monthly_token_limit=row.monthly_token_limit,
        monthly_cost_limit=row.monthly_cost_limit,
        daily_request_limit=row.daily_request_limit,
        limit_agent_chat=row.limit_agent_chat,
        limit_rag_ingest=row.limit_rag_ingest,
        limit_market_analyze=row.limit_market_analyze,
        limit_agent_narrative=row.limit_agent_narrative,
        limit_paper_execution=row.limit_paper_execution,
        soft_warning_threshold=row.soft_warning_threshold,
        hard_block_threshold=row.hard_block_threshold,
        plan_id=getattr(row, "plan_id", "free"),
    )
