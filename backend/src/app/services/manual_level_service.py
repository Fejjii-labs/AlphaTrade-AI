"""Manual chart level service with immutable revision identity (Phase 3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import ManualChartLevel as ManualChartLevelModel
from app.db.models import ManualLevelRevision
from app.repositories.manual_levels import ManualChartLevelRepository
from app.schemas.manual_levels import (
    ManualChartLevel,
    ManualChartLevelCreate,
    ManualChartLevelUpdate,
)
from app.schemas.strategy_lifecycle import ManualLevelRevisionRecord
from app.services.canonical_serialization import canonical_sha256


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _level_value(
    price: Decimal | None, low: Decimal | None, high: Decimal | None
) -> Decimal | None:
    if price is not None:
        return price
    if low is not None and high is not None:
        return (low + high) / Decimal("2")
    return low if low is not None else high


def _revision_hash(
    *,
    level_id: uuid.UUID,
    revision_id: uuid.UUID,
    revision_number: int,
    organization_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    instrument: str,
    exchange: str,
    venue: str,
    market_type: str,
    price_unit: str,
    timeframe: str | None,
    level_type: str,
    value: Decimal | None,
    price_low: Decimal | None,
    price_high: Decimal | None,
    valid: bool,
    effective_at: datetime,
    created_at: datetime,
    supersedes_revision_id: uuid.UUID | None,
) -> str:
    return canonical_sha256(
        {
            "level_id": str(level_id),
            "revision_id": str(revision_id),
            "revision_number": revision_number,
            "organization_id": str(organization_id),
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "instrument": instrument,
            "exchange": exchange,
            "venue": venue,
            "market_type": market_type,
            "price_unit": price_unit,
            "timeframe": timeframe,
            "level_type": level_type,
            "value": value,
            "price_low": price_low,
            "price_high": price_high,
            "valid": valid,
            "effective_at": _aware_utc(effective_at),
            "created_at": _aware_utc(created_at),
            "supersedes_revision_id": str(supersedes_revision_id)
            if supersedes_revision_id
            else None,
        }
    )


manual_level_revision_content_hash = _revision_hash


class ManualLevelService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = ManualChartLevelRepository(session)

    def create(self, payload: ManualChartLevelCreate) -> ManualChartLevel:
        entity = ManualChartLevelModel(
            organization_id=payload.organization_id,
            user_id=payload.user_id,
            symbol=str(payload.symbol),
            exchange=payload.exchange,
            timeframe=payload.timeframe.value if payload.timeframe else None,
            level_type=payload.level_type,
            price=payload.price,
            price_low=payload.price_low,
            price_high=payload.price_high,
            label=payload.label,
            notes=payload.notes,
            enabled=payload.enabled,
        )
        self._repo.add(entity)
        self._append_revision(entity, actor_user_id=payload.user_id, supersedes=None)
        return ManualChartLevel.model_validate(entity, from_attributes=True)

    def list_levels(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        symbol: str | None = None,
        exchange: str | None = None,
        enabled_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ManualChartLevel], int]:
        rows, total = self._repo.list_scoped(
            organization_id=organization_id,
            user_id=user_id,
            symbol=symbol,
            exchange=exchange,
            enabled_only=enabled_only,
            limit=limit,
            offset=offset,
        )
        items = [ManualChartLevel.model_validate(row, from_attributes=True) for row in rows]
        return items, total

    def get(
        self,
        level_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ManualChartLevel:
        row = self._repo.get_scoped(level_id, organization_id=organization_id, user_id=user_id)
        if row is None:
            raise NotFoundError("Manual level not found.")
        return ManualChartLevel.model_validate(row, from_attributes=True)

    def update(
        self,
        level_id: uuid.UUID,
        payload: ManualChartLevelUpdate,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ManualChartLevel:
        row = self._repo.get_scoped(level_id, organization_id=organization_id, user_id=user_id)
        if row is None:
            raise NotFoundError("Manual level not found.")
        previous = self.current_revision(level_id, organization_id=organization_id)
        data = payload.model_dump(exclude_unset=True)
        semantic = False
        if "symbol" in data and data["symbol"] is not None:
            row.symbol = str(data["symbol"])
            semantic = True
        if "timeframe" in data and data["timeframe"] is not None:
            row.timeframe = data["timeframe"].value
            semantic = True
        for key in ("exchange", "level_type", "price", "price_low", "price_high", "enabled"):
            if key in data:
                setattr(row, key, data[key])
                semantic = True
        for key in ("label", "notes"):
            if key in data:
                setattr(row, key, data[key])
        if semantic:
            self._append_revision(
                row,
                actor_user_id=user_id,
                supersedes=previous.id if previous is not None else None,
            )
        return ManualChartLevel.model_validate(row, from_attributes=True)

    def delete(
        self,
        level_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        row = self._repo.get_scoped(level_id, organization_id=organization_id, user_id=user_id)
        if row is None:
            raise NotFoundError("Manual level not found.")
        # Keep historical revisions resolvable after the current projection is removed.
        self._repo.delete(row)

    def get_revision(
        self,
        revision_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> ManualLevelRevisionRecord:
        row = self._session.get(ManualLevelRevision, revision_id)
        # A foreign revision must look exactly like a missing one (no existence oracle).
        if row is None or row.organization_id != organization_id:
            raise NotFoundError("Manual level revision not found.")
        return ManualLevelRevisionRecord.model_validate(row, from_attributes=True)

    def current_revision(
        self,
        level_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> ManualLevelRevision | None:
        stmt = (
            select(ManualLevelRevision)
            .where(
                ManualLevelRevision.level_id == level_id,
                ManualLevelRevision.organization_id == organization_id,
            )
            .order_by(ManualLevelRevision.revision_number.desc())
            .limit(1)
        )
        return self._session.scalar(stmt)

    def _next_revision_number(self, level_id: uuid.UUID) -> int:
        stmt = (
            select(ManualLevelRevision.revision_number)
            .where(ManualLevelRevision.level_id == level_id)
            .order_by(ManualLevelRevision.revision_number.desc())
            .limit(1)
        )
        current = self._session.scalar(stmt)
        return 1 if current is None else int(current) + 1

    def _append_revision(
        self,
        level: ManualChartLevelModel,
        *,
        actor_user_id: uuid.UUID | None,
        supersedes: uuid.UUID | None,
    ) -> ManualLevelRevision:
        revision_number = self._next_revision_number(level.id)
        created_at = datetime.now(UTC)
        effective_at = created_at
        value = _level_value(level.price, level.price_low, level.price_high)
        revision_id = uuid.uuid4()
        venue = level.exchange
        market_type = "unspecified"
        price_unit = "quote"
        content_hash = _revision_hash(
            level_id=level.id,
            revision_id=revision_id,
            revision_number=revision_number,
            organization_id=level.organization_id,
            actor_user_id=actor_user_id,
            instrument=level.symbol,
            exchange=level.exchange,
            venue=venue,
            market_type=market_type,
            price_unit=price_unit,
            timeframe=level.timeframe,
            level_type=level.level_type.value,
            value=value,
            price_low=level.price_low,
            price_high=level.price_high,
            valid=bool(level.enabled),
            effective_at=effective_at,
            created_at=created_at,
            supersedes_revision_id=supersedes,
        )
        row = ManualLevelRevision(
            id=revision_id,
            level_id=level.id,
            revision_number=revision_number,
            organization_id=level.organization_id,
            user_id=level.user_id,
            instrument=level.symbol,
            exchange=level.exchange,
            timeframe=level.timeframe,
            level_type=level.level_type,
            value=value,
            price_low=level.price_low,
            price_high=level.price_high,
            valid=bool(level.enabled),
            actor_user_id=actor_user_id,
            venue=venue,
            market_type=market_type,
            price_unit=price_unit,
            content_hash=content_hash,
            supersedes_revision_id=supersedes,
            effective_at=effective_at,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row
