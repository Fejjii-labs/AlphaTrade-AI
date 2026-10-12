"""Account-bound recorded outcomes, independent of balances and simulator statistics."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.db.models import ExecutionAccount, JournalTrade
from app.schemas.common import JournalTradeSource
from app.schemas.dashboard_demo_account import DashboardDemoAccount, DemoAccountPerformance
from app.security.tenant import TenantContext
from app.services.manual_demo_history import ManualDemoHistoryService

_LIMIT = 200


def configured_demo_account(
    session: Session, settings: Settings, *, organization_id: UUID, user_id: UUID
) -> ExecutionAccount | None:
    """Resolve only the explicitly configured owner/account; never guess by venue."""
    try:
        scope = tuple(
            UUID(value)
            for value in (
                settings.governed_blofin_demo_organization_id,
                settings.governed_blofin_demo_user_id,
                settings.governed_blofin_demo_account_id,
            )
        )
    except (ValueError, TypeError, AttributeError):
        return None
    if scope[:2] != (organization_id, user_id):
        return None
    return session.scalar(
        select(ExecutionAccount).where(
            ExecutionAccount.id == scope[2],
            ExecutionAccount.organization_id == organization_id,
            ExecutionAccount.user_id == user_id,
            ExecutionAccount.execution_mode == "PAPER",
        )
    )


def attach_demo_performance(
    account: DashboardDemoAccount, *, session: Session, settings: Settings, tenant: TenantContext
) -> DashboardDemoAccount:
    configured = configured_demo_account(
        session, settings, organization_id=tenant.organization_id, user_id=tenant.user_id
    )
    if configured is None or account.account_id != configured.id:
        account.performance = DemoAccountPerformance(
            coverage="Performance is unavailable until a fresh snapshot is bound to your "
            "configured BloFin execution account. Other accounts are excluded."
        )
        return account
    rows = list(
        session.scalars(
            select(JournalTrade)
            .where(
                JournalTrade.organization_id == tenant.organization_id,
                JournalTrade.user_id == tenant.user_id,
                JournalTrade.account_id == configured.id,
                JournalTrade.exchange == "BLOFIN_DEMO",
            )
            .order_by(JournalTrade.entry_time.desc(), JournalTrade.id.desc())
            .limit(_LIMIT + 1)
        )
    )
    truncated = len(rows) > _LIMIT
    rows = rows[:_LIMIT]
    performance = DemoAccountPerformance(
        manual_test_trades=sum(row.source == JournalTradeSource.MANUAL_DEMO_TEST for row in rows),
        coverage="Partial coverage: verified AlphaTrade manual demo lifecycles only; "
        "outside venue history and strategy outcomes are not included. "
        "Manual tests count as account activity and are excluded from strategy performance. "
        "Gross/net PnL require verified native fee currency/sign "
        "and fillPnl fee/funding semantics; "
        "funding also requires native funding evidence.",
    )
    if truncated:
        performance.coverage += f" Only the latest {_LIMIT} recorded trades were checked."
    verified: list[dict[str, Decimal | None]] = []
    history = ManualDemoHistoryService(session)
    for row in rows:
        if row.source != JournalTradeSource.MANUAL_DEMO_TEST or row.execution_lifecycle_id is None:
            performance.unresolved_trades += 1
            continue
        try:
            attempt = history.get(tenant, row.execution_lifecycle_id)
        except NotFoundError:
            performance.unresolved_trades += 1
            continue
        proof = attempt.evidence
        if (
            proof.journal_trade_id != row.id
            or proof.execution_status != "closed"
            or proof.filled_quantity <= 0
            or proof.exit_quantity != proof.filled_quantity
        ):
            performance.unresolved_trades += 1
            continue
        performance.verified_closed_trades += 1
        verified.append(
            {
                "gross_pnl": proof.gross_pnl,
                "fees": proof.entry_fees + proof.exit_fees
                if proof.entry_fees is not None and proof.exit_fees is not None
                else None,
                "funding": proof.funding,
                "net_pnl": proof.net_pnl,
            }
        )
    if verified:
        performance.status = "partial"
        for field in ("gross_pnl", "fees", "funding", "net_pnl"):
            values = [proof[field] for proof in verified]
            if all(value is not None for value in values):
                setattr(performance, field, sum((v for v in values if v is not None), Decimal(0)))
    account.performance = performance
    return account
