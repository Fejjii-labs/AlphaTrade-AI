"""Paper position management."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFoundError, TradingPolicyError, ValidationAppError
from app.db.models import Position as PositionModel
from app.repositories.positions import PositionRepository
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AuditEventType,
    JournalEntryMethod,
    PositionStatus,
    TradeDirection,
)
from app.schemas.position import ClosePaperPositionRequest, Position, PositionUpdate
from app.schemas.proposal import TakeProfitLevel
from app.services.audit_service import AuditService
from app.services.journal_trade_service import JournalTradeService
from app.services.market_data_service import MarketDataService
from app.services.risk.daily_risk_accounting import DailyRiskAccounting
from app.services.risk.settings_service import RiskSettingsService

logger = structlog.get_logger(__name__)


class PositionService:
    def __init__(
        self,
        session: Session,
        audit_service: AuditService,
        *,
        settings: Settings | None = None,
        risk_settings: RiskSettingsService | None = None,
        daily_risk: DailyRiskAccounting | None = None,
        market_data_service: MarketDataService | None = None,
    ) -> None:
        self._session = session
        self._repo = PositionRepository(session)
        self._audit = audit_service
        self._app_settings = settings
        settings_svc = risk_settings or RiskSettingsService(session, audit_service)
        self._daily_risk = daily_risk or DailyRiskAccounting(session, settings_svc)
        self._market_data = market_data_service

    def list_positions(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        status: PositionStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Position], int]:
        rows, total = self._repo.list_positions(
            organization_id=organization_id,
            user_id=user_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return [_to_schema(row) for row in rows], total

    def get(self, position_id: uuid.UUID) -> Position:
        row = self._repo.get(position_id)
        if row is None:
            raise NotFoundError("Position not found")
        return _to_schema(row)

    def update(self, position_id: uuid.UUID, data: PositionUpdate) -> Position:
        row = self._repo.get(position_id)
        if row is None:
            raise NotFoundError("Position not found")
        if row.status is not PositionStatus.OPEN:
            raise ValidationAppError("Only open positions can be updated.")
        if data.stop_loss is not None:
            row.stop_loss = data.stop_loss
        if data.take_profits is not None:
            row.take_profits = [
                {"price": str(tp.price), "size_fraction": tp.size_fraction}
                for tp in data.take_profits
            ]
        if data.risk_state is not None:
            row.risk_state = data.risk_state
        self._repo.add(row)
        self._record_audit(row, AuditEventType.POSITION_UPDATED, {"action": "update"})
        return _to_schema(row)

    def close_paper(self, position_id: uuid.UUID, data: ClosePaperPositionRequest) -> Position:
        row = self._repo.get(position_id)
        if row is None:
            raise NotFoundError("Position not found")
        if row.status is not PositionStatus.OPEN:
            raise ValidationAppError("Position is already closed.")
        exit_price = self._resolve_close_exit_price(row.symbol, requested=data.exit_price)
        pnl = _estimate_pnl(row, exit_price)
        row.realized_pnl = pnl
        row.unrealized_pnl = Decimal("0")
        row.status = PositionStatus.CLOSED
        row.closed_at = datetime.now(UTC)
        self._repo.add(row)
        snapshot = self._daily_risk.record_after_position_close(
            organization_id=row.organization_id,
            user_id=row.user_id,
        )
        self._record_audit(
            row,
            AuditEventType.POSITION_UPDATED,
            {
                "action": "close_paper",
                "exit_price": str(exit_price),
                "requested_exit_price": str(data.exit_price),
                "exit_price_source": "user_submitted",
                "reason": data.reason,
                "realized_pnl": str(pnl),
                "daily_realized_pnl": str(snapshot.realized_pnl),
                "daily_locked": str(snapshot.daily_locked),
            },
        )
        self._auto_journal_after_close(row)
        return _to_schema(row)

    def _auto_journal_after_close(self, row: PositionModel) -> None:
        """Opt-in AT-033 hook: journal the closed position (entry_method=auto).

        Reuses the idempotent ``create_from_position`` prefill, so a position
        the user already journaled manually is never duplicated. Runs inside a
        savepoint and swallows every error: journaling must never block or
        roll back the position close itself.
        """
        settings = self._app_settings
        if settings is None or not settings.journal_auto_from_position_close:
            return
        try:
            with self._session.begin_nested():
                JournalTradeService(self._session, self._audit).create_from_position(
                    row.id,
                    organization_id=row.organization_id,
                    user_id=row.user_id,
                    entry_method=JournalEntryMethod.AUTO,
                    action="auto_journal_position_close",
                )
        except Exception as exc:
            logger.warning(
                "auto_journal_position_close_failed",
                position_id=str(row.id),
                error_type=type(exc).__name__,
            )

    def _resolve_close_exit_price(self, symbol: str, *, requested: Decimal | None) -> Decimal:
        """Resolve the exit price for a paper close (paper-close honesty).

        An explicit user-submitted paper close price is authoritative: the exact
        submitted decimal drives persistence, realized PnL, and the audit event.
        Market ticker data must never silently replace it (P0 defect: staging
        recorded the ticker price instead of the submitted ``91234.56``).

        System-generated closes without an explicit price (``requested is None``,
        e.g. future automated liquidation) bind to fresh server market data and
        fail closed when it is missing, stale, or degraded — a price is never
        fabricated.
        """
        if requested is not None:
            return _validated_explicit_exit_price(requested)
        return self._market_exit_price(symbol)

    def _market_exit_price(self, symbol: str) -> Decimal:
        """Fresh server ticker price for closes with no explicit price; fail closed."""
        if self._market_data is None:
            raise TradingPolicyError(
                "No explicit exit price and no market data service; paper close refused.",
                details={"reason": "market_data_unavailable", "symbol": symbol},
            )
        try:
            ticker = self._market_data.get_ticker(symbol)
        except Exception as exc:
            raise TradingPolicyError(
                "Market data is unavailable; paper close refused.",
                details={"reason": "market_data_unavailable", "symbol": symbol},
            ) from exc

        meta = ticker.meta
        if meta.is_stale or meta.fallback_used:
            raise TradingPolicyError(
                "Market data is stale or degraded; paper close refused.",
                details={
                    "reason": "market_data_degraded",
                    "symbol": symbol,
                    "is_stale": str(meta.is_stale),
                    "fallback_used": str(meta.fallback_used),
                },
            )
        return ticker.last_price

    def _record_audit(
        self,
        row: PositionModel,
        event_type: AuditEventType,
        metadata: dict[str, object],
    ) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id="position-api",
                trace_id="position-api",
                event_type=event_type,
                resource_type="position",
                resource_id=str(row.id),
                organization_id=row.organization_id,
                user_id=row.user_id,
                actor_type=ActorType.USER,
                metadata=metadata,
            )
        )


def _validated_explicit_exit_price(value: Decimal) -> Decimal:
    """Fail closed on malformed explicit close prices (defense in depth).

    The request schema already enforces a positive decimal, but the service can
    be called directly; NaN/Infinity or non-positive values must never reach
    PnL math or the audit trail.
    """
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValidationAppError(
            "Explicit paper close exit price must be a positive finite decimal."
        )
    return value


def _estimate_pnl(row: PositionModel, exit_price: Decimal) -> Decimal:
    delta = exit_price - row.entry_price
    if row.direction is TradeDirection.SHORT:
        delta = -delta
    return delta * row.size


def _to_schema(row: PositionModel) -> Position:
    take_profits = [
        TakeProfitLevel(price=Decimal(str(tp["price"])), size_fraction=float(tp["size_fraction"]))
        for tp in (row.take_profits or [])
    ]
    return Position(
        id=row.id,
        organization_id=row.organization_id,
        user_id=row.user_id,
        strategy_id=row.strategy_id,
        linked_proposal_id=row.linked_proposal_id,
        symbol=row.symbol,
        direction=row.direction,
        size=row.size,
        entry_price=row.entry_price,
        leverage=row.leverage,
        stop_loss=row.stop_loss,
        take_profits=take_profits,
        liquidation_price=row.liquidation_price,
        unrealized_pnl=row.unrealized_pnl,
        realized_pnl=row.realized_pnl,
        risk_state=row.risk_state or {},
        status=row.status,
        opened_at=row.opened_at,
        closed_at=row.closed_at,
    )
