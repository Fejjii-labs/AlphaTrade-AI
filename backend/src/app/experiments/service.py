"""Experiment lifecycle and sample authority. Never activates an execution runtime."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import localcontext
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.db.models import Membership, UserStrategy, UserStrategyVersion
from app.experiments.attribution import ExperimentSourceResolver
from app.experiments.identity import account_scope, require_account, utc
from app.experiments.models import (
    ExperimentEventRow,
    ExperimentRow,
    ExperimentSampleRow,
    ExperimentVersionRow,
)
from app.experiments.repository import ExperimentRepository
from app.schemas.common import MembershipRole
from app.schemas.experiments import (
    ExperimentApproval,
    ExperimentConfiguration,
    ExperimentCreate,
    ExperimentDetail,
    ExperimentFamily,
    ExperimentMode,
    ExperimentPage,
    ExperimentPromotion,
    ExperimentSample,
    ExperimentSampleCreate,
    ExperimentTransition,
    ExperimentVersion,
    ExperimentVersionCreate,
)
from app.schemas.nested_continuation import NestedContinuationSpec, NestedParameters
from app.security.tenant import TenantContext
from app.services.canonical_serialization import canonical_sha256
from app.strategy_brain.sfp.contracts import SfpParameters, SfpSpec
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseParameters, TrendPulseSpec


def semantic_hash(value: dict[str, Any]) -> str:
    with localcontext() as context:
        context.prec = 80
        return canonical_sha256(value)


class ExperimentService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
        source_resolver: ExperimentSourceResolver | None = None,
    ) -> None:
        self.session, self.settings = session, settings
        self.clock = clock or (lambda: datetime.now(UTC))
        self.source_resolver = source_resolver

    def _mutation(self, tenant: TenantContext, *, owner: bool = False) -> None:
        membership = self.session.scalar(
            select(Membership).where(
                Membership.organization_id == tenant.organization_id,
                Membership.user_id == tenant.user_id,
            )
        )
        allowed = {MembershipRole.OWNER} if owner else {MembershipRole.OWNER, MembershipRole.TRADER}
        if (
            membership is None
            or membership.role not in allowed
            or tenant.membership_role not in allowed
        ):
            raise ForbiddenError(
                "Experiment approval requires Owner; other mutations require Trader."
            )

    def _strategy_hashes(
        self, tenant: TenantContext, config: ExperimentConfiguration
    ) -> dict[str, str]:
        strategy = self.session.get(UserStrategy, config.strategy_id, populate_existing=True)
        if strategy is None or (strategy.organization_id, strategy.user_id) != (
            tenant.organization_id,
            tenant.user_id,
        ):
            raise NotFoundError("Strategy not found.")
        hashes, symbols, timeframes = {}, set(), set()
        for variant in config.variants:
            version = self.session.get(
                UserStrategyVersion, variant.strategy_version_id, populate_existing=True
            )
            if version is None or version.strategy_id != strategy.id:
                raise NotFoundError("Strategy version not found.")
            spec = version.pattern_spec or {}
            if spec.get("kind") != config.family.value:
                raise ConflictError(
                    "Experiment family differs from immutable strategy.",
                    code="experiment_strategy_mismatch",
                )
            authored: NestedContinuationSpec | SfpSpec | TrendPulseSpec
            parameters: NestedParameters | SfpParameters | TrendPulseParameters
            try:
                if config.family is ExperimentFamily.NESTED:
                    authored = NestedContinuationSpec.model_validate(spec)
                    parameters = NestedParameters.model_validate(variant.parameters)
                elif config.family is ExperimentFamily.SFP:
                    authored = SfpSpec.model_validate(spec)
                    parameters = SfpParameters.model_validate(variant.parameters)
                else:
                    authored = TrendPulseSpec.model_validate(spec)
                    parameters = TrendPulseParameters.model_validate(variant.parameters)
            except ValidationError as exc:
                raise ConflictError(
                    "Stored strategy parameters are invalid.", code="experiment_strategy_mismatch"
                ) from exc
            if semantic_hash(parameters.model_dump()) != semantic_hash(
                authored.parameters.model_dump()
            ):
                raise ConflictError(
                    "Parameters must match the immutable strategy version.",
                    code="experiment_strategy_mismatch",
                )
            symbols.add(authored.symbol)
            timeframes.add(authored.trigger_timeframe)
            if isinstance(authored, TrendPulseSpec):
                timeframes.add(authored.trend_timeframe)
            hashes[str(version.id)] = version.content_hash
        if symbols != set(config.symbols) or timeframes != set(config.timeframes):
            raise ConflictError(
                "Universe must match authored variant symbol/timeframe bindings.",
                code="experiment_strategy_mismatch",
            )
        if (
            config.family is ExperimentFamily.SFP
            and config.sample_target.kind != "setup_observation"
        ):
            raise ConflictError(
                "SFP has no authorized automatic trade plan.", code="experiment_adapter_unavailable"
            )
        if (
            config.family is ExperimentFamily.TRENDPULSE_1R
            and config.sample_target.kind != "setup_observation"
        ):
            raise ConflictError(
                "TrendPulse1R research geometry has no authorized automatic trade plan.",
                code="experiment_adapter_unavailable",
            )
        if (
            config.sample_target.kind == "closed_trade"
            and config.sample_target.minimum > config.risk_limits.max_trades_total
        ):
            raise ConflictError("Sample minimum exceeds the approved trade envelope.")
        return hashes

    def create(self, tenant: TenantContext, body: ExperimentCreate) -> ExperimentVersion:
        self._mutation(tenant)
        # A membership exists even before a root: serialize idempotent root creation.
        self.session.scalar(
            select(Membership)
            .where(
                Membership.organization_id == tenant.organization_id,
                Membership.user_id == tenant.user_id,
            )
            .with_for_update()
        )
        digest = semantic_hash(body.model_dump())
        existing = self.session.scalar(
            select(ExperimentRow).where(
                ExperimentRow.organization_id == tenant.organization_id,
                ExperimentRow.user_id == tenant.user_id,
                ExperimentRow.idempotency_key == body.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != digest:
                raise ConflictError("Experiment idempotency key was used for different content.")
            row = self.session.scalar(
                select(ExperimentVersionRow).where(
                    ExperimentVersionRow.experiment_id == existing.id,
                    ExperimentVersionRow.version == 1,
                )
            )
            assert row is not None
            return self.view(row)
        root = ExperimentRow(
            id=uuid4(),
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            name=body.name,
            idempotency_key=body.idempotency_key,
            request_hash=digest,
            latest_version=1,
            created_at=self.clock(),
        )
        self.session.add(root)
        self.session.flush()
        return self.view(self._new_version(tenant, root, body.configuration, parent=None))

    def _new_version(
        self,
        tenant: TenantContext,
        root: ExperimentRow,
        config: ExperimentConfiguration,
        *,
        parent: ExperimentVersionRow | None,
    ) -> ExperimentVersionRow:
        config = ExperimentConfiguration.model_validate(config.model_dump(mode="json"))
        require_account(self.session, tenant, config.account, self.settings, now=self.clock())
        hashes = self._strategy_hashes(tenant, config)
        if parent is not None:
            original = ExperimentConfiguration.model_validate(parent.configuration)
            if (config.account, config.family, config.strategy_id) != (
                original.account,
                original.family,
                original.strategy_id,
            ):
                raise ConflictError("Account/source/family changes require a separate experiment.")
            root.latest_version += 1
        identity, sample_group = uuid4(), uuid4()
        row = ExperimentVersionRow(
            id=identity,
            experiment_id=root.id,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            version=root.latest_version,
            parent_version_id=parent.id if parent else None,
            execution_account_id=config.account.execution_account_id,
            execution_source=config.account.source.value,
            native_uid=config.account.native_uid,
            account_scope=account_scope(config.account),
            state="draft",
            revision=0,
            configuration=config.model_dump(mode="json"),
            strategy_content_hashes=hashes,
            sample_group_id=sample_group,
            created_at=self.clock(),
            configuration_hash=semantic_hash(
                {
                    "organization_id": tenant.organization_id,
                    "user_id": tenant.user_id,
                    "experiment_id": root.id,
                    "version_id": identity,
                    "sample_group_id": sample_group,
                    "configuration": config.model_dump(),
                    "strategy_content_hashes": hashes,
                }
            ),
        )
        self.session.add(row)
        self.session.flush()
        self._event(tenant, row)
        return row

    def fork(
        self, tenant: TenantContext, root_id: UUID, body: ExperimentVersionCreate
    ) -> ExperimentVersion:
        self._mutation(tenant)
        repo = ExperimentRepository(self.session, tenant)
        parent = repo.version(root_id, body.parent_version_id, lock=True)
        return self.view(
            self._new_version(tenant, repo.root(root_id), body.configuration, parent=parent)
        )

    def _revision(self, row: ExperimentVersionRow, expected: int) -> None:
        if row.revision != expected:
            raise ConflictError(
                "Experiment state changed; refresh before retrying.",
                code="experiment_revision_conflict",
            )

    def _revalidate(self, tenant: TenantContext, row: ExperimentVersionRow) -> None:
        config = ExperimentConfiguration.model_validate(row.configuration)
        require_account(self.session, tenant, config.account, self.settings, now=self.clock())
        if self._strategy_hashes(tenant, config) != row.strategy_content_hashes:
            raise ConflictError("Immutable strategy content changed.")
        expected = semantic_hash(
            {
                "organization_id": row.organization_id,
                "user_id": row.user_id,
                "experiment_id": row.experiment_id,
                "version_id": row.id,
                "sample_group_id": row.sample_group_id,
                "configuration": config.model_dump(),
                "strategy_content_hashes": row.strategy_content_hashes,
            }
        )
        if expected != row.configuration_hash:
            raise ConflictError("Experiment configuration hash mismatch.")

    def approve(
        self, tenant: TenantContext, root_id: UUID, identity: UUID, body: ExperimentApproval
    ) -> ExperimentVersion:
        self._mutation(tenant, owner=True)
        row = ExperimentRepository(self.session, tenant).version(root_id, identity, lock=True)
        self._revision(row, body.expected_revision)
        now = self.clock()
        if row.state != "pending_approval" or body.configuration_hash != row.configuration_hash:
            raise ConflictError("Approval must name the exact submitted configuration.")
        if not now < body.authorized_until <= now + timedelta(days=30):
            raise ConflictError("Approval must expire within thirty days.")
        self._revalidate(tenant, row)
        row.approved_at, row.approved_by, row.authorized_until = (
            now,
            tenant.user_id,
            body.authorized_until,
        )
        self._advance(tenant, row, "approved")
        return self.view(row)

    def transition(
        self, tenant: TenantContext, root_id: UUID, identity: UUID, body: ExperimentTransition
    ) -> ExperimentVersion:
        self._mutation(tenant)
        row = ExperimentRepository(self.session, tenant).version(root_id, identity, lock=True)
        self._revision(row, body.expected_revision)
        destinations = {
            "submit": ("pending_approval", {"draft"}),
            "start": ("running", {"approved", "paused"}),
            "pause": ("paused", {"running"}),
            "complete": ("completed", {"running", "paused"}),
        }
        destination, states = destinations[body.action]
        if row.state not in states:
            raise ConflictError("Illegal experiment lifecycle transition.")
        now = self.clock()
        if body.action in {"submit", "start"}:
            self._revalidate(tenant, row)
        if body.action == "start" and (
            row.authorized_until is None or utc(row.authorized_until) <= now
        ):
            raise ConflictError("Experiment approval expired.", code="experiment_approval_expired")
        if body.action == "submit":
            row.submitted_at = now
        elif body.action == "start" and row.started_at is None:
            row.started_at = now
        elif body.action == "pause":
            row.paused_at = now
        elif body.action == "complete":
            row.completed_at = now
        self._advance(tenant, row, destination)
        return self.view(row)

    def _advance(self, tenant: TenantContext, row: ExperimentVersionRow, state: str) -> None:
        row.state, row.revision = state, row.revision + 1
        self.session.flush()
        self._event(tenant, row)

    def _event(self, tenant: TenantContext, row: ExperimentVersionRow) -> None:
        self.session.add(
            ExperimentEventRow(
                version_id=row.id,
                organization_id=row.organization_id,
                user_id=row.user_id,
                actor_user_id=tenant.user_id,
                revision=row.revision,
                state=row.state,
                occurred_at=self.clock(),
                configuration_hash=row.configuration_hash,
            )
        )
        self.session.flush()

    def promote(
        self, tenant: TenantContext, root_id: UUID, identity: UUID, body: ExperimentPromotion
    ) -> ExperimentVersion:
        self._mutation(tenant)
        repo = ExperimentRepository(self.session, tenant)
        parent = repo.version(root_id, identity, lock=True)
        config = ExperimentConfiguration.model_validate(parent.configuration)
        selected = next((v for v in config.variants if v.key == body.variant_key), None)
        if selected is None:
            raise ConflictError("Unknown experiment variant.")
        if parent.state == "promoted" and parent.promotion_version_id is not None:
            child = repo.version(root_id, parent.promotion_version_id)
            if child.configuration["variants"][0]["key"] != body.variant_key:
                raise ConflictError("Promotion already selected a different variant.")
            return self.view(child)
        self._revision(parent, body.expected_revision)
        if parent.state != "completed" or config.mode is not ExperimentMode.EXPLORATION:
            raise ConflictError("Only completed Exploration can promote to fresh Validation.")
        if repo.counts(parent).get(selected.key, 0) < config.sample_target.minimum:
            raise ConflictError(
                "Selected variant has an insufficient independent sample.",
                code="experiment_insufficient_sample",
            )
        promoted = ExperimentConfiguration.model_validate(
            {
                **config.model_dump(),
                "mode": ExperimentMode.VALIDATION,
                "strategy_version_id": selected.strategy_version_id,
                "variants": (selected,),
            }
        )
        child = self._new_version(tenant, repo.root(root_id), promoted, parent=parent)
        parent.promoted_at, parent.promotion_version_id = self.clock(), child.id
        self._advance(tenant, parent, "promoted")
        return self.view(child)

    def record_sample(
        self, tenant: TenantContext, root_id: UUID, identity: UUID, body: ExperimentSampleCreate
    ) -> ExperimentSample:
        self._mutation(tenant)
        repo = ExperimentRepository(self.session, tenant)
        row = repo.version(root_id, identity, lock=True)
        if self.source_resolver is None:
            raise AppError(
                "Trusted experiment source adapter is unavailable.",
                status_code=503,
                code="experiment_source_adapter_unavailable",
            )
        config = ExperimentConfiguration.model_validate(row.configuration)
        variant = next((v for v in config.variants if v.key == body.variant_key), None)
        if variant is None:
            raise ConflictError("Unknown experiment variant.")
        proof = self.source_resolver.resolve(
            self.view(row), body.variant_key, body.source_record_id
        )
        binding = (
            proof.organization_id,
            proof.execution_account_id,
            proof.native_uid,
            proof.source,
            proof.version_id,
            proof.configuration_hash,
            proof.sample_group_id,
            proof.strategy_version_id,
            proof.variant_key,
            proof.kind,
            proof.source_record_id,
        )
        expected = (
            tenant.organization_id,
            config.account.execution_account_id,
            config.account.native_uid,
            config.account.source,
            row.id,
            row.configuration_hash,
            row.sample_group_id,
            variant.strategy_version_id,
            variant.key,
            config.sample_target.kind,
            body.source_record_id,
        )
        if binding != expected:
            raise ConflictError(
                "Source proof does not belong to this experiment sample.",
                code="experiment_attribution_mismatch",
            )
        digest = semantic_hash(proof.model_dump())
        existing = self.session.scalar(
            select(ExperimentSampleRow).where(
                ExperimentSampleRow.organization_id == tenant.organization_id,
                ExperimentSampleRow.source == proof.source.value,
                ExperimentSampleRow.account_scope == row.account_scope,
                ExperimentSampleRow.kind == proof.kind,
                ExperimentSampleRow.source_record_id == proof.source_record_id,
            )
        )
        if existing is not None:
            if existing.version_id != row.id or existing.evidence_hash != digest:
                raise ConflictError(
                    "Source sample is already assigned or changed.", code="experiment_sample_reused"
                )
            return self.sample_view(existing)
        if (
            row.state not in {"running", "paused"}
            or row.started_at is None
            or row.authorized_until is None
        ):
            raise ConflictError("Experiment has no active sample window.")
        event = self.session.scalar(
            select(ExperimentEventRow)
            .where(
                ExperimentEventRow.version_id == row.id,
                ExperimentEventRow.occurred_at <= proof.opened_at,
            )
            .order_by(ExperimentEventRow.revision.desc())
            .limit(1)
        )
        if (
            event is None
            or event.state != "running"
            or not (
                utc(row.started_at) <= proof.opened_at < utc(row.authorized_until)
                and proof.opened_at <= proof.completed_at <= self.clock()
            )
        ):
            raise ConflictError(
                "Sample did not begin in an approved running interval.",
                code="experiment_sample_window",
            )
        counts = repo.counts(row)
        if counts.get(variant.key, 0) >= config.sample_target.maximum or (
            proof.kind == "closed_trade"
            and sum(counts.values()) >= config.risk_limits.max_trades_total
        ):
            raise ConflictError("Experiment sample/trade cap reached.")
        sample = ExperimentSampleRow(
            version_id=row.id,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            sample_group_id=row.sample_group_id,
            variant_key=variant.key,
            source=proof.source.value,
            account_scope=row.account_scope,
            kind=proof.kind,
            source_record_id=proof.source_record_id,
            evidence_hash=digest,
            proof=proof.model_dump(mode="json"),
            opened_at=proof.opened_at,
            completed_at=proof.completed_at,
            recorded_at=self.clock(),
        )
        try:
            with self.session.begin_nested():
                self.session.add(sample)
                self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Source sample is already assigned.", code="experiment_sample_reused"
            ) from exc
        return self.sample_view(sample)

    def view(self, row: ExperimentVersionRow) -> ExperimentVersion:
        values = {
            name: getattr(row, name)
            for name in ExperimentVersion.model_fields
            if hasattr(row, name)
        }
        for name, value in values.items():
            if isinstance(value, datetime):
                values[name] = utc(value)
        counts = dict(
            self.session.execute(
                select(ExperimentSampleRow.variant_key, func.count())
                .where(ExperimentSampleRow.version_id == row.id)
                .group_by(ExperimentSampleRow.variant_key)
            )
            .tuples()
            .all()
        )
        values["sample_counts"] = {
            v["key"]: counts.get(v["key"], 0) for v in row.configuration["variants"]
        }
        return ExperimentVersion.model_validate(values)

    def sample_view(self, row: ExperimentSampleRow) -> ExperimentSample:
        values = {name: getattr(row, name) for name in ExperimentSample.model_fields}
        values["opened_at"], values["completed_at"] = utc(row.opened_at), utc(row.completed_at)
        return ExperimentSample.model_validate(values)

    def detail(self, tenant: TenantContext, root_id: UUID) -> ExperimentDetail:
        root = ExperimentRepository(self.session, tenant).root(root_id)
        rows = self.session.scalars(
            select(ExperimentVersionRow)
            .where(ExperimentVersionRow.experiment_id == root_id)
            .order_by(ExperimentVersionRow.version)
        )
        return ExperimentDetail(
            id=root.id, name=root.name, versions=[self.view(row) for row in rows]
        )

    def list(self, tenant: TenantContext, *, limit: int = 50, offset: int = 0) -> ExperimentPage:
        conditions = (
            ExperimentRow.organization_id == tenant.organization_id,
            ExperimentRow.user_id == tenant.user_id,
        )
        total = (
            self.session.scalar(select(func.count()).select_from(ExperimentRow).where(*conditions))
            or 0
        )
        roots = self.session.scalars(
            select(ExperimentRow)
            .where(*conditions)
            .order_by(ExperimentRow.created_at, ExperimentRow.id)
            .limit(limit)
            .offset(offset)
        )
        return ExperimentPage(
            items=[self.detail(tenant, root.id) for root in roots],
            total=total,
            limit=limit,
            offset=offset,
        )
