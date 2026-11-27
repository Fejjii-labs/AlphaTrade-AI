"""Named choices for Telegram policy filters (read-only).

Strategy alerts match on an identifier: Candidate alerts carry the strategy
version id, legacy paper alerts the strategy id. One strategy choice therefore
stands for its strategy id plus its most recent version ids. A family choice
("D-line", "Nested", "SFP") stands for the identifiers of every listed strategy
of that family; strategies created later are not in it until it is saved again.
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
from app.services.display_names import family_label

MAX_STRATEGIES = 50
MAX_FAMILY_STRATEGIES = 200
VERSIONS_PER_STRATEGY = 10
_STAGE_LABELS = {NestedMaturityStage.N4_PLUS: "N4+"}
_FAMILY_ORDER = ("D-line", "Nested", "SFP")


def telegram_policy_options(
    session: Session, *, organization_id: UUID, user_id: UUID
) -> TelegramPolicyOptions:
    watchlist = WatcherWatchlistRepository(session).load(organization_id)
    strategies = session.scalars(
        select(UserStrategy)
        .where(UserStrategy.organization_id == organization_id, UserStrategy.user_id == user_id)
        .order_by(UserStrategy.name, UserStrategy.id)
        .limit(MAX_FAMILY_STRATEGIES)
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

    def identifiers(strategy: UserStrategy) -> tuple[str, ...]:
        return (str(strategy.id), *(str(v) for v in versions[strategy.id]))

    families: dict[str, list[str]] = defaultdict(list)
    for strategy in strategies:
        families[family_label(strategy.setup_type)].extend(identifiers(strategy))
    return TelegramPolicyOptions(
        symbols=tuple(
            TelegramFilterOption(label=symbol, values=(symbol,))
            for symbol in watchlist.enabled_symbols()
        ),
        strategies=tuple(
            TelegramFilterOption(label=strategy.name, values=identifiers(strategy))
            for strategy in strategies[:MAX_STRATEGIES]
        ),
        setup_stages=tuple(
            TelegramFilterOption(label=_STAGE_LABELS.get(stage, stage.value), values=(stage.value,))
            for stage in NestedMaturityStage
        ),
        strategy_families=tuple(
            TelegramFilterOption(label=label, values=tuple(families[label]))
            for label in sorted(families, key=_family_sort_key)
        ),
    )


def _family_sort_key(label: str) -> tuple[int, str]:
    rank = _FAMILY_ORDER.index(label) if label in _FAMILY_ORDER else len(_FAMILY_ORDER)
    return (rank, label)
