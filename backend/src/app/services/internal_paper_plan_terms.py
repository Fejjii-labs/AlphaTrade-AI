"""Existing internal paper plan semantics, shared by governed entry adapters.

This is a pure terms builder. It grants no Candidate, sizing, approval, execution,
or journal authority. Fee, funding, and slippage allowances stay zero. A profile
isolated plan stores stop-out loss plus the round-trip fee and the funding floor
in ``maximum_loss``, which the claim reserves as daily loss. A cross plan stores
price distance only. Neither path writes the shared ledger row.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid5

from app.schemas.common import TradeDirection
from app.schemas.trade_plan import (
    ContractType,
    EntryOrderType,
    EntrySide,
    MarginMode,
    MarketType,
    QuantityUnit,
    TimeInForce,
    TradePlanRevisionCreate,
)
from app.services.risk_policy.plan_sizing import stop_out_cash
from app.signal_fusion.candidate import Candidate

INTERNAL_PAPER_LOT_SIZE = Decimal("0.001")
INTERNAL_PAPER_TICK_SIZE = Decimal("0.10")
INTERNAL_PAPER_MIN_NOTIONAL = Decimal("5")
_NAMESPACE = UUID("c44ea7de-0008-4000-8000-a070e1100001")
_POLICY_VERSION = "paper-internal-entry/v1"


def build_internal_paper_terms(
    *,
    candidate: Candidate,
    account_id: UUID,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    quantity: Decimal,
    observed: datetime,
    now: datetime,
    valid_until: datetime,
    freshness: int,
    symbol: str,
    tick_size: Decimal,
    target_formula: str,
    presentation: dict[str, object],
    leverage: Decimal = Decimal("1"),
    margin_mode: MarginMode = MarginMode.CROSS,
) -> TradePlanRevisionCreate:
    """Shared internal paper semantics. No detector, sizing, approval, or execution."""
    is_long = candidate.direction is TradeDirection.LONG
    distance = abs(stop - entry)
    price_loss = quantity * distance
    maximum_loss = (
        stop_out_cash(quantity, entry, stop, include_book_buffer=False)
        if margin_mode is MarginMode.ISOLATED
        else price_loss
    )
    evidence_ids = (
        uuid5(_NAMESPACE, f"{candidate.evidence_window_hash}:trigger"),
        uuid5(_NAMESPACE, f"{candidate.evidence_window_hash}:quote"),
    )
    return TradePlanRevisionCreate.model_validate(
        {
            "account_id": account_id,
            "exchange_account_id": None,
            "strategy_version_id": candidate.strategy_version_id,
            "setup_definition_id": candidate.setup_definition_id,
            "candidate_id": candidate.candidate_id,
            "permission_attestation_id": uuid5(_NAMESPACE, f"permission:{candidate.candidate_id}"),
            "permission_attestation_version": "paper-internal-permissions/v1",
            "evidence_ids": evidence_ids,
            "evidence_venue": candidate.evidence_venue.value,
            "evidence_market": candidate.evidence_market.name,
            "evidence_instrument": candidate.evidence_instrument,
            "evidence_observed_at": observed,
            "evidence_freshness_seconds": freshness,
            "evidence_is_live": True,
            "evidence_fallback_used": False,
            "evidence_sequence_complete": True,
            "evidence_final": True,
            "execution_venue": "PAPER_INTERNAL",
            "execution_market": MarketType.PERPETUAL.value,
            "execution_instrument": symbol,
            "timeframe": candidate.timeframe.value,
            "instrument_mapping_version": f"paper-internal-{symbol.lower()}-v1",
            "side": EntrySide.BUY.value if is_long else EntrySide.SELL.value,
            "quantity": {"value": str(quantity), "unit": QuantityUnit.BASE.value},
            "quantity_unit": QuantityUnit.BASE.value,
            "order_type": EntryOrderType.MARKET.value,
            "time_in_force": TimeInForce.IOC.value,
            "limit_price": None,
            "market_marker": True,
            "entry_zone": {"lower": str(entry), "upper": str(entry), "price_unit": "USDT"},
            "entry_zone_derivation": {
                "formula_id": "fresh-perpetual-trade",
                "formula_version": "1",
            },
            "slippage_policy": {
                "policy_id": "paper-internal-exact",
                "policy_version": "1",
                "maximum_bps": "0",
            },
            "reduce_only": False,
            "margin_mode": margin_mode.value,
            "position_mode": "NET",
            "instrument_rules": {
                "contract_multiplier": "1",
                "contract_type": ContractType.LINEAR.value,
                "base_currency": symbol.removesuffix("USDT"),
                "quote_currency": "USDT",
                "settlement_currency": "USDT",
                "tick_size": str(tick_size),
                "lot_size": str(INTERNAL_PAPER_LOT_SIZE),
                "minimum_quantity": str(INTERNAL_PAPER_LOT_SIZE),
                "minimum_notional": str(INTERNAL_PAPER_MIN_NOTIONAL),
                "rules_version": "paper-internal-linear-v1",
            },
            "basis_policy": {
                "policy_id": "paper-internal-same-venue",
                "policy_version": "1",
                "evidence_price": {"value": str(entry), "unit": "USDT"},
                "execution_price": {"value": str(entry), "unit": "USDT"},
                "formula": "(execution_price-evidence_price)/evidence_price",
                "timestamp": observed,
                "tolerance_bps": "0",
                "freshness_seconds": freshness,
            },
            "risk_and_exits": {
                "risk_budget": {"value": str(price_loss), "unit": "USDT"},
                "maximum_loss": {"value": str(maximum_loss), "unit": "USDT"},
                "fee_allowance": {"value": "0", "unit": "USDT"},
                "funding_allowance": {"value": "0", "unit": "USDT"},
                "slippage_allowance": {"value": "0", "unit": "USDT"},
                "stop": {"value": str(stop), "unit": "USDT"},
                "targets": [
                    {
                        "order": 1,
                        "price": {"value": str(target), "unit": "USDT"},
                        "quantity_fraction": "1",
                        "derivation": {
                            "formula_id": target_formula,
                            "formula_version": "1",
                        },
                    }
                ],
                "runner": {
                    "enabled": False,
                    "activation_target_order": None,
                    "remaining_quantity_fraction": "0",
                    "rule_id": "paper-internal-no-runner",
                    "rule_version": "1",
                    "expression": "disabled",
                },
                "leverage": format(leverage, "f"),
                "margin_assumption_id": (
                    "paper-profile-isolated"
                    if margin_mode is MarginMode.ISOLATED
                    else "paper-internal-cash"
                ),
                "margin_assumption_version": "1",
            },
            "valid_from": now,
            "valid_until": valid_until,
            "calculation_inputs": [
                {
                    "name": "rounded_base_quantity",
                    "input_value": str(quantity),
                    "result_value": str(quantity),
                    "unit": QuantityUnit.BASE.value,
                    "formula_id": "floor-to-lot",
                    "formula_version": "1",
                    "precision": 3,
                    "rounding_mode": "ROUND_FLOOR",
                    "conservative_remainder": "0",
                }
            ],
            "execution_policy_version": _POLICY_VERSION,
            "presentation_metadata": presentation,
        }
    )
