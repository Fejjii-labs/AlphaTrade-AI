"""Bounded research work on the existing worker; no execution or sample admission."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.experiments.identity import require_account, utc
from app.experiments.models import ExperimentVersionRow
from app.experiments.service import ExperimentService
from app.schemas.common import MembershipRole
from app.schemas.experiments import ExperimentFamily, ExperimentState, ExperimentVersion
from app.schemas.trendpulse_screening import TrendPulseScreeningCreate, TrendPulseScreeningDetail
from app.security.tenant import TenantContext
from app.strategy_brain.trendpulse_1r.contracts import TRENDPULSE_NAMESPACE
from app.strategy_brain.trendpulse_screening.models import TrendPulseScreeningRow
from app.strategy_brain.trendpulse_screening.service import TrendPulseScreeningService


def select_variant(version: ExperimentVersion, attempts: dict[str, int]) -> str | None:
    """Exploration balances declared variants; Validation freezes its one variant.

    Every persisted attempt counts toward the finite acquisition cap, including
    provider failures/no-setups. It never counts as an accepted performance sample.
    """
    eligible = [
        v.key
        for v in version.configuration.variants
        if attempts.get(v.key, 0) < version.configuration.sample_target.maximum
    ]
    return min(eligible, key=lambda key: (attempts.get(key, 0), key)) if eligible else None


class ExperimentResearchScheduler:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        screening: TrendPulseScreeningService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session, self.settings = session, settings
        self.clock = clock or (lambda: datetime.now(UTC))
        self.screening = screening or TrendPulseScreeningService(
            session, settings, clock=self.clock
        )

    def tick(self, tenant: TenantContext, *, limit: int = 2) -> list[TrendPulseScreeningDetail]:
        """At most two screens, latest closed event only, stable durable request IDs.

        No catch-up/backdating or hidden retries. Existing approval, expiry, exact
        account proof and disabled/advisory budgets are rechecked on every call.
        Caller commits; PostgreSQL screening locks fence independent workers.
        """
        if not 1 <= limit <= 2:
            raise ValueError("Research tick limit must be 1..2")
        if not self.settings.trendpulse_screening_enabled:
            return []
        now = self.clock().astimezone(UTC)
        trigger = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0)
        if not trigger + timedelta(seconds=5) <= now < trigger + timedelta(seconds=60):
            return []
        rows = self.session.scalars(
            select(ExperimentVersionRow)
            .where(
                ExperimentVersionRow.organization_id == tenant.organization_id,
                ExperimentVersionRow.user_id == tenant.user_id,
                ExperimentVersionRow.state == ExperimentState.RUNNING.value,
            )
            .order_by(ExperimentVersionRow.started_at, ExperimentVersionRow.id)
            .limit(32)
        ).all()
        results: list[TrendPulseScreeningDetail] = []
        domain = ExperimentService(self.session, self.settings, clock=self.clock)
        for row in rows:
            if len(results) >= limit:
                break
            version = domain.view(row)
            config = version.configuration
            if (
                config.family is not ExperimentFamily.TRENDPULSE_1R
                or config.model_policy.mode != "disabled"
                or version.authorized_until is None
                or version.started_at is None
                or not utc(version.started_at) <= trigger < now < utc(version.authorized_until)
            ):
                continue
            # Current role and sealed authored version are verified by the screening producer.
            try:
                require_account(self.session, tenant, config.account, self.settings, now=now)
            except AppError:
                continue
            attempts: dict[str, int] = dict(
                self.session.execute(
                    select(
                        TrendPulseScreeningRow.variant_key,
                        func.count(),
                    )
                    .where(TrendPulseScreeningRow.version_id == version.id)
                    .group_by(TrendPulseScreeningRow.variant_key)
                )
                .tuples()
                .all()
            )
            # Select unattempted variants of this event, then balance total attempts.
            used = set(
                self.session.scalars(
                    select(TrendPulseScreeningRow.variant_key).where(
                        TrendPulseScreeningRow.version_id == version.id,
                        TrendPulseScreeningRow.trigger_end == trigger,
                    )
                ).all()
            )
            capped = {**attempts, **dict.fromkeys(used, config.sample_target.maximum)}
            key = select_variant(version, capped)
            if key is None:
                continue
            body = TrendPulseScreeningCreate(
                request_id=uuid5(
                    TRENDPULSE_NAMESPACE, f"schedule:{version.id}:{key}:{trigger.isoformat()}"
                ),
                variant_key=key,
                trigger_end=trigger,
            )
            try:
                with self.session.begin_nested():
                    result = self.screening.screen(tenant, version.experiment_id, version.id, body)
                    results.append(result)
            except AppError:
                # One busy/blocked provider/version cannot stop independent work.
                continue
        return results


def tick_existing_worker(session: Session, settings: Settings) -> int:
    """Research only; invoked in the existing watcher cycle, with its opt-in intact."""
    if not settings.worker_enabled or not settings.trendpulse_screening_enabled:
        return 0
    from app.db.models import Membership

    owners = session.execute(
        select(
            ExperimentVersionRow.organization_id,
            ExperimentVersionRow.user_id,
        )
        .join(
            Membership,
            (Membership.organization_id == ExperimentVersionRow.organization_id)
            & (Membership.user_id == ExperimentVersionRow.user_id),
        )
        .where(
            ExperimentVersionRow.state == "running",
            Membership.role.in_([MembershipRole.OWNER, MembershipRole.TRADER]),
        )
        .distinct()
        .order_by(ExperimentVersionRow.organization_id, ExperimentVersionRow.user_id)
        .limit(16)
    ).all()
    count = 0
    scheduler = ExperimentResearchScheduler(session, settings)
    for organization_id, user_id in owners:
        if count >= 2:
            break
        tenant = TenantContext(
            organization_id=organization_id,
            user_id=user_id,
            email="",
            membership_role=MembershipRole.TRADER,
        )
        count += len(scheduler.tick(tenant, limit=2 - count))
    return count
