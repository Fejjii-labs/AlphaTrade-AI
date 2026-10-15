"""BloFin demo read-only synchronisation (AT-037).

Fetches and persists bounded account/position/market-context snapshots.
Never places, modifies, or cancels orders. Demo mode only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.blofin_identity import connection_binding, native_uid
from app.core.blofin_readonly_access import get_readonly_account_provider
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError
from app.core.exchange_demo_access import ensure_demo_exchange_access, get_demo_account_provider
from app.db.blofin_activity import BloFinActivityAccount
from app.db.models import BloFinDemoSyncSnapshot as SnapshotModel
from app.guardrails.redaction import redact_text
from app.providers.base import ProviderHealth
from app.providers.exchange.base import (
    ExchangeAccountProvider,
    ExchangeBalance,
    ExchangePositionData,
)
from app.providers.exchange.blofin_account import BloFinAccountProvider
from app.providers.exchange.errors import ExchangeError
from app.repositories.blofin_sync import BloFinSyncRepository
from app.schemas.audit import AuditRecordCreate
from app.schemas.blofin_sync import BloFinSyncResult, BloFinSyncSnapshotItem
from app.schemas.common import (
    ActorType,
    AuditEventType,
    AuditResult,
    AuditSeverity,
    BloFinSyncHealthStatus,
)
from app.services.audit_service import AuditService
from app.services.blofin_activity_config import get_activity_settings
from app.services.dashboard.demo_performance import configured_demo_account

logger = structlog.get_logger(__name__)


class BloFinSyncService:
    """Read-only BloFin demo account/position reconciliation."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._snapshots = BloFinSyncRepository(session)
        self._audit = AuditService(session)

    def sync(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        request_id: str | None = None,
        include_instrument_metadata: bool = False,
    ) -> BloFinSyncResult:
        """Fetch demo account state and persist a bounded snapshot."""
        now = datetime.now(UTC)
        exchange_mode = self._settings.exchange_mode.value
        health = BloFinSyncHealthStatus.UNAVAILABLE
        account: dict[str, Any] = {}
        positions: dict[str, Any] = {"items": []}
        market: dict[str, Any] = {"symbols": []}
        error_summary: str | None = None
        position_count = 0
        balance_count = 0
        provenance: dict[str, Any] = {
            "provider": "blofin_demo",
            "exchange_mode": exchange_mode,
            "read_only": True,
            "order_mutations": False,
            "synced_at": now.isoformat(),
            "credentials_configured": (
                self._settings.blofin_readonly_configured
                if self._settings.blofin_readonly_sync_enabled
                else self._settings.blofin_demo_configured
            ),
            "readonly_sync_enabled": self._settings.blofin_readonly_sync_enabled,
            "demo_active": self._settings.exchange_demo_active,
        }
        configured = configured_demo_account(
            self._session, self._settings, organization_id=organization_id, user_id=user_id
        )
        if configured is not None:
            provenance["configured_execution_account_id"] = str(configured.id)

        try:
            binding = connection_binding(
                self._settings, readonly=self._settings.blofin_readonly_sync_enabled
            )
            provenance.update(connection_binding=binding, environment="demo")
            provider: ExchangeAccountProvider
            if self._settings.blofin_readonly_sync_enabled:
                provider = get_readonly_account_provider(self._settings)
            else:
                ensure_demo_exchange_access(self._settings)
                provider = get_demo_account_provider(self._settings)
            permissions = provider.get_account_permissions()
            if self._settings.blofin_readonly_sync_enabled and permissions.can_trade:
                raise ValueError("Dedicated BloFin sync key must be read-only.")
            if permissions.can_withdraw or permissions.can_transfer or not permissions.can_read:
                raise ValueError("BloFin sync requires read access without money-movement scopes.")
            uid = getattr(provider, "native_account_uid", None)
            if native_uid({"uid": uid}) is None:
                raise ValueError("BloFin sync native account UID is unverified.")
            config = get_activity_settings()
            if (
                self._settings.blofin_readonly_sync_enabled
                and config.organization_id == organization_id
                and config.expected_uid
                and config.expected_uid != uid
            ):
                raise ValueError("BloFin sync native account UID does not match connection pin.")
            provenance.update(native_account_uid=uid, identity_status="verified")
            balances = provider.get_balances()
            open_positions = provider.get_positions()
            metadata: dict[str, dict[str, str]] = {}
            metadata_failed = False
            if (
                include_instrument_metadata
                and isinstance(provider, BloFinAccountProvider)
                and open_positions
            ):
                try:
                    metadata = provider.get_position_metadata(
                        {
                            p.inst_id
                            for p in open_positions[: self._settings.blofin_sync_max_positions]
                        }
                    )
                except Exception as exc:
                    # Optional conversion must not discard valid native account evidence.
                    metadata_failed = True
                    logger.warning(
                        "blofin_instrument_metadata_failed", error_type=type(exc).__name__
                    )
            provider_status = provider.status()

            max_balances = self._settings.blofin_sync_max_balances
            max_positions = self._settings.blofin_sync_max_positions
            balance_items = [_balance_dict(item) for item in balances[:max_balances]]
            position_items = [_position_dict(item) for item in open_positions[:max_positions]]
            balance_count = len(balance_items)
            position_count = len(position_items)

            account = {
                "total_equity_usd": (
                    _dec(provider.total_equity_usd)
                    if isinstance(provider, BloFinAccountProvider)
                    else None
                ),
                "balances": balance_items,
                "balances_truncated": len(balances) > max_balances,
                "permissions": {
                    "can_read": permissions.can_read,
                    "can_trade": permissions.can_trade,
                    "can_withdraw": permissions.can_withdraw,
                    "can_transfer": permissions.can_transfer,
                    # Never persist raw secret-bearing material; scopes are tokens only.
                    "raw_scopes": [
                        scope
                        for scope in permissions.raw_scopes
                        if scope in {"read", "readonly", "read_only", "trade", "trading"}
                    ][:20],
                    "response_keys": [
                        key
                        for key in permissions.response_keys
                        if key in {"readOnly", "read_only", "permissions", "permission", "scopes"}
                    ][:40],
                },
                "provider_status": {
                    "name": provider_status.name,
                    "health": (
                        provider_status.health.value
                        if hasattr(provider_status.health, "value")
                        else str(provider_status.health)
                    ),
                    "using_fallback": provider_status.using_fallback,
                    "is_mock": provider_status.is_mock,
                    "detail": redact_text(provider_status.detail or "")[:200],
                    "error_message": redact_text(provider_status.error_message or "")[:200] or None,
                },
            }
            positions = {
                "items": position_items,
                "truncated": len(open_positions) > max_positions,
                "instrument_metadata": metadata,
                "metadata_unavailable": metadata_failed,
            }
            symbols = sorted(
                {symbol for item in position_items if (symbol := item.get("symbol")) is not None}
            )[:max_positions]
            market = {
                "symbols": symbols,
                "note": "Market context limited to open demo position symbols.",
            }
            provenance["provider_name"] = provider.name
            provenance["fetched_at"] = datetime.now(UTC).isoformat()

            if (
                provider_status.health is not ProviderHealth.HEALTHY
                or provider_status.using_fallback
                or metadata_failed
            ):
                health = BloFinSyncHealthStatus.DEGRADED
                error_summary = "Demo account provider is degraded or using fallback."
            else:
                health = BloFinSyncHealthStatus.OK
        except Exception as exc:
            # Fail closed into an unavailable snapshot; never leak secrets.
            logger.warning(
                "blofin_demo_sync_failed",
                error_type=type(exc).__name__,
            )
            if isinstance(exc, ExchangeError):
                error_summary = redact_text(str(exc))[:200]
            else:
                error_summary = redact_text(f"{type(exc).__name__}: sync unavailable")[:200]
            health = BloFinSyncHealthStatus.UNAVAILABLE
            provenance["error_type"] = type(exc).__name__
            account, positions, market = {}, {"items": []}, {"symbols": []}
            position_count = balance_count = 0

        row = SnapshotModel(
            organization_id=organization_id,
            user_id=user_id,
            synced_at=now,
            health_status=health.value,
            provider="blofin_demo",
            exchange_mode=exchange_mode,
            account_snapshot=account,
            positions_snapshot=positions,
            market_context=market,
            provenance=provenance,
            is_stale=False,
            stale_reason=None,
            error_summary=error_summary,
            position_count=position_count,
            balance_count=balance_count,
        )
        self._snapshots.add(row)
        self._audit.record(
            AuditRecordCreate(
                request_id=request_id or f"blofin-sync-{row.id}",
                trace_id=str(uuid.uuid4()),
                user_id=user_id,
                organization_id=organization_id,
                event_type=AuditEventType.BLOFIN_DEMO_SYNC,
                resource_type="blofin_demo_sync_snapshot",
                resource_id=str(row.id),
                actor_type=ActorType.USER,
                result=(
                    AuditResult.SUCCESS
                    if health is BloFinSyncHealthStatus.OK
                    else AuditResult.FAILURE
                ),
                severity=AuditSeverity.INFO,
                metadata={
                    "health_status": health.value,
                    "position_count": position_count,
                    "balance_count": balance_count,
                    "read_only": True,
                },
            )
        )
        return BloFinSyncResult(snapshot=self._to_item(row, mark_stale=True))

    def latest(
        self,
        *,
        organization_id: uuid.UUID,
        successful_only: bool = False,
    ) -> BloFinSyncSnapshotItem:
        try:
            binding = connection_binding(
                self._settings, readonly=self._settings.blofin_readonly_sync_enabled
            )
        except Exception:
            raise NotFoundError("Current BloFin connection is unverified.") from None
        uid = self._snapshots.verified_uid_for_binding(organization_id, binding)
        if self._settings.blofin_readonly_sync_enabled:
            config = get_activity_settings()
            if config.organization_id == organization_id and config.expected_uid:
                proof = self._session.get(
                    BloFinActivityAccount, (organization_id, "demo", config.expected_uid)
                )
                if (
                    proof is not None
                    and proof.credential_binding == binding
                    and proof.identity_verified_at is not None
                    and not proof.identity_error
                ):
                    uid = config.expected_uid
                elif uid != config.expected_uid or (proof is not None and proof.identity_error):
                    uid = None
        if native_uid({"uid": uid}) is None:
            raise NotFoundError("Current BloFin connection is unverified.")
        assert uid is not None
        row = self._snapshots.latest_for_connection(
            organization_id, native_uid=uid, binding=binding, successful_only=successful_only
        )
        if row is None:
            raise NotFoundError("No BloFin demo sync snapshot found for this organization.")
        return self._to_item(row, mark_stale=True)

    def _to_item(
        self,
        row: SnapshotModel,
        *,
        mark_stale: bool = False,
    ) -> BloFinSyncSnapshotItem:
        health = (
            row.health_status
            if isinstance(row.health_status, BloFinSyncHealthStatus)
            else BloFinSyncHealthStatus(str(row.health_status))
        )
        is_stale = bool(row.is_stale)
        stale_reason = row.stale_reason
        if mark_stale:
            synced_at = row.synced_at
            if synced_at.tzinfo is None:
                synced_at = synced_at.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - synced_at).total_seconds()
            if age > self._settings.blofin_sync_stale_after_seconds:
                is_stale = True
                stale_reason = stale_reason or "Snapshot older than configured freshness window."
                if health is BloFinSyncHealthStatus.OK:
                    health = BloFinSyncHealthStatus.STALE
        return BloFinSyncSnapshotItem(
            id=row.id,
            organization_id=row.organization_id,
            user_id=row.user_id,
            synced_at=row.synced_at,
            health_status=health,
            provider=row.provider,
            exchange_mode=row.exchange_mode,
            account_snapshot=row.account_snapshot if isinstance(row.account_snapshot, dict) else {},
            positions_snapshot=(
                row.positions_snapshot if isinstance(row.positions_snapshot, dict) else {}
            ),
            market_context=row.market_context if isinstance(row.market_context, dict) else {},
            provenance=(
                {key: value for key, value in row.provenance.items() if key != "connection_binding"}
                if isinstance(row.provenance, dict)
                else {}
            ),
            is_stale=is_stale,
            stale_reason=stale_reason,
            error_summary=row.error_summary,
            position_count=row.position_count,
            balance_count=row.balance_count,
        )


def _dec(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _balance_dict(item: ExchangeBalance) -> dict[str, str | None]:
    return {
        "asset": item.asset,
        "total": _dec(item.total) or "0",
        "available": _dec(item.available) or "0",
        "equity": _dec(item.equity),
    }


def _position_dict(item: ExchangePositionData) -> dict[str, str | None]:
    return {
        "symbol": item.symbol,
        "inst_id": item.inst_id,
        "side": item.side,
        "size": _dec(item.size),
        "entry_price": _dec(item.entry_price),
        "mark_price": _dec(item.mark_price),
        "unrealized_pnl": _dec(item.unrealized_pnl),
        "leverage": _dec(item.leverage),
    }
