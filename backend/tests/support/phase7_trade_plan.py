"""Builders for Phase 7 canonical TradePlanRevision tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from app.schemas.canonical_trade_plan import CanonicalTradePlanCommand
from app.schemas.trade_plan import TradePlanRevisionCreate
from app.services.canonical_trade_plan import (
    CanonicalTradePlanService,
    in_memory_canonical_trade_plan,
)
from app.signal_fusion.action_eligibility import (
    ActionEligibilityEvaluation,
    ActionEligibilityService,
    in_memory_action_eligibility,
)
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.lifecycle import CandidateLifecycleService, in_memory_candidate_lifecycle
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase6_eligibility import eligibility_command
from tests.support.phase6_fusion import (
    ACCOUNT_ID,
    CORRELATION_A,
    ORG_ID,
    USER_ID,
    VALID_UNTIL,
    make_assessment,
    make_creation_command,
    make_evidence_window,
)

PLAN_EVIDENCE_NS = UUID("c33ea7de-0007-4000-8000-71ade01a00e1")
PERMISSION_ATTESTATION_ID = UUID("10000000-0000-0000-0000-000000000007")


@dataclass(frozen=True)
class CanonicalPlanWorld:
    lifecycle: CandidateLifecycleService
    eligibility: ActionEligibilityService
    plans: CanonicalTradePlanService
    candidate: Candidate
    evaluation: ActionEligibilityEvaluation


def make_world() -> CanonicalPlanWorld:
    """Shared lifecycle + eligibility + plan service over one ACTIVE candidate."""
    window = make_evidence_window()
    assessment = make_assessment(window)
    lifecycle = in_memory_candidate_lifecycle(now=EVALUATED_AT)
    candidate = lifecycle.create_from_confirmed_setup(
        make_creation_command(window=window, assessment=assessment)
    )
    eligibility = in_memory_action_eligibility(now=EVALUATED_AT)
    evaluation = eligibility.evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    plans = in_memory_canonical_trade_plan(
        now=EVALUATED_AT,
        lifecycle=lifecycle,
        eligibility=eligibility,
    )
    return CanonicalPlanWorld(
        lifecycle=lifecycle,
        eligibility=eligibility,
        plans=plans,
        candidate=candidate,
        evaluation=evaluation,
    )


def single_target_exits(candidate: Candidate) -> dict[str, Any]:
    """The fixture exits reduced to TP1 at full size with no runner.

    Governed demo dispatch attaches one full-size TP and refuses a ladder.
    """

    risk = plan_terms(candidate).risk_and_exits.model_dump(mode="json")
    risk["targets"] = [{**risk["targets"][0], "quantity_fraction": "1"}]
    risk["runner"] = {
        **risk["runner"],
        "enabled": False,
        "activation_target_order": None,
        "remaining_quantity_fraction": "0",
    }
    return risk


def plan_terms(
    candidate: Candidate, *, account_id: UUID = ACCOUNT_ID, **updates: Any
) -> TradePlanRevisionCreate:
    payload: dict[str, Any] = {
        "account_id": account_id,
        "exchange_account_id": None,
        "strategy_version_id": candidate.strategy_version_id,
        "setup_definition_id": candidate.setup_definition_id,
        "candidate_id": candidate.candidate_id,
        "permission_attestation_id": PERMISSION_ATTESTATION_ID,
        "permission_attestation_version": "paper-permissions-v1",
        "evidence_ids": [
            uuid5(PLAN_EVIDENCE_NS, f"{candidate.evidence_window_hash}:1"),
            uuid5(PLAN_EVIDENCE_NS, f"{candidate.evidence_window_hash}:2"),
        ],
        "evidence_venue": candidate.evidence_venue.value,
        "evidence_market": candidate.evidence_market.name,
        "evidence_instrument": candidate.evidence_instrument,
        "evidence_observed_at": EVALUATED_AT - timedelta(seconds=5),
        "evidence_freshness_seconds": 10,
        "evidence_is_live": True,
        "evidence_fallback_used": False,
        "evidence_sequence_complete": True,
        "evidence_final": True,
        "execution_venue": "BLOFIN_DEMO",
        "execution_market": "PERPETUAL",
        "execution_instrument": "BTC-USDT",
        "timeframe": candidate.timeframe.value,
        "instrument_mapping_version": "blofin-btc-usdt-v1",
        "side": "SELL",
        "quantity": {"value": "2.000", "unit": "CONTRACTS"},
        "quantity_unit": "CONTRACTS",
        "order_type": "MARKET",
        "time_in_force": "IOC",
        "limit_price": None,
        "market_marker": True,
        "entry_zone": {"lower": "99950", "upper": "100050", "price_unit": "USDT"},
        "entry_zone_derivation": {
            "formula_id": "pattern-trigger-intersection",
            "formula_version": "1",
        },
        "slippage_policy": {
            "policy_id": "conservative-entry",
            "policy_version": "1",
            "maximum_bps": "15.0",
        },
        "reduce_only": False,
        "margin_mode": "CROSS",
        "position_mode": "NET",
        "instrument_rules": {
            "contract_multiplier": "1",
            "contract_type": "LINEAR",
            "base_currency": "BTC",
            "quote_currency": "USDT",
            "settlement_currency": "USDT",
            "tick_size": "0.10",
            "lot_size": "1",
            "minimum_quantity": "1",
            "minimum_notional": "5",
            "rules_version": "blofin-rules-2026-09-15",
        },
        "basis_policy": {
            "policy_id": "cross-venue-basis",
            "policy_version": "1",
            "evidence_price": {"value": "100000", "unit": "USDT"},
            "execution_price": {"value": "100000", "unit": "USDT"},
            "formula": "(execution_price-evidence_price)/evidence_price",
            "timestamp": EVALUATED_AT - timedelta(seconds=4),
            "tolerance_bps": "20",
            "freshness_seconds": 10,
        },
        "risk_and_exits": {
            "risk_budget": {"value": "10", "unit": "USDT"},
            "maximum_loss": {"value": "12", "unit": "USDT"},
            "fee_allowance": {"value": "1", "unit": "USDT"},
            "funding_allowance": {"value": "0.25", "unit": "USDT"},
            "slippage_allowance": {"value": "0.75", "unit": "USDT"},
            "stop": {"value": "101000", "unit": "USDT"},
            "targets": [
                {
                    "order": 1,
                    "price": {"value": "98000", "unit": "USDT"},
                    "quantity_fraction": "0.50",
                    "derivation": {"formula_id": "nearest-structure", "formula_version": "1"},
                },
                {
                    "order": 2,
                    "price": {"value": "97000", "unit": "USDT"},
                    "quantity_fraction": "0.25",
                    "derivation": {"formula_id": "next-structure", "formula_version": "1"},
                },
            ],
            "runner": {
                "enabled": True,
                "activation_target_order": 2,
                "remaining_quantity_fraction": "0.25",
                "rule_id": "atr-trailing-runner",
                "rule_version": "1",
                "expression": "trail_by_atr_after_target_2",
            },
            "leverage": "2",
            "margin_assumption_id": "cross-margin-conservative",
            "margin_assumption_version": "1",
        },
        "valid_from": EVALUATED_AT,
        "valid_until": VALID_UNTIL,
        "calculation_inputs": [
            {
                "name": "rounded_contract_quantity",
                "input_value": "2.000",
                "result_value": "2",
                "unit": "CONTRACTS",
                "formula_id": "floor-to-lot",
                "formula_version": "1",
                "precision": 0,
                "rounding_mode": "ROUND_FLOOR",
                "conservative_remainder": "0",
            }
        ],
        "execution_policy_version": "paper-entry-policy-v1",
        "presentation_metadata": {"channel": "WEB", "display_title": "BTC setup"},
    }
    payload.update(updates)
    return TradePlanRevisionCreate.model_validate(payload)


def plan_command(
    world: CanonicalPlanWorld,
    *,
    idempotency_key: str = "canonical-plan-create-1",
    correlation_id: UUID = CORRELATION_A,
    terms: TradePlanRevisionCreate | None = None,
    organization_id: UUID = ORG_ID,
    user_id: UUID = USER_ID,
    account_id: UUID = ACCOUNT_ID,
    candidate_id: UUID | None = None,
    eligibility_id: UUID | None = None,
) -> CanonicalTradePlanCommand:
    return CanonicalTradePlanCommand(
        organization_id=organization_id,
        user_id=user_id,
        account_id=account_id,
        candidate_id=candidate_id or world.candidate.candidate_id,
        eligibility_id=eligibility_id or world.evaluation.eligibility.eligibility_id,
        terms=terms or plan_terms(world.candidate, account_id=account_id),
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )


def unused_uuid() -> UUID:
    return uuid4()
