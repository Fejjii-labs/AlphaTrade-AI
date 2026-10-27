"""What the autonomous paper bot did: today's trade count and its last actions.

Read-only. The trade count comes from the same daily accounting the bot cap is
checked against; it is computed inside a savepoint that is always rolled back,
so reading never writes daily risk state. Actions come from the paper loop's
own audit records; refusals the loop does not audit are not listed.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import AuditLog, JournalTrade
from app.schemas.autonomous_activity import AutonomousActivityView, BotAction, BotActionOutcome
from app.schemas.common import AuditEventType
from app.services.audit_service import AuditService
from app.services.risk.daily_risk_accounting import DailyRiskAccounting
from app.services.risk.settings_service import RiskSettingsService

PAPER_LOOP_RESOURCE = "automated_paper_loop"

_OUTCOMES: dict[AuditEventType, BotActionOutcome] = {
    AuditEventType.JOURNAL_TRADE_CREATED: BotActionOutcome.ENTERED,
    AuditEventType.RISK_BLOCK: BotActionOutcome.BLOCKED,
    AuditEventType.PAPER_ORDER_REJECTED: BotActionOutcome.REJECTED,
}


class AutonomousActivityService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._accounting = DailyRiskAccounting(
            session, RiskSettingsService(session, AuditService(session))
        )

    def view(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID, limit: int = 5
    ) -> AutonomousActivityView:
        day, timezone = self._accounting.resolve_day(
            organization_id=organization_id, user_id=user_id
        )
        return AutonomousActivityView(
            day=day,
            timezone=timezone,
            trades_today=self._trades_today(organization_id=organization_id, user_id=user_id),
            recent_actions=self._recent_actions(
                organization_id=organization_id, user_id=user_id, limit=limit
            ),
        )

    def _trades_today(self, *, organization_id: uuid.UUID, user_id: uuid.UUID) -> int | None:
        savepoint = self._session.begin_nested()
        try:
            snapshot = self._accounting.sync_from_portfolio(
                organization_id=organization_id, user_id=user_id
            )
            return snapshot.trade_count
        except SQLAlchemyError:  # an unreadable count is reported as unknown
            return None
        finally:
            savepoint.rollback()

    def _recent_actions(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID, limit: int
    ) -> list[BotAction]:
        rows = self._session.scalars(
            select(AuditLog)
            .where(
                AuditLog.organization_id == organization_id,
                AuditLog.user_id == user_id,
                AuditLog.resource_type == PAPER_LOOP_RESOURCE,
                AuditLog.action.in_(list(_OUTCOMES)),
            )
            .order_by(AuditLog.event_at.desc())
            .limit(limit)
        ).all()
        journal_ids = {
            parsed
            for row in rows
            if (parsed := _uuid((row.redacted_metadata or {}).get("journal_trade_id")))
        }
        trades = (
            {
                trade.id: trade
                for trade in self._session.scalars(
                    select(JournalTrade).where(
                        JournalTrade.id.in_(journal_ids),
                        JournalTrade.organization_id == organization_id,
                        JournalTrade.user_id == user_id,
                    )
                )
            }
            if journal_ids
            else {}
        )
        actions: list[BotAction] = []
        for row in rows:
            metadata = row.redacted_metadata or {}
            journal_id = _uuid(metadata.get("journal_trade_id"))
            trade = trades.get(journal_id) if journal_id else None
            reason = metadata.get("reason_code") or metadata.get("eligibility_state")
            actions.append(
                BotAction(
                    at=row.event_at,
                    outcome=_OUTCOMES[row.action],
                    symbol=trade.symbol if trade else _text(metadata.get("symbol")),
                    direction=trade.direction.value if trade else _text(metadata.get("direction")),
                    reason=_text(reason),
                    journal_trade_id=trade.id if trade else None,
                )
            )
        return actions


def _uuid(value: object) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
