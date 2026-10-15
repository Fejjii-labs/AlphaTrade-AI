"""Durable one-trigger research evaluation, isolated from execution and samples."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.db.models import Membership, UserStrategy, UserStrategyVersion
from app.db.strategy_immutability import strategy_version_content_hash
from app.experiments.identity import utc
from app.experiments.repository import ExperimentRepository
from app.experiments.service import ExperimentService
from app.schemas.common import MembershipRole
from app.schemas.experiments import ExperimentFamily
from app.schemas.trendpulse_screening import (
    TrendPulseScreeningCreate,
    TrendPulseScreeningDetail,
    TrendPulseScreeningEvidence,
    TrendPulseScreeningPage,
    TrendPulseScreeningRecord,
)
from app.security.tenant import TenantContext
from app.strategy_brain.trendpulse_1r.adapter import evaluate_trendpulse
from app.strategy_brain.trendpulse_1r.contracts import (
    TrendPulseParameters,
    TrendPulseResult,
    TrendPulseSignal,
    TrendPulseSpec,
    TrendPulseStatus,
    exact_hash,
)
from app.strategy_brain.trendpulse_1r.experiment import (
    ExperimentBoundTrendPulse,
    bind_experiment_signal,
)
from app.strategy_brain.trendpulse_screening.acquisition import (
    BinanceScreeningAcquirer,
    ScreeningAcquirer,
    ScreeningAcquisitionError,
)
from app.strategy_brain.trendpulse_screening.models import TrendPulseScreeningRow

_SLOTS = threading.BoundedSemaphore(2)


def _derivation_hash(signal: TrendPulseSignal) -> str:
    # A later decision/receipt changes transport proof, never the accepted derivation.
    values = signal.model_dump(exclude={"content_hash", "known_at", "decision_at", "evidence"})
    values["evidence"] = [ref.model_dump(exclude={"available_at"}) for ref in signal.evidence]
    return exact_hash(values)


class TrendPulseScreeningService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        acquirer: ScreeningAcquirer | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session, self.settings, self.clock = session, settings, clock
        self.acquirer = acquirer or BinanceScreeningAcquirer(
            clock=clock, base_url=settings.market_data_futures_base_url
        )

    def _conditions(self, tenant: TenantContext) -> tuple[ColumnElement[bool], ...]:
        return (
            TrendPulseScreeningRow.organization_id == tenant.organization_id,
            TrendPulseScreeningRow.user_id == tenant.user_id,
        )

    def _existing(
        self, tenant: TenantContext, body: TrendPulseScreeningCreate
    ) -> TrendPulseScreeningRow | None:
        return self.session.scalar(
            select(TrendPulseScreeningRow).where(
                *self._conditions(tenant), TrendPulseScreeningRow.request_id == body.request_id
            )
        )

    def screen(
        self,
        tenant: TenantContext,
        experiment_id: UUID,
        version_id: UUID,
        body: TrendPulseScreeningCreate,
    ) -> TrendPulseScreeningDetail:
        membership = self.session.scalar(
            select(Membership).where(
                Membership.organization_id == tenant.organization_id,
                Membership.user_id == tenant.user_id,
            )
        )
        allowed = {MembershipRole.OWNER, MembershipRole.TRADER}
        if (
            membership is None
            or membership.role not in allowed
            or tenant.membership_role not in allowed
        ):
            raise ForbiddenError("Research screening requires Trader.")
        row = ExperimentRepository(self.session, tenant).version(experiment_id, version_id)
        request_hash = exact_hash(
            {"experiment_id": experiment_id, "version_id": version_id, "request": body.model_dump()}
        )
        old = self._existing(tenant, body)
        if old is not None:
            return self._repeat(old, request_hash)
        if not self.settings.trendpulse_screening_enabled:
            raise AppError(
                "Research screening is disabled.", status_code=503, code="screening_disabled"
            )
        if self.acquirer.mode == "public_rest" and (
            not self.settings.market_data_enabled
            or self.settings.perpetual_evidence_source != "binance_usdm"
            or self.settings.perpetual_evidence_secondary_source != "none"
        ):
            raise AppError(
                "Explicit Binance public evidence configuration is required.",
                status_code=503,
                code="screening_source_not_enabled",
            )
        version = ExperimentService(self.session, self.settings).view(row)
        if version.configuration.family is not ExperimentFamily.TRENDPULSE_1R:
            raise ConflictError("Experiment is not TrendPulse1R.", code="screening_family_mismatch")
        variant = next(
            (v for v in version.configuration.variants if v.key == body.variant_key), None
        )
        if variant is None:
            raise NotFoundError("Experiment variant not found.")
        strategy = self.session.get(
            UserStrategy, version.configuration.strategy_id, populate_existing=True
        )
        authored = self.session.get(
            UserStrategyVersion, variant.strategy_version_id, populate_existing=True
        )
        if (
            strategy is None
            or authored is None
            or authored.strategy_id != strategy.id
            or (strategy.organization_id, strategy.user_id)
            != (tenant.organization_id, tenant.user_id)
            or version.strategy_content_hashes.get(str(authored.id)) != authored.content_hash
            or strategy_version_content_hash(
                card=authored.card,
                structured_rules=authored.structured_rules,
                lesson_source_metadata=authored.lesson_source_metadata,
                pattern_spec=authored.pattern_spec,
            )
            != authored.content_hash
        ):
            raise ConflictError(
                "Immutable strategy binding differs.", code="screening_strategy_mismatch"
            )
        spec = TrendPulseSpec.model_validate(authored.pattern_spec)
        expected_config_hash = exact_hash(
            {
                "organization_id": version.organization_id,
                "user_id": version.user_id,
                "experiment_id": version.experiment_id,
                "version_id": version.id,
                "sample_group_id": version.sample_group_id,
                "configuration": version.configuration.model_dump(),
                "strategy_content_hashes": version.strategy_content_hashes,
            }
        )
        if (
            expected_config_hash != version.configuration_hash
            or exact_hash(spec.parameters)
            != exact_hash(TrendPulseParameters.model_validate(variant.parameters))
            or spec.symbol not in version.configuration.symbols
        ):
            raise ConflictError(
                "Experiment configuration binding differs.", code="screening_configuration_mismatch"
            )
        # Cross-process, nonblocking single-flight per immutable version. No worker threads.
        lock_key = int(exact_hash({"screening_version": version.id})[:16], 16)
        if lock_key >= 2**63:
            lock_key -= 2**64
        if not self.session.scalar(select(func.pg_try_advisory_xact_lock(lock_key))):
            raise ConflictError(
                "Research screening is already in progress.", code="screening_in_progress"
            )
        old = self._existing(tenant, body)
        if old is not None:
            return self._repeat(old, request_hash)
        start = self.clock()
        evidence = TrendPulseScreeningEvidence()
        acquired = False
        result = TrendPulseResult(status=TrendPulseStatus.UNAVAILABLE, reason="trigger_not_closed")
        if start >= body.trigger_end + timedelta(seconds=60):
            result = TrendPulseResult(status=TrendPulseStatus.REFUSED, reason="trigger_expired")
        elif start < body.trigger_end:
            pass
        elif start < body.trigger_end + timedelta(seconds=5):
            result = TrendPulseResult(
                status=TrendPulseStatus.UNAVAILABLE, reason="provider_settlement_pending"
            )
        elif not _SLOTS.acquire(blocking=False):
            result = TrendPulseResult(
                status=TrendPulseStatus.UNAVAILABLE, reason="screening_capacity_exhausted"
            )
        else:
            try:
                evidence = self.acquirer.acquire(spec, body.trigger_end)
                acquired = True
            except ScreeningAcquisitionError as exc:
                evidence = exc.evidence
                result = TrendPulseResult(status=TrendPulseStatus.UNAVAILABLE, reason=exc.reason)
            finally:
                _SLOTS.release()
        decision = self.clock()
        if acquired and evidence.instrument_rules is not None:
            result = evaluate_trendpulse(
                spec,
                trend_bars=evidence.trend_bars,
                trend_observations=evidence.trend_observations,
                entry_bars=evidence.entry_bars,
                entry_observations=evidence.entry_observations,
                instrument_rules=evidence.instrument_rules,
                trigger_end=body.trigger_end,
                evaluated_at=decision,
            )
        bound = None
        dedupe_key, signal_id, duplicate_of = None, None, None
        signal = result.signal
        if signal is not None:
            signal_id = signal.signal_id
            dedupe_key = exact_hash(
                {"signal_id": signal_id, "market_identity": signal.identity.model_dump()}
            )
            previous = self.session.scalar(
                select(TrendPulseScreeningRow).where(
                    *self._conditions(tenant),
                    TrendPulseScreeningRow.version_id == version.id,
                    TrendPulseScreeningRow.variant_key == variant.key,
                    TrendPulseScreeningRow.dedupe_key == dedupe_key,
                )
            )
            if previous is not None:
                prior = ExperimentBoundTrendPulse.model_validate(previous.signal).signal
                dedupe_key = None
                if _derivation_hash(prior) != _derivation_hash(signal):
                    result = TrendPulseResult(
                        status=TrendPulseStatus.REFUSED,
                        reason="persisted_signal_derivation_conflict",
                    )
                    signal_id = None
                else:
                    result = TrendPulseResult(
                        status=TrendPulseStatus.DUPLICATE,
                        reason="signal_already_persisted",
                        duplicate_signal_id=signal_id,
                    )
                    duplicate_of = previous.id
            else:
                bound = bind_experiment_signal(
                    version,
                    variant_key=variant.key,
                    spec=spec,
                    strategy_content_hash=authored.content_hash,
                    signal=signal,
                )
        stored = TrendPulseScreeningRow(
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            experiment_id=experiment_id,
            version_id=version_id,
            request_id=body.request_id,
            request_hash=request_hash,
            configuration_hash=version.configuration_hash,
            variant_key=variant.key,
            strategy_version_id=authored.id,
            strategy_content_hash=authored.content_hash,
            trigger_end=body.trigger_end,
            acquisition_started_at=start,
            decision_at=decision,
            evidence_mode=self.acquirer.mode,
            receipt_provenance=self.acquirer.provenance,
            status=result.status.value,
            reason=result.reason,
            trend_receipts=len(evidence.trend_observations),
            entry_receipts=len(evidence.entry_observations),
            evidence=evidence.model_dump(mode="json"),
            evidence_hash=exact_hash(evidence),
            signal=bound.model_dump(mode="json") if bound else None,
            signal_id=signal_id,
            dedupe_key=dedupe_key,
            duplicate_of=duplicate_of,
        )
        try:
            with self.session.begin_nested():
                self.session.add(stored)
                self.session.flush()
        except IntegrityError:
            old = self._existing(tenant, body)
            if old is None:
                raise
            return self._repeat(old, request_hash)
        return self._view(stored)

    def _repeat(self, row: TrendPulseScreeningRow, request_hash: str) -> TrendPulseScreeningDetail:
        if row.request_hash != request_hash:
            raise ConflictError(
                "Screening request ID was reused with different inputs.",
                code="screening_request_conflict",
            )
        return self._view(row)

    def _view(self, row: TrendPulseScreeningRow) -> TrendPulseScreeningDetail:
        return TrendPulseScreeningDetail.model_validate(
            {
                **self._summary(row).model_dump(),
                "evidence": row.evidence,
                "signal": row.signal,
            }
        )

    def _summary(self, row: TrendPulseScreeningRow) -> TrendPulseScreeningRecord:
        return TrendPulseScreeningRecord.model_validate(
            {
                **{
                    name: getattr(row, name)
                    for name in TrendPulseScreeningRecord.model_fields
                    if hasattr(row, name)
                },
                "experiment_version_id": row.version_id,
                "trigger_end": utc(row.trigger_end),
                "acquisition_started_at": utc(row.acquisition_started_at),
                "decision_at": utc(row.decision_at),
            }
        )

    def detail(self, tenant: TenantContext, record_id: UUID) -> TrendPulseScreeningDetail:
        row = self.session.scalar(
            select(TrendPulseScreeningRow).where(
                *self._conditions(tenant), TrendPulseScreeningRow.id == record_id
            )
        )
        if row is None:
            raise NotFoundError("Research screening not found.")
        return self._view(row)

    def list(
        self,
        tenant: TenantContext,
        experiment_id: UUID,
        version_id: UUID,
        *,
        limit: int = 20,
        offset: int = 0,
        status: TrendPulseStatus | None = None,
    ) -> TrendPulseScreeningPage:
        if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
            raise ValueError("Screening read bounds exceeded")
        ExperimentRepository(self.session, tenant).version(experiment_id, version_id)
        conditions = (*self._conditions(tenant), TrendPulseScreeningRow.version_id == version_id)
        if status is not None:
            conditions = (*conditions, TrendPulseScreeningRow.status == status.value)
        total = (
            self.session.scalar(
                select(func.count()).select_from(TrendPulseScreeningRow).where(*conditions)
            )
            or 0
        )
        rows = self.session.scalars(
            select(TrendPulseScreeningRow)
            .options(defer(TrendPulseScreeningRow.evidence), defer(TrendPulseScreeningRow.signal))
            .where(*conditions)
            .order_by(TrendPulseScreeningRow.decision_at.desc(), TrendPulseScreeningRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return TrendPulseScreeningPage(
            items=[self._summary(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
