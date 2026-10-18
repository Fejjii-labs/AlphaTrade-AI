"""Persist proposals, immutable versions, grade bindings, and trade records.

The caller owns the transaction. This service flushes and does not commit,
except for a breaker cancellation of a due loosening. That row is committed
on its own so closing the read session cannot roll it back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.risk_policy import (
    RiskPolicyBreakerReset,
    RiskPolicyBreakerState,
    RiskPolicyCalibrationSample,
    RiskPolicyGradeBinding,
    RiskPolicyPromotionReportRow,
    RiskPolicyProposal,
    RiskPolicyTradeRecord,
    RiskPolicyVersion,
    RiskPolicyVersionCancellation,
)
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType, AuditResult, AuditSeverity
from app.services.audit_service import AuditService
from app.services.risk_policy.breakers import BreakerConfig, BreakerSnapshot, evaluate_breakers
from app.services.risk_policy.calibration import PromotionReport, assert_promotion_allows
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.governance import (
    HumanApplyProof,
    PolicyActor,
    PolicyChannel,
    assert_can_apply,
    assert_can_propose,
    decide_apply,
    proposal_is_expired,
)
from app.services.risk_policy.grader import GRADER_VERSION, GradeDecision
from app.services.risk_policy.profile import (
    PolicyKind,
    RiskPolicyProfile,
    canonical_profile,
    placeholder_profile,
    profile_from_canonical,
    profile_hash,
)
from app.services.risk_policy.records import TradeRiskRecord

_KIND_VALUES = {kind.value for kind in PolicyKind}


def _as_utc(moment: datetime) -> datetime:
    """SQLite returns naive UTC datetimes. Postgres returns aware ones."""

    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _pause_active(paused_until: datetime | None, now: datetime) -> bool:
    if paused_until is None:
        return False
    return _as_utc(paused_until) > now


class RiskPolicyService:
    """Read, propose, and apply grade-based risk profiles for one organization."""

    def __init__(self, session: Session, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    def read(
        self, *, organization_id: uuid.UUID, kind: PolicyKind, now: datetime | None = None
    ) -> tuple[RiskPolicyProfile, RiskPolicyVersion | None, RiskPolicyVersion | None]:
        """Return the profile in force, its version row, and a scheduled loosening if any.

        When nothing has been applied, the profile is the conservative placeholder
        and both rows are None.
        """

        moment = now or datetime.now(UTC)
        active = self._active_row(organization_id, kind, moment)
        scheduled = self._scheduled_row(organization_id, kind, moment)
        if active is None:
            return placeholder_profile(kind), None, scheduled
        return profile_from_row(active), active, scheduled

    def propose(
        self,
        *,
        organization_id: uuid.UUID,
        actor: PolicyActor,
        profile: RiskPolicyProfile,
        reason: str,
        now: datetime | None = None,
    ) -> RiskPolicyProposal:
        assert_can_propose(actor)
        self._reason(reason)
        canonical = canonical_profile(profile)
        row = RiskPolicyProposal(
            organization_id=organization_id,
            kind=profile.kind.value,
            payload=canonical,
            actor_id=actor.actor_id,
            actor_channel=actor.channel.value,
            reason=reason.strip(),
            content_hash=profile_hash(profile),
            created_at=now or datetime.now(UTC),
        )
        self._session.add(row)
        self._session.flush()
        self._audit_event(
            AuditEventType.RISK_POLICY_PROPOSED,
            organization_id=organization_id,
            actor=actor,
            resource_id=str(row.id),
            metadata={"kind": profile.kind.value, "content_hash": row.content_hash},
        )
        return row

    def apply(
        self,
        *,
        organization_id: uuid.UUID,
        proposal_id: uuid.UUID,
        actor: PolicyActor,
        proof: HumanApplyProof | None,
        owner_user_id: str,
        pinned_telegram_chat_id: str = "",
        now: datetime | None = None,
    ) -> RiskPolicyVersion:
        moment = now or datetime.now(UTC)
        proposal = self._session.get(RiskPolicyProposal, proposal_id)
        if proposal is None or proposal.organization_id != organization_id:
            raise RiskPolicyRejected("proposal_not_found")
        try:
            assert_can_apply(
                actor,
                proof,
                owner_user_id=owner_user_id,
                pinned_telegram_chat_id=pinned_telegram_chat_id,
                proposal_id=str(proposal.id),
                content_hash=proposal.content_hash,
                now=moment,
            )
        except RiskPolicyRejected as exc:
            self._audit_event(
                AuditEventType.RISK_POLICY_APPLY_REFUSED,
                organization_id=organization_id,
                actor=actor,
                resource_id=str(proposal_id),
                result=AuditResult.BLOCKED,
                metadata={"reason": exc.code},
            )
            raise
        if proposal_is_expired(_as_utc(proposal.created_at), moment):
            raise RiskPolicyRejected("proposal_expired")
        if proposal.kind not in _KIND_VALUES:
            raise RiskPolicyRejected("kind_invalid")
        profile = profile_from_canonical(
            {str(key): str(value) for key, value in proposal.payload.items()}
        )
        assert_promotion_allows(profile, self.stored_promotion_report(organization_id))
        kind = PolicyKind(proposal.kind)
        previous_row = self._active_row(organization_id, kind, moment)
        first_store = previous_row is None
        if previous_row is None:
            previous = placeholder_profile(kind)
        else:
            previous = profile_from_row(previous_row)
        decision = decide_apply(
            actor=actor,
            previous=previous,
            proposed=profile,
            now=moment,
            breaker_active=self.breaker_active(organization_id=organization_id, now=moment),
            first_store=first_store,
        )
        version_number = self._next_version_number(organization_id, kind)
        canonical = canonical_profile(profile)
        row = RiskPolicyVersion(
            organization_id=organization_id,
            proposal_id=proposal.id,
            kind=kind.value,
            version_number=version_number,
            risk_percent_a_plus=Decimal(canonical["risk_percent_a_plus"]),
            risk_percent_a=Decimal(canonical["risk_percent_a"]),
            risk_percent_b_plus=Decimal(canonical["risk_percent_b_plus"]),
            risk_percent_b=Decimal(canonical["risk_percent_b"]),
            risk_percent_c=Decimal(canonical["risk_percent_c"]),
            max_leverage=Decimal(canonical["max_leverage"]),
            daily_loss_limit_percent=Decimal(canonical["daily_loss_limit_percent"]),
            max_aggregate_open_risk_percent=Decimal(canonical["max_aggregate_open_risk_percent"]),
            max_open_positions=int(canonical["max_open_positions"]),
            max_notional_per_trade_percent=Decimal(canonical["max_notional_per_trade_percent"]),
            max_aggregate_notional_percent=Decimal(canonical["max_aggregate_notional_percent"]),
            min_stop_atr_multiple=Decimal(canonical["min_stop_atr_multiple"]),
            min_stop_absolute_floor=Decimal(canonical["min_stop_absolute_floor"]),
            max_cost_share_of_r=Decimal(canonical["max_cost_share_of_r"]),
            drawdown_breaker_percent=Decimal(canonical["drawdown_breaker_percent"]),
            consecutive_loss_limit=int(canonical["consecutive_loss_limit"]),
            autonomous_internal_paper_enabled=False,
            autonomous_blofin_demo_enabled=False,
            change_class=decision.change_class.value,
            effective_at=decision.effective_at,
            actor_id=actor.actor_id,
            actor_channel=PolicyChannel.HUMAN.value,
            reason=proposal.reason,
            diff=decision.diff,
            content_hash=profile_hash(profile),
            created_at=moment,
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise RiskPolicyRejected("version_conflict") from exc
        self._audit_event(
            AuditEventType.RISK_POLICY_APPLIED,
            organization_id=organization_id,
            actor=actor,
            resource_id=str(row.id),
            metadata={
                "kind": kind.value,
                "version_number": version_number,
                "change_class": decision.change_class.value,
                "effective_at": decision.effective_at.isoformat(),
                "proposal_id": str(proposal.id),
                "applies_to_new_trades_only": True,
            },
        )
        return row

    def breaker_active(self, *, organization_id: uuid.UUID, now: datetime) -> bool:
        rows = self._session.scalars(
            select(RiskPolicyBreakerState).where(
                RiskPolicyBreakerState.organization_id == organization_id
            )
        ).all()
        moment = _as_utc(now)
        for row in rows:
            if row.drawdown_latched:
                return True
            if _pause_active(row.daily_paused_until, moment):
                return True
            if _pause_active(row.consecutive_paused_until, moment):
                return True
        return False

    def latch_drawdown(
        self,
        *,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        equity_peak: Decimal,
        now: datetime,
    ) -> None:
        row = self._breaker_row(organization_id, account_id, equity_peak, now)
        row.drawdown_latched = True
        row.equity_peak = equity_peak
        row.updated_at = now
        self._session.flush()

    def bind_grade(
        self,
        *,
        organization_id: uuid.UUID,
        setup_id: str,
        decision: GradeDecision,
        now: datetime | None = None,
    ) -> GradeDecision:
        existing = self._session.scalar(
            select(RiskPolicyGradeBinding).where(
                RiskPolicyGradeBinding.organization_id == organization_id,
                RiskPolicyGradeBinding.setup_id == setup_id,
            )
        )
        if existing is not None:
            return _decision_from_binding(existing)
        if (
            decision.grade is None
            or decision.trend_score is None
            or decision.momentum_score is None
        ):
            raise RiskPolicyRejected(decision.refusal or "ungraded")
        row = RiskPolicyGradeBinding(
            organization_id=organization_id,
            setup_id=setup_id,
            grade=decision.grade.value,
            grader_version=decision.grader_version,
            trend_score=format(decision.trend_score, "f"),
            momentum_score=format(decision.momentum_score, "f"),
            refusal=decision.refusal,
            created_at=now or datetime.now(UTC),
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError:
            winner = self._session.scalar(
                select(RiskPolicyGradeBinding).where(
                    RiskPolicyGradeBinding.organization_id == organization_id,
                    RiskPolicyGradeBinding.setup_id == setup_id,
                )
            )
            if winner is None:
                raise RiskPolicyRejected("grade_bind_conflict") from None
            return _decision_from_binding(winner)
        return decision

    def record_trade(
        self,
        *,
        organization_id: uuid.UUID,
        trade_id: str,
        record: TradeRiskRecord,
        now: datetime | None = None,
    ) -> RiskPolicyTradeRecord:
        row = RiskPolicyTradeRecord(
            organization_id=organization_id,
            trade_id=trade_id,
            profile_version_id=record.profile_version_id,
            grade=record.grade.value,
            grader_version=record.grader_version,
            adjusters=record.adjusters,
            calculation=record.calculation,
            ai_explanation=record.ai_explanation,
            created_at=now or datetime.now(UTC),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def add_calibration_sample(
        self,
        *,
        organization_id: uuid.UUID,
        grade: str,
        grader_version: str,
        r_multiple: Decimal,
        in_sample: bool,
        walk_forward_fold: int,
        slippage_r: Decimal,
        now: datetime | None = None,
    ) -> None:
        self._session.add(
            RiskPolicyCalibrationSample(
                organization_id=organization_id,
                grade=grade,
                grader_version=grader_version,
                r_multiple=r_multiple,
                in_sample=in_sample,
                walk_forward_fold=walk_forward_fold,
                slippage_r=slippage_r,
                created_at=now or datetime.now(UTC),
            )
        )
        self._session.flush()

    def _active_row(
        self, organization_id: uuid.UUID, kind: PolicyKind, now: datetime
    ) -> RiskPolicyVersion | None:
        rows = self._session.scalars(
            select(RiskPolicyVersion)
            .where(
                RiskPolicyVersion.organization_id == organization_id,
                RiskPolicyVersion.kind == kind.value,
                RiskPolicyVersion.effective_at <= now,
            )
            .order_by(RiskPolicyVersion.version_number.desc())
        ).all()
        cancelled = set(
            self._session.scalars(
                select(RiskPolicyVersionCancellation.version_id).where(
                    RiskPolicyVersionCancellation.organization_id == organization_id
                )
            ).all()
        )
        for row in rows:
            if row.id in cancelled:
                continue
            if row.change_class == "loosening" and self.breaker_active(
                organization_id=organization_id, now=now
            ):
                self._cancel_version(row, now)
                continue
            return row
        return None

    def _cancel_version(self, row: RiskPolicyVersion, now: datetime) -> None:
        """Cancel a due loosening and commit that row.

        The profile read path does not commit, and closing that session rolls
        back. The cancellation has to survive that rollback, and it stays
        cancelled after the breaker clears. A new proposal is required.
        """

        already = self._session.scalar(
            select(RiskPolicyVersionCancellation.id).where(
                RiskPolicyVersionCancellation.version_id == row.id
            )
        )
        if already is not None:
            return
        self._session.add(
            RiskPolicyVersionCancellation(
                organization_id=row.organization_id,
                version_id=row.id,
                reason="breaker_active_at_activation",
                created_at=now,
            )
        )
        self._session.commit()

    def stored_promotion_report(self, organization_id: uuid.UUID) -> PromotionReport | None:
        row = self._session.scalar(
            select(RiskPolicyPromotionReportRow)
            .where(
                RiskPolicyPromotionReportRow.organization_id == organization_id,
                RiskPolicyPromotionReportRow.grader_version == GRADER_VERSION,
            )
            .limit(1)
        )
        if row is None:
            return None
        return PromotionReport(
            grader_version=row.grader_version,
            grades=(),
            ordered=row.ordered,
            walk_forward_ok=row.walk_forward_ok,
            eligible=row.eligible,
            auto_demotion=(),
            promotion_applied=False,
        )

    def store_promotion_report(
        self,
        *,
        organization_id: uuid.UUID,
        report: PromotionReport,
        now: datetime,
    ) -> None:
        if report.grader_version != GRADER_VERSION or not report.eligible:
            raise RiskPolicyRejected("promotion_report_not_eligible")
        self._session.add(
            RiskPolicyPromotionReportRow(
                organization_id=organization_id,
                grader_version=report.grader_version,
                eligible=report.eligible,
                ordered=report.ordered,
                walk_forward_ok=report.walk_forward_ok,
                payload={"grader_version": report.grader_version, "eligible": report.eligible},
                created_at=now,
            )
        )
        self._session.flush()

    def record_breaker(
        self,
        *,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        equity_peak: Decimal,
        consecutive_losses: int,
        realized_pnl: Decimal,
        unrealized_pnl: Decimal,
        daily_paused_until: datetime | None,
        consecutive_paused_until: datetime | None,
        drawdown_latched: bool,
        now: datetime,
    ) -> RiskPolicyBreakerState:
        row = self._breaker_row(organization_id, account_id, equity_peak, now)
        row.equity_peak = equity_peak
        row.consecutive_losses = consecutive_losses
        row.realized_pnl = realized_pnl
        row.unrealized_pnl = unrealized_pnl
        row.daily_paused_until = daily_paused_until
        row.consecutive_paused_until = consecutive_paused_until
        row.drawdown_latched = drawdown_latched
        row.updated_at = now
        self._session.flush()
        return row

    def reset_drawdown_latch(
        self,
        *,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        actor: PolicyActor,
        owner_user_id: str,
        equity: Decimal,
        drawdown_breaker_percent: Decimal,
        now: datetime,
    ) -> None:
        """Clear the drawdown latch only after equity is back inside the threshold."""

        if actor.channel in {PolicyChannel.AGENT, PolicyChannel.BOT, PolicyChannel.SERVICE}:
            raise RiskPolicyRejected("drawdown_reset_requires_human")
        if not owner_user_id or actor.actor_id != owner_user_id:
            raise RiskPolicyRejected("apply_owner_mismatch")
        row = self._session.scalar(
            select(RiskPolicyBreakerState).where(
                RiskPolicyBreakerState.organization_id == organization_id,
                RiskPolicyBreakerState.account_id == account_id,
            )
        )
        if row is None or not row.drawdown_latched:
            raise RiskPolicyRejected("drawdown_not_latched")
        verdict = evaluate_breakers(
            BreakerConfig(
                daily_loss_limit_percent=Decimal("100"),
                drawdown_breaker_percent=drawdown_breaker_percent,
                consecutive_loss_limit=row.consecutive_losses + 1,
            ),
            BreakerSnapshot(
                equity=equity,
                equity_peak=row.equity_peak,
                reference_equity=row.equity_peak,
                realized_pnl_today=(
                    row.realized_pnl if row.realized_pnl is not None else Decimal("0")
                ),
                unrealized_pnl_today=(
                    row.unrealized_pnl if row.unrealized_pnl is not None else Decimal("0")
                ),
                consecutive_losses=0,
                drawdown_latched=False,
                now=now,
            ),
        )
        if verdict.drawdown_active:
            raise RiskPolicyRejected("drawdown_still_active")
        self._session.add(
            RiskPolicyBreakerReset(
                organization_id=organization_id,
                account_id=account_id,
                actor_id=actor.actor_id,
                actor_channel=actor.channel.value,
                created_at=now,
            )
        )
        row.drawdown_latched = False
        row.updated_at = now
        self._session.flush()

    def _scheduled_row(
        self, organization_id: uuid.UUID, kind: PolicyKind, now: datetime
    ) -> RiskPolicyVersion | None:
        return self._session.scalar(
            select(RiskPolicyVersion)
            .where(
                RiskPolicyVersion.organization_id == organization_id,
                RiskPolicyVersion.kind == kind.value,
                RiskPolicyVersion.effective_at > now,
            )
            .order_by(RiskPolicyVersion.version_number.asc())
            .limit(1)
        )

    def _next_version_number(self, organization_id: uuid.UUID, kind: PolicyKind) -> int:
        current = self._session.scalar(
            select(RiskPolicyVersion.version_number)
            .where(
                RiskPolicyVersion.organization_id == organization_id,
                RiskPolicyVersion.kind == kind.value,
            )
            .order_by(RiskPolicyVersion.version_number.desc())
            .limit(1)
        )
        return int(current or 0) + 1

    def _breaker_row(
        self,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        equity_peak: Decimal,
        now: datetime,
    ) -> RiskPolicyBreakerState:
        row = self._session.scalar(
            select(RiskPolicyBreakerState).where(
                RiskPolicyBreakerState.organization_id == organization_id,
                RiskPolicyBreakerState.account_id == account_id,
            )
        )
        if row is not None:
            return row
        row = RiskPolicyBreakerState(
            organization_id=organization_id,
            account_id=account_id,
            equity_peak=equity_peak,
            drawdown_latched=False,
            consecutive_losses=0,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def _audit_event(
        self,
        event_type: AuditEventType,
        *,
        organization_id: uuid.UUID,
        actor: PolicyActor,
        resource_id: str,
        metadata: dict[str, object],
        result: AuditResult = AuditResult.SUCCESS,
    ) -> None:
        actor_type = {
            PolicyChannel.HUMAN: ActorType.USER,
            PolicyChannel.UNDECLARED: ActorType.USER,
            PolicyChannel.AGENT: ActorType.AGENT,
            PolicyChannel.BOT: ActorType.TOOL,
            PolicyChannel.SERVICE: ActorType.TOOL,
        }[actor.channel]
        self._audit.record(
            AuditRecordCreate(
                request_id="risk-policy",
                trace_id="risk-policy",
                event_type=event_type,
                resource_type="risk_policy",
                actor_type=actor_type,
                result=result,
                severity=AuditSeverity.INFO
                if result is AuditResult.SUCCESS
                else AuditSeverity.HIGH,
                organization_id=organization_id,
                resource_id=resource_id,
                metadata=metadata,
            )
        )

    @staticmethod
    def _reason(reason: str) -> None:
        text = reason.strip()
        if len(text) < 3 or len(text) > 500:
            raise RiskPolicyRejected("reason_invalid")


def profile_from_row(row: RiskPolicyVersion) -> RiskPolicyProfile:
    return profile_from_canonical(
        {
            "kind": row.kind,
            "risk_percent_a_plus": format(row.risk_percent_a_plus, "f"),
            "risk_percent_a": format(row.risk_percent_a, "f"),
            "risk_percent_b_plus": format(row.risk_percent_b_plus, "f"),
            "risk_percent_b": format(row.risk_percent_b, "f"),
            "risk_percent_c": format(row.risk_percent_c, "f"),
            "max_leverage": format(row.max_leverage, "f"),
            "daily_loss_limit_percent": format(row.daily_loss_limit_percent, "f"),
            "max_aggregate_open_risk_percent": format(row.max_aggregate_open_risk_percent, "f"),
            "max_open_positions": str(row.max_open_positions),
            "max_notional_per_trade_percent": format(row.max_notional_per_trade_percent, "f"),
            "max_aggregate_notional_percent": format(row.max_aggregate_notional_percent, "f"),
            "min_stop_atr_multiple": format(row.min_stop_atr_multiple, "f"),
            "min_stop_absolute_floor": format(row.min_stop_absolute_floor, "f"),
            "max_cost_share_of_r": format(row.max_cost_share_of_r, "f"),
            "drawdown_breaker_percent": format(row.drawdown_breaker_percent, "f"),
            "consecutive_loss_limit": str(row.consecutive_loss_limit),
            "autonomous_internal_paper_enabled": (
                "true" if row.autonomous_internal_paper_enabled else "false"
            ),
            "autonomous_blofin_demo_enabled": (
                "true" if row.autonomous_blofin_demo_enabled else "false"
            ),
        }
    )


def _decision_from_binding(row: RiskPolicyGradeBinding) -> GradeDecision:
    from app.services.risk_policy.profile import Grade

    return GradeDecision(
        grade=Grade(row.grade),
        grader_version=row.grader_version,
        trend_score=Decimal(row.trend_score),
        momentum_score=Decimal(row.momentum_score),
        refusal=row.refusal,
    )
