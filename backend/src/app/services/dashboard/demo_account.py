"""Project saved demo account evidence without touching order reconciliation."""

import re
from contextlib import suppress
from datetime import UTC, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from uuid import UUID

from app.core.config import ExchangeMode, ExecutionMode, Settings
from app.core.errors import NotFoundError
from app.core.exchange_safety import is_allowlisted_demo_host
from app.schemas.blofin_sync import BloFinSyncSnapshotItem
from app.schemas.dashboard_demo_account import (
    DashboardDemoAccount,
    DemoAccountBalance,
    DemoAccountPosition,
)
from app.services.blofin_sync_service import BloFinSyncService

_TOKEN = re.compile(r"^[A-Za-z0-9_/-]{1,64}$")


def demo_account_active(settings: Settings) -> bool:
    """True when a paper demo snapshot may be shown or refreshed.

    ``paper_exchange_demo`` stays open when the dedicated read-only flag is on,
    so snapshot sync, the manual mirror, and autonomous demo orders can share
    one mode. Real trading and a non-allowlisted host stay closed.
    """
    if settings.execution_mode is not ExecutionMode.PAPER or settings.enable_real_trading:
        return False
    if settings.real_trading_enabled:
        return False
    if not is_allowlisted_demo_host(settings.blofin_demo_rest_base_url):
        return False
    if settings.exchange_mode is ExchangeMode.PAPER_EXCHANGE_DEMO:
        dedicated = settings.blofin_readonly_sync_enabled and settings.blofin_readonly_configured
        return dedicated or settings.blofin_demo_configured
    if settings.blofin_readonly_sync_enabled:
        return (
            settings.exchange_mode is ExchangeMode.PAPER_INTERNAL
            and settings.blofin_readonly_configured
        )
    return False


def _number(value: Any, *, required: bool = False) -> Decimal | None:
    if value is None or value == "":
        if required:
            raise ValueError("Missing account quantity")
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid account quantity") from exc
    if not result.is_finite():
        raise ValueError("Invalid account quantity")
    return result


def _token(value: Any) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError("Invalid account instrument")
    return value


def _required_number(value: Any) -> Decimal:
    result = _number(value, required=True)
    assert result is not None
    return result


def _base_quantity(
    row: dict[str, Any], metadata: Any, size: Decimal
) -> tuple[str | None, Decimal | None, str | None]:
    """Convert only exact, verified native linear metadata saved with this sync."""
    if not isinstance(metadata, dict):
        return None, None, None
    if not isinstance(row.get("inst_id"), str):
        return None, None, None
    info = metadata.get(row["inst_id"])
    if not isinstance(info, dict):
        return None, None, None
    try:
        base = _token(info.get("base_asset"))
        quote = _token(info.get("quote_asset"))
        inst_id = info.get("inst_id")
        if (
            inst_id != row.get("inst_id")
            or inst_id not in {f"{base}-{quote}", f"{base}-{quote}-SWAP"}
            or row.get("symbol") != f"{base}{quote}"
            or info.get("contract_type") != "linear"
            or info.get("source") != "/api/v1/market/instruments"
        ):
            return None, None, None
        multiplier = _required_number(info.get("contract_value"))
        if multiplier <= 0:
            return None, None, None
        with localcontext() as context:
            context.prec = max(28, len(size.as_tuple().digits) + len(multiplier.as_tuple().digits))
            return base, size.copy_abs() * multiplier, quote
    except ValueError:
        return None, None, None


def project_demo_account(
    snapshot: BloFinSyncSnapshotItem, *, settings: Settings, can_refresh: bool
) -> DashboardDemoAccount:
    synced_at = snapshot.synced_at
    if synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=UTC)
    base = DashboardDemoAccount(
        status="unavailable",
        can_refresh=can_refresh,
        snapshot_id=snapshot.id,
        synced_at=synced_at,
        expires_at=synced_at + timedelta(seconds=settings.blofin_sync_stale_after_seconds),
        message="The latest demo account sync failed. Refresh to retrieve current account data.",
    )
    binding = snapshot.provenance.get("configured_execution_account_id")
    if isinstance(binding, str):
        with suppress(ValueError):
            base.account_id = UUID(binding)
    if (
        snapshot.health_status.value == "unavailable"
        or snapshot.provider != "blofin_demo"
        or snapshot.exchange_mode
        not in {
            ExchangeMode.PAPER_EXCHANGE_DEMO.value,
            ExchangeMode.PAPER_INTERNAL.value,
        }
        or snapshot.provenance.get("read_only") is not True
        or snapshot.provenance.get("order_mutations") is not False
    ):
        return base
    try:
        balance_rows = snapshot.account_snapshot.get("balances")
        position_rows = snapshot.positions_snapshot.get("items")
        if not isinstance(balance_rows, list) or not isinstance(position_rows, list):
            raise ValueError("Missing account rows")
        balances: list[DemoAccountBalance] = []
        positions: list[DemoAccountPosition] = []
        for row in balance_rows[: settings.blofin_sync_max_balances]:
            if not isinstance(row, dict):
                raise ValueError("Invalid balance row")
            balances.append(
                DemoAccountBalance(
                    asset=_token(row.get("asset")),
                    total=_required_number(row.get("total")),
                    available=_required_number(row.get("available")),
                    equity=_number(row.get("equity")),
                )
            )
        for row in position_rows[: settings.blofin_sync_max_positions]:
            if not isinstance(row, dict):
                raise ValueError("Invalid position row")
            size = _required_number(row.get("size"))
            if size == 0:
                continue
            side = row.get("side")
            if side == "net":
                side = "long" if size > 0 else "short"
            if side not in {"long", "short"}:
                raise ValueError("Unknown position side")
            base_asset, base_quantity, quote_asset = _base_quantity(
                row, snapshot.positions_snapshot.get("instrument_metadata"), size
            )
            positions.append(
                DemoAccountPosition(
                    symbol=_token(row.get("symbol")),
                    side=side,
                    contracts=size.copy_abs(),
                    base_asset=base_asset,
                    quote_asset=quote_asset,
                    base_quantity=base_quantity,
                    entry_price=_number(row.get("entry_price")),
                    mark_price=_number(row.get("mark_price")),
                    unrealized_pnl=_number(row.get("unrealized_pnl")),
                    leverage=_number(row.get("leverage")),
                )
            )
        base.total_equity_usd = _number(snapshot.account_snapshot.get("total_equity_usd"))
    except (ValueError, InvalidOperation):
        base.message = "Demo account evidence is incomplete. Refresh to retrieve current data."
        return base
    base.balances = balances
    base.positions = positions
    base.balances_truncated = snapshot.account_snapshot.get("balances_truncated") is True or len(
        balance_rows
    ) > len(balances)
    base.positions_truncated = (
        snapshot.positions_snapshot.get("truncated") is True
        or len(position_rows) > settings.blofin_sync_max_positions
    )
    base.position_count = None if base.positions_truncated else len(positions)
    base.status = "stale" if snapshot.is_stale else snapshot.health_status.value
    base.message = (
        "Saved demo account snapshot is stale. Refresh for current balances and positions."
        if base.status == "stale"
        else "Native demo account snapshot. Positions may include trades placed outside AlphaTrade."
    )
    return base


def preserved_demo_account(
    snapshot: BloFinSyncSnapshotItem,
    *,
    service: BloFinSyncService,
    organization_id: UUID,
    settings: Settings,
    can_refresh: bool,
) -> DashboardDemoAccount:
    """Dashboard alone retains successful evidence after a failed saved attempt."""
    current = project_demo_account(snapshot, settings=settings, can_refresh=can_refresh)
    if current.status != "unavailable":
        return current
    try:
        previous = service.latest(organization_id=organization_id, successful_only=True)
    except NotFoundError:
        return current
    saved = project_demo_account(previous, settings=settings, can_refresh=can_refresh)
    if (
        saved.status not in {"ok", "degraded", "stale"}
        or previous.exchange_mode != snapshot.exchange_mode
    ):
        return current
    saved.status = "stale"
    saved.last_attempt_at = current.synced_at
    saved.refresh_error = (
        "The latest demo account sync failed. Showing the last successful snapshot."
    )
    saved.message = saved.refresh_error
    return saved
