"""Tenant-scoped experiment consumer: admit trusted samples and read native performance.

This performs stored reads/reconciliation only. Integration may wire the existing
sample route and a Reader route to this facade; no runtime/exchange client is started.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError
from app.experiments.blofin_source import BloFinExperimentSourceResolver, rounded
from app.experiments.models import ExperimentSampleRow
from app.experiments.native_producer import BloFinNativeOutcomeProducer
from app.experiments.outcome_contract import ExperimentPerformance, NativeOutcome
from app.experiments.repository import ExperimentRepository
from app.experiments.service import ExperimentService, semantic_hash
from app.market_contracts.models import parse_canonical_decimal
from app.schemas.experiments import ExperimentSample, ExperimentSampleCreate, ExperimentSource
from app.security.tenant import TenantContext


class BloFinExperimentReads:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        tenant: TenantContext,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session, self.tenant = session, tenant
        self.clock = clock or (lambda: datetime.now(UTC))
        self.resolver = BloFinExperimentSourceResolver(session, settings, tenant, clock=self.clock)
        self.domain = ExperimentService(
            session, settings, clock=self.clock, source_resolver=self.resolver
        )

    def record_sample(
        self, experiment_id: UUID, version_id: UUID, body: ExperimentSampleCreate
    ) -> ExperimentSample:
        return self.domain.record_sample(self.tenant, experiment_id, version_id, body)

    def outcome(
        self, experiment_id: UUID, version_id: UUID, variant_key: str, source_record_id: str
    ) -> NativeOutcome:
        row = ExperimentRepository(self.session, self.tenant).version(experiment_id, version_id)
        version = self.domain.view(row)
        result = self.resolver.read(version, variant_key, source_record_id)
        return BloFinNativeOutcomeProducer(
            self.session, self.resolver.settings, self.tenant, clock=self.clock
        ).qualify_existing_origin(version, variant_key, source_record_id, result)

    def performance(
        self, experiment_id: UUID, version_id: UUID, variant_key: str
    ) -> ExperimentPerformance:
        row = ExperimentRepository(self.session, self.tenant).version(experiment_id, version_id)
        version = self.domain.view(row)
        config = version.configuration
        if config.account.source is not ExperimentSource.BLOFIN_DEMO:
            raise ConflictError(
                "Internal simulation requires its own outcome reader.",
                code="experiment_native_source_required",
            )
        if variant_key not in {v.key for v in config.variants}:
            raise ConflictError("Unknown experiment variant.", code="experiment_variant_unknown")
        now = self.clock()
        samples = list(
            self.session.scalars(
                select(ExperimentSampleRow)
                .where(
                    ExperimentSampleRow.organization_id == self.tenant.organization_id,
                    ExperimentSampleRow.user_id == self.tenant.user_id,
                    ExperimentSampleRow.version_id == version.id,
                    ExperimentSampleRow.variant_key == variant_key,
                )
                .order_by(ExperimentSampleRow.source_record_id)
                .limit(2001)
            )
        )
        base: dict[str, Any] = {
            "organization_id": version.organization_id,
            "version_id": version.id,
            "sample_group_id": version.sample_group_id,
            "variant_key": variant_key,
            "native_uid": config.account.native_uid,
            "evaluated_at": now,
            "required_sample_count": config.sample_target.minimum,
        }
        if len(samples) > 2000:
            return ExperimentPerformance(
                **base, status="unavailable", reason="outcome_read_budget_exceeded", sample_count=0
            )
        # One fixed evaluation clock for every component, not a changing per-sample cutoff.
        resolver = BloFinExperimentSourceResolver(
            self.session, self.resolver.settings, self.tenant, clock=lambda: now
        )
        producer = BloFinNativeOutcomeProducer(
            self.session, self.resolver.settings, self.tenant, clock=lambda: now
        )
        outcomes, valid = [], []
        for sample in samples:
            outcome = resolver.read(version, variant_key, sample.source_record_id)
            outcome = producer.qualify_existing_origin(
                version, variant_key, sample.source_record_id, outcome
            )
            outcomes.append(outcome)
            if (
                (outcome.status == "available" or outcome.closure_verified)
                and outcome.proof is not None
                and sample.source == ExperimentSource.BLOFIN_DEMO.value
                and sample.kind == "closed_trade"
                and sample.sample_group_id == version.sample_group_id
                and sample.account_scope == config.account.native_uid
                and sample.proof == outcome.proof.model_dump(mode="json")
                and sample.evidence_hash == semantic_hash(outcome.proof.model_dump())
                and sample.recorded_at <= now
            ):
                valid.append(outcome)
        base.update(sample_count=len(valid), outcomes=tuple(outcomes))
        price_outcomes = [o for o in valid if o.closure_verified]
        if len(price_outcomes) >= config.sample_target.minimum and len(price_outcomes) == len(
            samples
        ):
            with localcontext() as context:
                context.prec = 80
                values = [
                    parse_canonical_decimal(o.price_pnl_excluding_fees_and_funding)
                    for o in price_outcomes
                ]
                base.update(
                    price_pnl_sample_count=len(values),
                    price_pnl_currency=price_outcomes[0].currency,
                    price_pnl_methodology="verified-linear-fill-prices/v1",
                    price_pnl_excluding_fees_and_funding=rounded(sum(values, Decimal(0))),
                    price_pnl_win_rate=rounded(
                        Decimal(sum(v > 0 for v in values)) / Decimal(len(values))
                    ),
                )
        reason = None
        if len(valid) != len(samples):
            reason = "sample_outcome_unavailable_or_changed"
        elif len(valid) < config.sample_target.minimum:
            reason = "insufficient_samples"
        elif len({o.currency for o in valid}) != 1:
            reason = "outcome_currency_mismatch"
        elif any(o.status != "available" for o in valid):
            reason = "native_monetary_semantics_unverified"
        if reason:
            return ExperimentPerformance(**base, status="unavailable", reason=reason)
        with localcontext() as context:
            context.prec, context.rounding = 80, ROUND_HALF_EVEN
            gross = sum(
                (parse_canonical_decimal(o.reported_realized_pnl) for o in valid), Decimal(0)
            )
            fees = sum((parse_canonical_decimal(o.fee_cost) for o in valid), Decimal(0))
            pnl = sum(
                (
                    parse_canonical_decimal(o.realized_pnl_after_fees_excluding_funding)
                    for o in valid
                ),
                Decimal(0),
            )
            wins = sum(
                parse_canonical_decimal(o.realized_pnl_after_fees_excluding_funding) > 0
                for o in valid
            )
            return ExperimentPerformance(
                **base,
                status="available",
                currency=valid[0].currency,
                reported_realized_pnl=rounded(gross),
                fee_cost=rounded(fees),
                realized_pnl_after_fees_excluding_funding=rounded(pnl),
                win_rate=rounded(Decimal(wins) / Decimal(len(valid))),
            )
