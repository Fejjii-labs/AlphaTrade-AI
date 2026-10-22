"""Owner-scoped BloFin demo exchange probes (read-only) and gated cancel."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query

from app.core.demo_order_status import (
    cancelled_status_from_cancel_audit,
    probe_demo_order_status_with_retry,
)
from app.core.dependencies import (
    AuditServiceDep,
    BloFinSyncServiceDep,
    ProviderRegistryDep,
    SessionDep,
    SettingsDep,
)
from app.core.errors import ExchangeProviderError
from app.core.exchange_demo_access import (
    get_demo_account_provider,
    get_demo_execution_provider,
    run_demo_provider_call,
)
from app.core.exchange_readiness import exchange_provider_status
from app.guardrails.redaction import redact_text
from app.providers.exchange.base import (
    ExchangeBalance,
    ExchangeInstrument,
    ExchangeOrderResult,
    ExchangePositionData,
)
from app.providers.exchange.errors import ExchangeError
from app.providers.exchange.mapping import to_blofin_inst_id
from app.schemas.audit import AuditRecordCreate
from app.schemas.blofin_sync import BloFinSyncResult, BloFinSyncSnapshotItem
from app.schemas.common import ActorType, AuditEventType
from app.schemas.exchange import (
    ExchangeBalanceItem,
    ExchangeBalancesResponse,
    ExchangeDiagnosticsSummaryResponse,
    ExchangeInstrumentItem,
    ExchangeInstrumentsResponse,
    ExchangeInstrumentSummary,
    ExchangeLeverageInfoResponse,
    ExchangeLeverageSummary,
    ExchangeOrderCancelResponse,
    ExchangeOrderStatusResponse,
    ExchangePositionItem,
    ExchangePositionModeResponse,
    ExchangePositionsResponse,
    ExchangeStatusResponse,
)
from app.security.rbac import OwnerDep, ReaderDep
from app.services.audit_service import AuditService
from app.services.exchange_diagnostics_service import build_exchange_diagnostics_summary

router = APIRouter(prefix="/exchange", tags=["exchange"])


def _now() -> datetime:
    return datetime.now(UTC)


def _map_instrument(item: ExchangeInstrument) -> ExchangeInstrumentItem:
    return ExchangeInstrumentItem(
        symbol=item.symbol,
        inst_id=item.inst_id,
        base_currency=item.base_currency,
        quote_currency=item.quote_currency,
        instrument_type=item.instrument_type,
        tick_size=item.tick_size,
        lot_size=item.lot_size,
        min_size=item.min_size,
        contract_size=item.contract_size,
        active=item.active,
    )


def _map_balance(item: ExchangeBalance) -> ExchangeBalanceItem:
    return ExchangeBalanceItem(asset=item.asset, total=item.total, available=item.available)


def _map_position(item: ExchangePositionData) -> ExchangePositionItem:
    return ExchangePositionItem(
        symbol=item.symbol,
        inst_id=item.inst_id,
        side=item.side,
        size=item.size,
        entry_price=item.entry_price,
        mark_price=item.mark_price,
        unrealized_pnl=item.unrealized_pnl,
        leverage=item.leverage,
    )


def _map_diagnostics_response(snapshot) -> ExchangeDiagnosticsSummaryResponse:
    leverage = None
    if snapshot.leverage is not None:
        leverage = ExchangeLeverageSummary(
            inst_id=snapshot.leverage.inst_id,
            margin_mode=snapshot.leverage.margin_mode,
            leverage=snapshot.leverage.leverage,
            probe_ok=snapshot.leverage.probe_ok,
        )
    instrument = None
    if snapshot.instrument is not None:
        instrument = ExchangeInstrumentSummary(
            symbol=snapshot.instrument.symbol,
            inst_id=snapshot.instrument.inst_id,
            active=snapshot.instrument.active,
            probe_ok=snapshot.instrument.probe_ok,
        )
    return ExchangeDiagnosticsSummaryResponse(
        exchange_mode=snapshot.exchange_mode,
        execution_mode=snapshot.execution_mode,
        real_trading_enabled=snapshot.real_trading_enabled,
        demo_active=snapshot.demo_active,
        provider_health=snapshot.provider_health,
        worker_enabled=snapshot.worker_enabled,
        telegram_enabled=snapshot.telegram_enabled,
        position_mode=snapshot.position_mode,
        leverage=leverage,
        instrument=instrument,
        venue_positions_count=snapshot.venue_positions_count,
        last_exchange_order_status=snapshot.last_exchange_order_status,
        last_demo_mirror_result=snapshot.last_demo_mirror_result,
        last_demo_mirror_error_code=snapshot.last_demo_mirror_error_code,
        last_demo_mirror_error_message=snapshot.last_demo_mirror_error_message,
        last_cancel_status=snapshot.last_cancel_status,
        readiness=snapshot.readiness,
        warnings=snapshot.warnings,
        generated_at=snapshot.generated_at,
    )


@router.get(
    "/diagnostics/summary",
    response_model=ExchangeDiagnosticsSummaryResponse,
    summary="Exchange demo diagnostics summary",
)
async def exchange_diagnostics_summary(
    tenant: OwnerDep,
    settings: SettingsDep,
    registry: ProviderRegistryDep,
    audit_service: AuditServiceDep,
) -> ExchangeDiagnosticsSummaryResponse:
    """Read-only operator summary of demo exchange readiness and mirror health."""
    from app.persistence.worker_runtime_state import (
        load_worker_runtime_row,
        project_worker_effective,
    )

    snapshot = build_exchange_diagnostics_summary(
        settings=settings,
        registry=registry,
        audit_service=audit_service,
        organization_id=tenant.organization_id,
    )
    response = _map_diagnostics_response(snapshot)
    effective = project_worker_effective(
        load_worker_runtime_row(),
        now=datetime.now(UTC),
        stale_after_seconds=settings.watcher_heartbeat_stale_after_seconds,
    )
    telegram_enabled = response.telegram_enabled
    if effective.source == "worker":
        telegram_enabled = any(
            (
                effective.telegram_alerts_enabled,
                effective.telegram_interaction_enabled,
                effective.automatic_telegram_delivery_enabled,
                effective.telegram_paper_activation_armed,
            )
        )
    return response.model_copy(
        update={"worker_effective": effective, "telegram_enabled": telegram_enabled}
    )


@router.get("/status", response_model=ExchangeStatusResponse, summary="Exchange status")
async def exchange_status(
    _tenant: OwnerDep,
    settings: SettingsDep,
    registry: ProviderRegistryDep,
) -> ExchangeStatusResponse:
    """Return redacted exchange posture and provider health (no secrets)."""
    return ExchangeStatusResponse(
        exchange_mode=settings.exchange_mode.value,
        execution_mode=settings.execution_mode.value,
        real_trading_enabled=settings.real_trading_enabled,
        blofin_demo_enabled=settings.blofin_demo_enabled,
        demo_active=settings.exchange_demo_active,
        api_key_configured=bool(settings.blofin_api_key.strip()),
        api_secret_configured=bool(settings.blofin_api_secret.strip()),
        api_passphrase_configured=bool(settings.blofin_api_passphrase.strip()),
        credentials_configured=settings.blofin_demo_configured,
        provider=exchange_provider_status(registry),
        generated_at=_now(),
    )


@router.get(
    "/instruments",
    response_model=ExchangeInstrumentsResponse,
    summary="List BloFin demo instruments",
)
async def list_instruments(
    _tenant: OwnerDep,
    settings: SettingsDep,
    symbol: str | None = Query(default=None, description="Optional platform symbol filter."),
) -> ExchangeInstrumentsResponse:
    """Return safe instrument sizing fields for demo order planning (no secrets)."""
    account = get_demo_account_provider(settings)
    instruments = run_demo_provider_call("instruments", account.get_instruments)
    if symbol:
        normalized = symbol.strip().upper()
        instruments = [i for i in instruments if i.symbol.upper() == normalized]
    return ExchangeInstrumentsResponse(
        items=[_map_instrument(i) for i in instruments],
        generated_at=_now(),
    )


@router.get(
    "/balances",
    response_model=ExchangeBalancesResponse,
    summary="BloFin demo account balances (redacted summary)",
)
async def list_balances(
    _tenant: OwnerDep,
    settings: SettingsDep,
) -> ExchangeBalancesResponse:
    """Return per-asset totals and available balances only (no raw account payload)."""
    account = get_demo_account_provider(settings)
    balances = run_demo_provider_call("balances", account.get_balances)
    return ExchangeBalancesResponse(
        items=[_map_balance(b) for b in balances],
        generated_at=_now(),
    )


@router.get(
    "/positions",
    response_model=ExchangePositionsResponse,
    summary="Open BloFin demo positions",
)
async def list_positions(
    _tenant: OwnerDep,
    settings: SettingsDep,
) -> ExchangePositionsResponse:
    """Return open demo positions with safe derived fields only."""
    account = get_demo_account_provider(settings)
    positions = run_demo_provider_call("positions", account.get_positions)
    return ExchangePositionsResponse(
        items=[_map_position(p) for p in positions],
        generated_at=_now(),
    )


@router.get(
    "/account/position-mode",
    response_model=ExchangePositionModeResponse,
    summary="BloFin demo account position mode",
)
async def get_position_mode(
    _tenant: OwnerDep,
    settings: SettingsDep,
) -> ExchangePositionModeResponse:
    """Return one-way vs hedge position mode (read-only; no account mutation)."""
    account = get_demo_account_provider(settings)
    mode = run_demo_provider_call("position mode", account.get_position_mode)
    return ExchangePositionModeResponse(
        position_mode=mode.position_mode,
        generated_at=_now(),
    )


@router.get(
    "/account/leverage-info",
    response_model=ExchangeLeverageInfoResponse,
    summary="BloFin demo leverage for an instrument",
)
async def get_leverage_info(
    _tenant: OwnerDep,
    settings: SettingsDep,
    inst_id: str = Query(default="BTC-USDT", description="BloFin instrument id."),
    margin_mode: str = Query(default="cross", description="Margin mode (cross or isolated)."),
) -> ExchangeLeverageInfoResponse:
    """Return configured leverage for an instrument (read-only; no account mutation)."""
    account = get_demo_account_provider(settings)
    normalized_inst = to_blofin_inst_id(inst_id) if "-" not in inst_id else inst_id.upper()
    info = run_demo_provider_call(
        "leverage info",
        lambda: account.get_leverage_info(inst_id=normalized_inst, margin_mode=margin_mode),
    )
    return ExchangeLeverageInfoResponse(
        inst_id=info.inst_id,
        margin_mode=info.margin_mode,
        leverage=info.leverage,
        position_side=info.position_side,
        generated_at=_now(),
    )


def _map_order_status_response(
    *,
    inst_id: str,
    result: ExchangeOrderResult,
    status_source: str = "venue",
) -> ExchangeOrderStatusResponse:
    return ExchangeOrderStatusResponse(
        inst_id=inst_id,
        exchange_order_id=result.exchange_order_id,
        client_order_id=result.client_order_id,
        status=result.status,
        filled_size=result.filled_size,
        average_price=result.average_price,
        status_source=status_source,
        generated_at=_now(),
    )


def _has_cancel_audit(
    audit_service: AuditService,
    *,
    organization_id: uuid.UUID,
    exchange_order_id: str,
) -> bool:
    records, total = audit_service.list_records(
        organization_id=organization_id,
        request_id=f"cancel-{exchange_order_id}",
        event_type=AuditEventType.EXCHANGE_DEMO_ORDER_CANCELLED,
        limit=1,
    )
    return total > 0 and any(r.resource_id == exchange_order_id for r in records)


@router.get(
    "/orders/{inst_id}/{exchange_order_id}",
    response_model=ExchangeOrderStatusResponse,
    summary="BloFin demo order status",
)
async def get_order_status(
    inst_id: str,
    exchange_order_id: str,
    tenant: OwnerDep,
    settings: SettingsDep,
    audit_service: AuditServiceDep,
    after_cancel: bool = Query(
        default=False,
        description="When true, retry transient venue probe failures and fall back to "
        "cancelled if a matching EXCHANGE_DEMO_ORDER_CANCELLED audit exists.",
    ),
) -> ExchangeOrderStatusResponse:
    """Read-only demo order status via the venue (no secrets)."""
    execution = get_demo_execution_provider(settings)
    normalized_inst = to_blofin_inst_id(inst_id) if "-" not in inst_id else inst_id.upper()

    def get_order() -> ExchangeOrderResult:
        return execution.get_order(
            inst_id=normalized_inst,
            exchange_order_id=exchange_order_id,
        )

    if after_cancel:
        try:
            result = probe_demo_order_status_with_retry(get_order)
        except ExchangeError as exc:
            if _has_cancel_audit(
                audit_service,
                organization_id=tenant.organization_id,
                exchange_order_id=exchange_order_id,
            ):
                result = cancelled_status_from_cancel_audit(
                    exchange_order_id=exchange_order_id,
                )
                return _map_order_status_response(
                    inst_id=normalized_inst,
                    result=result,
                    status_source="cancel_audit_fallback",
                )
            raise ExchangeProviderError(
                "BloFin demo order status unavailable.",
                details={"error": redact_text(str(exc))[:200]},
            ) from exc
        return _map_order_status_response(inst_id=normalized_inst, result=result)

    result = run_demo_provider_call("order status", get_order)
    return _map_order_status_response(inst_id=normalized_inst, result=result)


@router.post(
    "/orders/{inst_id}/{exchange_order_id}/cancel",
    response_model=ExchangeOrderCancelResponse,
    summary="Cancel a BloFin demo order",
)
async def cancel_order(
    inst_id: str,
    exchange_order_id: str,
    tenant: OwnerDep,
    settings: SettingsDep,
    audit_service: AuditServiceDep,
    session: SessionDep,
) -> ExchangeOrderCancelResponse:
    """Cancel an existing demo venue order. Demo-gated; never places a new order."""
    execution = get_demo_execution_provider(settings)
    normalized_inst = to_blofin_inst_id(inst_id) if "-" not in inst_id else inst_id.upper()
    run_demo_provider_call(
        "order cancel",
        lambda: execution.cancel_order(
            inst_id=normalized_inst,
            exchange_order_id=exchange_order_id,
        ),
    )
    audit_service.record(
        AuditRecordCreate(
            request_id=f"cancel-{exchange_order_id}",
            trace_id=f"cancel-{exchange_order_id}",
            event_type=AuditEventType.EXCHANGE_DEMO_ORDER_CANCELLED,
            resource_type="exchange_order",
            resource_id=exchange_order_id,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            actor_type=ActorType.USER,
            metadata={"inst_id": normalized_inst, "mode": "paper_exchange_demo"},
        )
    )
    session.commit()
    return ExchangeOrderCancelResponse(
        inst_id=normalized_inst,
        exchange_order_id=exchange_order_id,
        cancelled=True,
        generated_at=_now(),
    )


@router.post(
    "/blofin/sync",
    response_model=BloFinSyncResult,
    summary="Sync BloFin demo account/positions (read-only)",
)
async def blofin_demo_sync(
    tenant: OwnerDep,
    service: BloFinSyncServiceDep,
    session: SessionDep,
) -> BloFinSyncResult:
    """Persist a bounded read-only demo snapshot. Never places or cancels orders."""
    result = service.sync(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        request_id=f"blofin-sync-{tenant.organization_id}",
    )
    session.commit()
    return result


@router.get(
    "/blofin/sync/latest",
    response_model=BloFinSyncSnapshotItem,
    summary="Latest BloFin demo sync snapshot",
)
async def blofin_demo_sync_latest(
    tenant: ReaderDep,
    service: BloFinSyncServiceDep,
) -> BloFinSyncSnapshotItem:
    return service.latest(organization_id=tenant.organization_id)
