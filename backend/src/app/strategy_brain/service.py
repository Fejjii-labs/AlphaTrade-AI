"""Bounded tenant reads and explicit library template creation. No provider calls."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import JournalTrade, UserStrategy
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.common import StrategyId, StrategyLifecycleState
from app.schemas.nested_continuation import (
    NESTED_KIND,
    NestedContinuationSpec,
    StrategyBrainDefinition,
)
from app.schemas.strategy_library import StrategyCard, UserStrategyCreate
from app.services.canonical_serialization import canonical_sha256
from app.services.strategy_library_service import StrategyLibraryService
from app.services.strategy_versioning import StrategyVersioningService
from app.strategy_brain.records import aware
from app.strategy_brain.sfp.contracts import SFP_KIND, SfpSpec


def template_name(spec: NestedContinuationSpec | SfpSpec) -> str:
    """Shared identity for a preview and explicit idempotent template creation."""
    signature = canonical_sha256(spec.model_dump(mode="json"))[:12]
    family_name = "SFP" if isinstance(spec, SfpSpec) else "Nested"
    return (
        f"{family_name} {spec.symbol} {spec.direction.value} "
        f"{spec.trigger_timeframe.value} {signature}"
    )


def create_template(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    spec: NestedContinuationSpec | SfpSpec,
) -> dict[str, Any]:
    name = template_name(spec)
    sfp = isinstance(spec, SfpSpec)
    existing = session.scalar(
        select(UserStrategy).where(
            UserStrategy.organization_id == organization_id,
            UserStrategy.user_id == user_id,
            UserStrategy.name == name,
        )
    )
    if existing is not None:
        version = StrategyVersioningService(session).selected_version(existing)
        if version is not None and version.pattern_spec == spec.model_dump(mode="json"):
            latest = StrategyVersioningService(session).latest_lifecycle_event_for_version(
                version.id
            )
            return {
                "strategy_id": existing.id,
                "version_id": version.id,
                "version": version.version,
                "lifecycle": latest.new_state.value if latest else "draft",
                "execution_permission": "existing_paper_gates_only",
                "parameters": spec.parameters.model_dump(mode="json"),
            }
    card = StrategyCard(
        strategy_name=name,
        asset_universe=[spec.symbol],
        timeframes=[spec.trigger_timeframe],
        entry_conditions=["Closed break of the impulse extreme after a controlled pullback"],
        confirmation_conditions=["Causal confirmed pullback pivot; fresh closed OHLCV"],
        invalidation=["Structural failure or maximum retracement"],
        stop_loss=["Controlled pullback extreme"],
        take_profit_plan=["Measured impulse; 3R/4R only when supported, never forced"],
        position_sizing=["Existing paper risk sizing and canonical limits"],
        brain=StrategyBrainDefinition(
            family="nested_continuation",
            structure_conditions=["impulse", "controlled pullback", "closed continuation"],
            setup_conditions=[
                "Required evidence AVAILABLE",
                "immutable explicitly approved version",
            ],
        ),
    )
    if sfp:
        card = card.model_copy(
            update={
                "entry_conditions": [
                    "Closed break of reclaim extreme after a causal structural sweep"
                ],
                "confirmation_conditions": ["Confirmed reclaim; fresh final canonical OHLCV"],
                "invalidation": ["Failed reclaim, breakout or structural failure"],
                "stop_loss": ["No SFP execution plan is authorized by this foundation"],
                "take_profit_plan": ["Available structural target space is advisory evidence only"],
                "brain": StrategyBrainDefinition(
                    family="sfp",
                    market_regime="structural liquidity sweep and reclaim",
                    structure_conditions=[
                        "important high or low",
                        "sweep",
                        "reclaim",
                        "confirmation",
                    ],
                    setup_conditions=["Required evidence AVAILABLE", "immutable approved version"],
                    alert_rules=[],
                ),
            }
        )
    strategy = StrategyLibraryService(session).create(
        UserStrategyCreate(
            organization_id=organization_id,
            user_id=user_id,
            name=card.strategy_name,
            setup_type=StrategyId.SFP if sfp else StrategyId.NESTED_CONTINUATION,
            card=card,
            pattern_spec=spec.model_dump(mode="json"),
        )
    )
    row = session.get(UserStrategy, strategy.id)
    assert row is not None
    version = StrategyVersioningService(session).selected_version(row)
    assert version is not None
    return {
        "strategy_id": strategy.id,
        "version_id": version.id,
        "version": version.version,
        "lifecycle": "draft",
        "execution_permission": "none",
        "parameters": spec.parameters.model_dump(mode="json"),
    }


def setup_view(session: Session, row: BrainSetupRow, *, now: datetime) -> dict[str, Any]:
    payload = dict(row.payload)
    observed = aware(row.observed_at)
    timeframe = payload.get("timeframe", "15m")
    from app.market_contracts.identity import interval_timedelta
    from app.schemas.common import Timeframe

    fresh_until = observed + interval_timedelta(Timeframe(timeframe))
    availability = "AVAILABLE"
    if payload.get("family") == "sfp":
        evidence_end = datetime.fromisoformat(payload["evidence_close_at"])
        fresh_until = (
            evidence_end
            + interval_timedelta(Timeframe(timeframe)) * payload["required_evidence_max_age_bars"]
        )
        availability = payload.get("required_evidence", "MISSING")
    fresh = observed <= now < fresh_until and availability == "AVAILABLE"
    quality = "AVAILABLE" if fresh else availability if availability != "AVAILABLE" else "STALE"
    expired = now >= aware(row.expires_at)
    journal = None
    if row.journal_trade_id:
        trade = session.scalar(
            select(JournalTrade).where(
                JournalTrade.id == row.journal_trade_id,
                JournalTrade.organization_id == row.organization_id,
            )
        )
        if trade:
            journal = {
                "id": str(trade.id),
                "status": trade.status.value,
                "net_pnl": str(trade.net_pnl) if trade.net_pnl is not None else None,
            }
    return {
        **payload,
        "setup_id": str(row.id),
        "symbol": row.symbol,
        "state": "EXPIRED"
        if expired and row.state not in {"COMPLETED", "INVALIDATED"}
        else row.state,
        "freshness": quality,
        "fresh_until": fresh_until.isoformat(),
        "data_quality": quality,
        "evidence": {
            **payload.get("evidence", {}),
            "closed_ohlcv": quality,
            "volume": quality,
        },
        "observed_at": observed.isoformat(),
        "expires_at": aware(row.expires_at).isoformat(),
        "journal": journal,
        "candidate_id": str(row.candidate_id) if row.candidate_id else None,
        "assessment_id": str(row.assessment_id) if row.assessment_id else None,
        "decision_id": str(row.decision_id) if row.decision_id else None,
        "current_market_claim": False,
        "historical_expectancy": "insufficient_history",
    }


def overview(
    session: Session,
    *,
    organization_id: UUID,
    symbol: str | None = None,
    user_id: UUID | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    config = WatcherWatchlistRepository(session).load(organization_id)
    stmt = select(BrainSetupRow).where(BrainSetupRow.organization_id == organization_id)
    if symbol:
        stmt = stmt.where(BrainSetupRow.symbol == symbol.strip().upper())
    rows = session.scalars(
        stmt.order_by(BrainSetupRow.observed_at.desc(), BrainSetupRow.id).limit(20)
    ).all()
    versions = []
    strategy_query = select(UserStrategy).where(UserStrategy.organization_id == organization_id)
    if user_id is not None:
        strategy_query = strategy_query.where(UserStrategy.user_id == user_id)
    for strategy in session.scalars(strategy_query.order_by(UserStrategy.id).limit(50)):
        version = StrategyVersioningService(session).selected_version(strategy)
        if (
            version is None
            or not version.pattern_spec
            or version.pattern_spec.get("kind") not in {NESTED_KIND, SFP_KIND}
        ):
            continue
        lifecycle = StrategyVersioningService(session).latest_lifecycle_event_for_version(
            version.id
        )
        approval = (
            lifecycle
            if lifecycle and lifecycle.new_state == StrategyLifecycleState.APPROVED
            else None
        )
        versions.append(
            {
                "strategy_id": str(strategy.id),
                "version_id": str(version.id),
                "version": version.version,
                "name": strategy.name,
                "spec": version.pattern_spec,
                "rules": {
                    key: version.card.get(key, [])
                    for key in ("entry_conditions", "invalidation", "stop_loss", "take_profit_plan")
                },
                "structured_rules": version.structured_rules,
                "research_validation": version.validation_status.value,
                "status": lifecycle.new_state.value if lifecycle else "draft",
                "lifecycle_event_id": str(lifecycle.id) if lifecycle else None,
                "enabled": strategy.enabled,
                "approved_by": str(approval.actor_user_id)
                if approval and approval.actor_user_id
                else None,
                "created_at": aware(version.created_at).isoformat(),
                "created_from": version.change_source.value,
                "execution_permission": "sfp_plan_path_unavailable"
                if version.pattern_spec.get("kind") == SFP_KIND
                else "paper_gates_required",
                "historical_expectancy": "insufficient_history",
            }
        )
    return {
        "watched_symbols": list(config.enabled_symbols()),
        "watchlist_revision": config.revision,
        "strategies": versions,
        "setups": [setup_view(session, row, now=now) for row in rows],
        "limitations": [
            "Stored observations only; no current conditions inferred.",
            "Stored setup evidence retains its recorded availability; optional current OI, "
            "flow, CVD and book context uses the existing canonical Agent market reader.",
            "Provisional operational proxy; insufficient history; "
            "no Elliott or profitability claim.",
        ],
        "paper_only": True,
    }


def details(session: Session, *, organization_id: UUID, setup_id: UUID) -> dict[str, Any]:
    row = session.scalar(
        select(BrainSetupRow).where(
            BrainSetupRow.id == setup_id, BrainSetupRow.organization_id == organization_id
        )
    )
    if row is None:
        raise NotFoundError("Setup not found.")
    events = session.scalars(
        select(BrainSetupEventRow)
        .where(
            BrainSetupEventRow.setup_id == setup_id,
            BrainSetupEventRow.organization_id == organization_id,
        )
        .order_by(BrainSetupEventRow.occurred_at, BrainSetupEventRow.id)
        .limit(256)
    )
    return {
        **setup_view(session, row, now=datetime.now(UTC)),
        "history": [
            {
                "record_id": str(event.id),
                "kind": event.kind,
                "occurred_at": aware(event.occurred_at).isoformat(),
                "payload": event.payload,
            }
            for event in events
        ],
    }
