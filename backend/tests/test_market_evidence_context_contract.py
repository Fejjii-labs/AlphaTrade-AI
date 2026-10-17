"""Stable serialization and the read-only experiment integration example."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from examples.experiment_market_evidence import experiment_evidence_record
from pydantic import ValidationError

from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.market_contracts.context import MarketEvidenceContext
from app.market_contracts.derivatives import DerivativeMetric, derivative_observation
from app.market_contracts.enums import VenueId
from app.market_contracts.errors import MarketContractError
from app.signal_fusion.enums import EvidenceRole
from tests.support.phase6_fusion import ORG_ID
from tests.test_market_intelligence_oi_funding import identity
from tests.test_required_derivative_acquisition import EVENT, SCAN, AdvancingClock, _policy, _source


def test_published_v1_serialization_schema_matches_typed_contract():
    target = (
        Path(__file__).resolve().parents[2]
        / "docs/contracts/market_evidence_context.v1.schema.json"
    )
    expected = MarketEvidenceContext.model_json_schema(mode="serialization")
    expected["$id"] = "urn:alphatrade:public-market-context:v1"
    assert json.loads(target.read_text()) == expected


@pytest.mark.parametrize("venue", (VenueId.BINANCE, VenueId.BYBIT))
def test_experiment_example_preserves_canonical_clocks_units_and_unavailable_metrics(venue):
    clock = AdvancingClock()
    source, counts = _source(venue, clock)
    assembled = FirstSliceEvidenceAssembler(source, replay=False, clock=clock).assemble(
        organization_id=ORG_ID, policy=_policy()
    )
    before = counts.copy()
    record = experiment_evidence_record(assembled, _policy())
    assert counts == before  # No second collection for the experiment consumer.
    assert record["detection_timeframe"] == "15m"
    assert record["evidence_cutoff_at"] == SCAN.isoformat()
    assert record["evidence_window_hash"] == assembled.evidence_window_hash
    context = MarketEvidenceContext.model_validate(record["market_context"])
    assert MarketEvidenceContext.model_validate_json(context.model_dump_json()) == context
    assert context.contract_version == "public-market-context/v1"
    assert context.qualification_authority is False
    assert context.evaluated_at == SCAN + timedelta(seconds=2)
    assert context.evidence_cutoff_at == SCAN
    assert all(x.event_time == EVENT and x.value is not None for x in context.derivatives)
    assert context.order_flow is None  # Optional missing executed flow stays missing.
    assert context.order_book is None
    for metric in (
        context.open_interest_change,
        context.open_interest_notional,
        context.historical_order_book,
    ):
        assert metric.availability == "UNSUPPORTED"
        assert metric.value is None
        assert metric.reason


def test_optional_experiment_context_cannot_replace_required_canonical_oi():
    clock = AdvancingClock()
    source, _ = _source(VenueId.BINANCE, clock)
    assembled = FirstSliceEvidenceAssembler(source, replay=False, clock=clock).assemble(
        organization_id=ORG_ID, policy=_policy()
    )
    optional = MarketEvidenceContext(
        evaluated_at=assembled.evaluated_at,
        anchor_venue="binance",
        anchor_symbol="BTCUSDT",
        derivatives=assembled.bundle.market_intelligence,
    )
    missing = assembled.model_copy(
        update={"bundle": assembled.bundle.model_copy(update={"market_intelligence": ()})}
    )
    with pytest.raises(MarketContractError, match="required_open_interest:MISSING"):
        experiment_evidence_record(missing, _policy(), optional_context=optional)
    book_required = _policy().model_copy(
        update={"required_roles": (*_policy().required_roles, EvidenceRole.ORDER_BOOK)}
    )
    with pytest.raises(MarketContractError, match="historical_snapshot_coverage_unavailable"):
        experiment_evidence_record(assembled, book_required, optional_context=optional)


def test_context_requires_unique_derivatives_and_explicit_cross_venue_labels():
    fact = derivative_observation(
        identity=identity(VenueId.BYBIT), metric=DerivativeMetric.OPEN_INTEREST, observed_at=SCAN
    )
    with pytest.raises(ValidationError, match="Cross-venue"):
        MarketEvidenceContext(
            evaluated_at=SCAN, anchor_venue="binance", anchor_symbol="BTCUSDT", derivatives=(fact,)
        )
    context = MarketEvidenceContext(
        evaluated_at=SCAN,
        anchor_venue="binance",
        anchor_symbol="BTCUSDT",
        derivatives=(fact,),
        cross_venue_components=("open_interest",),
    )
    assert context.derivatives[0].identity.venue is VenueId.BYBIT
    with pytest.raises(ValidationError, match="one explicit source"):
        MarketEvidenceContext(
            evaluated_at=SCAN,
            anchor_venue="bybit",
            anchor_symbol="BTCUSDT",
            derivatives=(fact, fact),
        )


def test_context_cannot_change_unavailable_metric_to_a_zero_or_grant_authority():
    base = {"evaluated_at": SCAN, "anchor_venue": "binance", "anchor_symbol": "BTCUSDT"}
    with pytest.raises(ValidationError):
        MarketEvidenceContext(
            **base,
            open_interest_change={
                "availability": "UNSUPPORTED",
                "value": "0",
                "reason": "no_history",
            },
        )
    with pytest.raises(ValidationError):
        MarketEvidenceContext(**base, qualification_authority=True)
    with pytest.raises(ValidationError, match="cutoff"):
        MarketEvidenceContext(**base, evidence_cutoff_at=SCAN + timedelta(seconds=1))
