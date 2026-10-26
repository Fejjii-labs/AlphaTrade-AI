"""BloFin demo read-only synchronisation (AT-037).

Fetches and persists bounded account/position/market-context snapshots.
Never places, modifies, or cancels orders. Demo mode only.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
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
from app.providers.exchange.blofin_account import BloFinAccountProvider, parse_account_permissions
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

_TRADE_CAPABLE_OVERRIDE_LOGGED = False


def ensure_trade_capable_key_allowed(settings: Settings, permissions: Any) -> None:
    """Accept a read+trade sync key only under the explicit demo-paper opt-in.

    The sync client stays GET-only either way. Raises unless the opt-in is on,
    the origin is the allowlisted BloFin demo host, execution is paper with
    real trading off, and the key has no withdraw or transfer scope.
    """
    from app.core.config import ExecutionMode
    from app.core.exchange_safety import is_allowlisted_demo_host

    global _TRADE_CAPABLE_OVERRIDE_LOGGED
    if not (
        settings.blofin_demo_allow_trade_capable_sync_key
        and is_allowlisted_demo_host(settings.blofin_demo_rest_base_url)
        and settings.execution_mode is ExecutionMode.PAPER
        and not settings.enable_real_trading
        and not settings.real_trading_enabled
        and not permissions.can_withdraw
        and not permissions.can_transfer
    ):
        raise ValueError("Dedicated BloFin sync key must be read-only.")
    if not _TRADE_CAPABLE_OVERRIDE_LOGGED:
        _TRADE_CAPABLE_OVERRIDE_LOGGED = True
        logger.warning("blofin_sync_key_trade_capable_demo_override")


def _demo_key_read(settings: Settings) -> bool:
    """True when GET sync uses the main demo key. Execution stays sealed."""

    return (
        settings.blofin_demo_read_fallback_ready or settings.blofin_paper_internal_main_key_selected
    )


def _account_pin_applies(settings: Settings) -> bool:
    """True when this sync key must match the pinned native demo account.

    The dedicated read-only key and the main demo-key read both do.
    The execution-provider path, used when a partial or unused read-only set
    is present, keeps its previous gate.
    """

    return settings.blofin_read_sync_uses_dedicated_credentials or _demo_key_read(settings)


def _log_snapshot_identity(result: str) -> None:
    """Log verified or mismatch. The UID and key values are not included."""

    if result not in {"verified", "mismatch"}:
        raise ValueError("Unknown BloFin snapshot identity result.")
    emit = logger.info if result == "verified" else logger.warning
    emit("blofin_snapshot_identity", result=result)


def _require_fallback_account_pin(settings: Settings, organization_id: uuid.UUID) -> None:
    """Refuse the main demo key unless the activity org and UID pins match.

    A missing pin, or a pin for a different organization, does not fall open.
    The log carries a reason token only.
    """

    if not _demo_key_read(settings):
        return
    config = get_activity_settings()
    pinned_org = config.organization_id
    if pinned_org is None:
        reason = "activity_organization_unset"
        message = "BloFin fallback sync requires BLOFIN_ACTIVITY_ORGANIZATION_ID."
    elif pinned_org != organization_id:
        reason = "activity_organization_mismatch"
        message = (
            "BloFin fallback sync organization does not match BLOFIN_ACTIVITY_ORGANIZATION_ID."
        )
    elif not config.expected_uid.strip():
        reason = "activity_expected_uid_unset"
        message = "BloFin fallback sync requires BLOFIN_ACTIVITY_EXPECTED_UID."
    else:
        return
    logger.warning("blofin_fallback_sync_refused", reason=reason)
    raise ValueError(message)


def require_internal_snapshot_mirror_identity(
    settings: Settings,
    organization_id: uuid.UUID,
    *,
    fetch_identity: Callable[[], Any],
) -> None:
    """Refuse the paper_internal main demo key unless the #304 pin matches.

    No-op for the dedicated read-only key and for ``paper_exchange_demo``.
    The organization and expected-UID pins are checked before
    ``fetch_identity``. That callable is the identity GET only. A mismatch
    or an unverified UID raises before the caller reads positions or history.
    """

    if not settings.blofin_paper_internal_main_key_selected:
        return
    _require_fallback_account_pin(settings, organization_id)
    payload = fetch_identity()
    permissions = parse_account_permissions(payload)
    uid = native_uid(payload) if permissions.can_read else None
    if native_uid({"uid": uid}) is None:
        raise ValueError("BloFin sync native account UID is unverified.")
    expected = get_activity_settings().expected_uid.strip()
    if expected != uid:
        _log_snapshot_identity("mismatch")
        logger.warning("blofin_fallback_sync_refused", reason="native_account_mismatch")
        raise ValueError("BloFin sync native account UID does not match connection pin.")
    _log_snapshot_identity("verified")


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
        actor_type: ActorType = ActorType.USER,
        sync_origin: str = "api",
    ) -> BloFinSyncResult:
        """Fetch demo account state and persist a bounded snapshot."""
        origin = sync_origin if sync_origin in {"api", "worker"} else "api"
        if origin == "worker" and not self._settings.blofin_worker_readonly_ready:
            logger.warning(
                "blofin_worker_sync_readonly_required",
                readonly_sync_enabled=self._settings.blofin_readonly_sync_enabled,
                readonly_credentials_present=self._settings.blofin_readonly_configured,
                demo_read_fallback_ready=self._settings.blofin_demo_read_fallback_ready,
                internal_snapshot_read_ready=(
                    self._settings.blofin_paper_internal_snapshot_read_ready
                ),
            )
            raise ValueError("BloFin worker sync requires dedicated read-only credentials.")
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
                if self._settings.blofin_read_sync_uses_dedicated_credentials
                else self._settings.blofin_demo_configured
            ),
            "readonly_sync_enabled": self._settings.blofin_readonly_sync_enabled,
            "demo_active": self._settings.exchange_demo_active,
            "sync_origin": origin,
        }
        configured = configured_demo_account(
            self._session, self._settings, organization_id=organization_id, user_id=user_id
        )
        if configured is not None:
            provenance["configured_execution_account_id"] = str(configured.id)

        try:
            uses_dedicated = self._settings.blofin_read_sync_uses_dedicated_credentials
            _require_fallback_account_pin(self._settings, organization_id)
            binding = connection_binding(self._settings, readonly=uses_dedicated)
            provenance.update(connection_binding=binding, environment="demo")
            provider: ExchangeAccountProvider
            if origin == "worker" or uses_dedicated or _demo_key_read(self._settings):
                provider = get_readonly_account_provider(self._settings)
            else:
                ensure_demo_exchange_access(self._settings)
                provider = get_demo_account_provider(self._settings)
            permissions = provider.get_account_permissions()
            if uses_dedicated and permissions.can_trade:
                ensure_trade_capable_key_allowed(self._settings, permissions)
            if permissions.can_withdraw or permissions.can_transfer or not permissions.can_read:
                raise ValueError("BloFin sync requires read access without money-movement scopes.")
            uid = getattr(provider, "native_account_uid", None)
            if native_uid({"uid": uid}) is None:
                raise ValueError("BloFin sync native account UID is unverified.")
            config = get_activity_settings()
            pin_applies = (
                _account_pin_applies(self._settings)
                and config.organization_id == organization_id
                and bool(config.expected_uid.strip())
            )
            if pin_applies and config.expected_uid.strip() != uid:
                _log_snapshot_identity("mismatch")
                if _demo_key_read(self._settings):
                    logger.warning("blofin_fallback_sync_refused", reason="native_account_mismatch")
                raise ValueError("BloFin sync native account UID does not match connection pin.")
            if pin_applies:
                _log_snapshot_identity("verified")
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
                actor_type=actor_type,
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
        user_id: uuid.UUID | None = None,
        successful_only: bool = False,
    ) -> BloFinSyncSnapshotItem:
        try:
            binding = connection_binding(
                self._settings,
                readonly=self._settings.blofin_read_sync_uses_dedicated_credentials,
            )
        except Exception:
            raise NotFoundError("Current BloFin connection is unverified.") from None
        uid = self._snapshots.verified_uid_for_binding(organization_id, binding)
        if _account_pin_applies(self._settings):
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
            organization_id,
            native_uid=uid,
            binding=binding,
            successful_only=successful_only,
            user_id=user_id,
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
