"""Immutable risk-policy versions, proposals, grades, and trade records.

Versions, proposals, grade bindings, trade records, calibration samples, and
breaker intervals are append-only. Breaker state is the one mutable table: it
is the live latch, not the audit of a profile change. Intervals keep the
windows that latch was open, so a later read can evaluate a past moment.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin

_MONEY = Numeric(20, 8)


# Kept in the migration as well. Tests compare the two texts.
def _kind_risk_check(column: str) -> str:
    """5% only for paper and demo. Every other kind stays at 2%."""

    paper = "kind IN ('internal_paper', 'demo')"
    other = "kind NOT IN ('internal_paper', 'demo')"
    return (
        f"(({paper} AND {column} > 0 AND {column} <= 5) OR "
        f"({other} AND {column} > 0 AND {column} <= 2))"
    )


# Live and non-paper kinds stay at 2%. Paper and demo may reach 5% (AT-ADR-110).
VERSION_CHECKS: tuple[str, ...] = (
    _kind_risk_check("risk_percent_a_plus"),
    _kind_risk_check("risk_percent_a"),
    _kind_risk_check("risk_percent_b_plus"),
    _kind_risk_check("risk_percent_b"),
    "risk_percent_c = 0",
    "max_leverage >= 1 AND max_leverage <= 20",
    "daily_loss_limit_percent > 0 AND daily_loss_limit_percent <= 6",
    "max_aggregate_open_risk_percent > 0 AND max_aggregate_open_risk_percent <= 6",
    "max_aggregate_notional_percent > max_notional_per_trade_percent",
    "max_cost_share_of_r > 0 AND max_cost_share_of_r <= 0.25",
    "actor_channel = 'human'",
    "change_class IN ('initial', 'tightening', 'loosening')",
    "default_leverage >= 1 AND default_leverage <= max_leverage AND default_leverage <= 20",
    "(paper_demo_data_collection = false OR kind IN ('internal_paper', 'demo'))",
)


class RiskPolicyProposal(UUIDPrimaryKeyMixin, Base):
    """An agent's or a human's request. Applying it inserts a version; this row stays."""

    __tablename__ = "risk_policy_proposals"
    __table_args__ = (
        CheckConstraint(
            "actor_channel IN ('human', 'agent', 'bot')",
            name="actor_channel",
        ),
        Index("ix_risk_policy_proposals_org_kind", "organization_id", "kind"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_channel: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskPolicyVersion(UUIDPrimaryKeyMixin, Base):
    """One immutable applied profile. The active row is the highest version already in effect."""

    __tablename__ = "risk_policy_versions"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_risk_policy_versions_proposal"),
        UniqueConstraint(
            "organization_id",
            "kind",
            "version_number",
            name="uq_risk_policy_versions_number",
        ),
        *(CheckConstraint(text, name=f"v{index}") for index, text in enumerate(VERSION_CHECKS)),
        Index("ix_risk_policy_versions_effective", "organization_id", "kind", "effective_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("risk_policy_proposals.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_percent_a_plus: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    risk_percent_a: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    risk_percent_b_plus: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    risk_percent_b: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    risk_percent_c: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    max_leverage: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    daily_loss_limit_percent: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    max_aggregate_open_risk_percent: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    max_open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    max_notional_per_trade_percent: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    max_aggregate_notional_percent: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    min_stop_atr_multiple: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    min_stop_absolute_floor: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    max_cost_share_of_r: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    drawdown_breaker_percent: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    consecutive_loss_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    autonomous_internal_paper_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    autonomous_blofin_demo_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    profile_name: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="", default=""
    )
    default_leverage: Mapped[Decimal] = mapped_column(
        _MONEY, nullable=False, server_default="1", default=Decimal("1")
    )
    paper_demo_data_collection: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    change_class: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_channel: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    diff: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskPolicyGradeBinding(UUIDPrimaryKeyMixin, Base):
    """The first grade for a setup. A later grade does not replace it."""

    __tablename__ = "risk_policy_grade_bindings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "setup_id",
            name="uq_risk_policy_grade_binding_setup",
        ),
        CheckConstraint(
            "grade IN ('A+', 'A', 'B+', 'B', 'C')",
            name="grade",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    setup_id: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    grader_version: Mapped[str] = mapped_column(String(64), nullable=False)
    trend_score: Mapped[str] = mapped_column(String(32), nullable=False)
    momentum_score: Mapped[str] = mapped_column(String(32), nullable=False)
    refusal: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskPolicyTradeRecord(UUIDPrimaryKeyMixin, Base):
    """Profile version, grade, adjusters, and the effective-risk calculation for one trade."""

    __tablename__ = "risk_policy_trade_records"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "trade_id",
            name="uq_risk_policy_trade_record",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    trade_id: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("risk_policy_versions.id"), nullable=False
    )
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    grader_version: Mapped[str] = mapped_column(String(64), nullable=False)
    adjusters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    calculation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ai_explanation: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskPolicyBreakerInterval(UUIDPrimaryKeyMixin, Base):
    """One opening or closing of a daily, consecutive, or drawdown window."""

    __tablename__ = "risk_policy_breaker_intervals"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "account_id",
            "kind",
            "phase",
            "occurred_at",
            name="uq_risk_policy_breaker_interval",
        ),
        CheckConstraint(
            "kind IN ('daily', 'consecutive', 'drawdown')",
            name="ck_risk_policy_breaker_interval_kind",
        ),
        CheckConstraint(
            "phase IN ('open', 'close')",
            name="ck_risk_policy_breaker_interval_phase",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    phase: Mapped[str] = mapped_column(String(8), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskPolicyBreakerState(UUIDPrimaryKeyMixin, Base):
    """Live breaker latch. Not a profile version."""

    __tablename__ = "risk_policy_breaker_states"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "account_id",
            name="uq_risk_policy_breaker_account",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    equity_peak: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    drawdown_latched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    realized_pnl: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    daily_paused_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_paused_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskPolicyCalibrationSample(UUIDPrimaryKeyMixin, Base):
    """One out-of-sample (or in-sample) R observation. Append-only."""

    __tablename__ = "risk_policy_calibration_samples"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    grade: Mapped[str] = mapped_column(String(8), nullable=False)
    grader_version: Mapped[str] = mapped_column(String(64), nullable=False)
    r_multiple: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    in_sample: Mapped[bool] = mapped_column(Boolean, nullable=False)
    walk_forward_fold: Mapped[int] = mapped_column(Integer, nullable=False)
    slippage_r: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskPolicyVersionCancellation(UUIDPrimaryKeyMixin, Base):
    """A due loosening that was cancelled because a breaker was active."""

    __tablename__ = "risk_policy_version_cancellations"
    __table_args__ = (UniqueConstraint("version_id", name="uq_risk_policy_version_cancellation"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("risk_policy_versions.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskPolicyPromotionReportRow(UUIDPrimaryKeyMixin, Base):
    """Stored promotion eligibility for one grader version. Append-only."""

    __tablename__ = "risk_policy_promotion_reports"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "grader_version",
            name="uq_risk_policy_promotion_grader",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    grader_version: Mapped[str] = mapped_column(String(64), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ordered: Mapped[bool] = mapped_column(Boolean, nullable=False)
    walk_forward_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskPolicyBreakerReset(UUIDPrimaryKeyMixin, Base):
    """A human reset of the drawdown latch. Append-only, and it names the actor."""

    __tablename__ = "risk_policy_breaker_resets"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_channel: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _reject_mutation(_mapper: object, _connection: object, _target: object) -> None:
    raise ValueError("Risk-policy audit rows are immutable.")


def _validate_version_insert(
    _mapper: object, _connection: object, target: RiskPolicyVersion
) -> None:
    """Reject an out-of-ceiling version at insert time. Do not clamp."""

    from app.services.risk_policy.profile import (
        Grade,
        PolicyKind,
        RiskPolicyProfile,
        validate_profile,
    )

    validate_profile(
        RiskPolicyProfile(
            kind=PolicyKind(target.kind),
            risk_percent_by_grade={
                Grade.A_PLUS: target.risk_percent_a_plus,
                Grade.A: target.risk_percent_a,
                Grade.B_PLUS: target.risk_percent_b_plus,
                Grade.B: target.risk_percent_b,
                Grade.C: target.risk_percent_c,
            },
            max_leverage=target.max_leverage,
            daily_loss_limit_percent=target.daily_loss_limit_percent,
            max_aggregate_open_risk_percent=target.max_aggregate_open_risk_percent,
            max_open_positions=target.max_open_positions,
            max_notional_per_trade_percent=target.max_notional_per_trade_percent,
            max_aggregate_notional_percent=target.max_aggregate_notional_percent,
            min_stop_atr_multiple=target.min_stop_atr_multiple,
            min_stop_absolute_floor=target.min_stop_absolute_floor,
            max_cost_share_of_r=target.max_cost_share_of_r,
            drawdown_breaker_percent=target.drawdown_breaker_percent,
            consecutive_loss_limit=target.consecutive_loss_limit,
            autonomous_internal_paper_enabled=target.autonomous_internal_paper_enabled,
            autonomous_blofin_demo_enabled=target.autonomous_blofin_demo_enabled,
            profile_name=target.profile_name,
            default_leverage=target.default_leverage,
            paper_demo_data_collection=target.paper_demo_data_collection,
        )
    )


event.listen(RiskPolicyVersion, "before_insert", _validate_version_insert)


for _model in (
    RiskPolicyProposal,
    RiskPolicyVersion,
    RiskPolicyGradeBinding,
    RiskPolicyTradeRecord,
    RiskPolicyCalibrationSample,
    RiskPolicyVersionCancellation,
    RiskPolicyPromotionReportRow,
    RiskPolicyBreakerReset,
    RiskPolicyBreakerInterval,
):
    event.listen(_model, "before_update", _reject_mutation)
    event.listen(_model, "before_delete", _reject_mutation)
