"""One-off staging job: release governed BloFin demo sends stuck as SEND_AMBIGUOUS when
BloFin has no record of them. Dry run by default (everything rolled back).

A send refused by the provider's own preflight before any POST (for example "Executable
demo depth is outside the authorized entry range") used to stay SEND_AMBIGUOUS. Its coin
then stayed blocked by demo_prior_entry_requires_reconciliation. New sends are now
recorded as proven unsent; this releases the ones recorded before that fix.

An effect is released only when every check passes, otherwise it is left untouched:
  - the plan expired, so the same dispatch authority can never be used again;
  - order-detail for its clientOrderId returns no order;
  - its clientOrderId is not among the coin's pending orders;
  - its clientOrderId is not in the coin's complete order history (bounded, fail closed).
It reads BloFin only; it never sends, cancels or resends an order.

Run from backend/ against staging only:

    uv run --frozen python ../scripts/release_absent_demo_sends.py            # dry run
    uv run --frozen python ../scripts/release_absent_demo_sends.py --apply    # commit

Refuses when real trading is on or the environment is not staging. Prints no secrets.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any


def out(tag: str, payload: object) -> None:
    print(f"RELEASE|{tag}|{json.dumps(payload, default=str)[:2000]}", flush=True)


def _venue_has_order(client: Any, *, inst_id: str, client_order_id: str) -> str | None:
    """A reason when BloFin shows any trace of this order, else None."""
    from app.providers.exchange.demo_reconciliation import history_rows

    detail = client.request(
        "GET",
        "/api/v1/trade/order-detail",
        params={"instId": inst_id, "clientOrderId": client_order_id},
        signed=True,
    )
    if detail not in (None, [], {}):
        return "order_detail_found"
    pending = client.request(
        "GET", "/api/v1/trade/orders-pending", params={"instId": inst_id}, signed=True
    )
    if not isinstance(pending, list):
        return "pending_orders_unreadable"
    if any(isinstance(r, dict) and r.get("clientOrderId") == client_order_id for r in pending):
        return "pending_order_found"
    history = history_rows(
        client,
        endpoint="GET /api/v1/trade/orders-history",
        cursor_field="orderId",
        params={"instId": inst_id},
    )
    if any(r.get("clientOrderId") == client_order_id for r in history):
        return "order_history_found"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    parser.add_argument("--command-id", action="append", type=uuid.UUID, default=[])
    args = parser.parse_args(argv)

    from sqlalchemy import select

    from app.core.config import get_settings
    from app.db.models import ExecutionCommand, VenueSubmitEffect
    from app.db.models import TradePlanRevision as PlanRow
    from app.db.session import get_session_factory
    from app.providers.exchange.factory import build_blofin_client
    from app.schemas.execution_protocol import VenueSubmitEffectState
    from app.services.audit_service import AuditService
    from app.services.risk.settings_service import RiskSettingsService
    from app.services.safety_epoch import SafetyEpochService
    from app.services.venue_submit_dispatcher import VenueSubmitDispatcher

    settings = get_settings()
    out(
        "flags",
        {
            "apply": args.apply,
            "environment": settings.environment,
            "real_trading": settings.enable_real_trading,
        },
    )
    if settings.environment != "staging" or settings.enable_real_trading:
        out("REFUSED", "staging with real trading off only")
        return 2
    account_id = uuid.UUID(settings.governed_blofin_demo_account_id)
    client = build_blofin_client(settings)
    released = 0
    with get_session_factory()() as session:
        query = (
            select(ExecutionCommand.id, VenueSubmitEffect.client_order_id, PlanRow)
            .join(VenueSubmitEffect, VenueSubmitEffect.command_id == ExecutionCommand.id)
            .join(PlanRow, PlanRow.id == ExecutionCommand.revision_id)
            .where(
                ExecutionCommand.account_id == account_id,
                PlanRow.execution_venue == "BLOFIN_DEMO",
                VenueSubmitEffect.state == VenueSubmitEffectState.SEND_AMBIGUOUS,
            )
            .order_by(ExecutionCommand.created_at)
        )
        if args.command_id:
            query = query.where(ExecutionCommand.id.in_(args.command_id))
        rows = session.execute(query).all()
        session.rollback()
        out("candidates", len(rows))
        dispatcher = VenueSubmitDispatcher(
            session,
            SafetyEpochService(
                session, settings, RiskSettingsService(session, AuditService(session))
            ),
        )
        for command_id, client_order_id, plan in rows:
            row = {
                "command_id": str(command_id),
                "instrument": plan.execution_instrument,
                "valid_until": plan.valid_until,
            }
            try:
                found = _venue_has_order(
                    client, inst_id=plan.execution_instrument, client_order_id=client_order_id
                )
            except Exception as exc:  # unreadable venue never proves absence
                out("kept", {**row, "reason": f"venue_unreadable:{type(exc).__name__}"})
                continue
            if found is not None:
                out("kept", {**row, "reason": found})
                continue
            try:
                dispatcher.release_venue_absent_send(
                    command_id=command_id, plan_valid_until=plan.valid_until
                )
            except Exception as exc:
                session.rollback()
                out("kept", {**row, "reason": f"refused:{type(exc).__name__}:{exc}"[:200]})
                continue
            if args.apply:
                session.commit()
            else:
                session.rollback()
            released += 1
            out("released" if args.apply else "would_release", row)
    out("COMMITTED" if args.apply else "DRY_RUN_ROLLED_BACK", {"released": released})
    return 0


if __name__ == "__main__":
    sys.exit(main())
