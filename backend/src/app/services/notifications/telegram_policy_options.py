"""Named choices for Telegram policy filters (read-only).

Strategy alerts match on an identifier: Candidate alerts carry the strategy
version id, legacy paper alerts the strategy id. One strategy choice therefore
stands for its strategy id plus its most recent version ids.
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UserStrategy, UserStrategyVersion
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.strategy_analytics import NestedMaturityStage
from app.schemas.telegram_policy import TelegramFilterOption, TelegramPolicyOptions

MAX_STRATEGIES = 50
VERSIONS_PER_STRATEGY = 10
_STAGE_LABELS = {NestedMaturityStage.N4_PLUS: "N4+"}


def telegram_policy_options(
    session: Session, *, organization_id: UUID, user_id: UUID
) -> TelegramPolicyOptions:
    watchlist = WatcherWatchlistRepository(session).load(organization_id)
    strategies = session.scalars(
        select(UserStrategy)
        .where(UserStrategy.organization_id == organization_id, UserStrategy.user_id == user_id)
        .order_by(UserStrategy.name, UserStrategy.id)
        .limit(MAX_STRATEGIES)
    ).all()
    versions: dict[UUID, list[UUID]] = defaultdict(list)
    if strategies:
        rows = session.execute(
            select(UserStrategyVersion.strategy_id, UserStrategyVersion.id)
            .where(UserStrategyVersion.strategy_id.in_([s.id for s in strategies]))
            .order_by(UserStrategyVersion.strategy_id, UserStrategyVersion.version.desc())
        ).all()
        for strategy_id, version_id in rows:
            if len(versions[strategy_id]) < VERSIONS_PER_STRATEGY:
                versions[strategy_id].append(version_id)
    return TelegramPolicyOptions(
        symbols=tuple(
            TelegramFilterOption(label=symbol, values=(symbol,))
            for symbol in watchlist.enabled_symbols()
        ),
        strategies=tuple(
            TelegramFilterOption(
                label=strategy.name,
                values=(str(strategy.id), *(str(v) for v in versions[strategy.id])),
            )
            for strategy in strategies
        ),
        setup_stages=tuple(
            TelegramFilterOption(label=_STAGE_LABELS.get(stage, stage.value), values=(stage.value,))
            for stage in NestedMaturityStage
        ),
    )
