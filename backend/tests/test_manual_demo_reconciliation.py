"""Native-shaped responses for the existing manual demo lifecycle; no live venue IO."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError, TradingPolicyError
from app.db.models import (
    AuditLog,
    ExecutionCommand,
    ExecutionFillFact,
    JournalTrade,
    KillSwitchState,
)
from app.interactive_agent.actions import RecordedTradeInput
from app.interactive_agent.contracts import AgentTurnRequest
from app.interactive_agent.recorded_trade import read_recorded_trade, route_recorded_trade
from app.interactive_agent.service import InteractiveAgentService
from app.schemas.common import JournalTradeSource, JournalTradeStatus, TradeDirection
from app.schemas.manual_demo import ManualDemoPreviewRequest
from app.services.manual_demo_history import ManualDemoHistoryService
from app.services.manual_demo_service import ManualDemoService
from tests.support.postgres_persistence import requires_postgres
from tests.test_manual_blofin_demo import confirmation
from tests.test_manual_blofin_demo import world as world

pytestmark = requires_postgres
LIVE_TIME = datetime(2026, 10, 8, 12, 56, 28, tzinfo=UTC)
LIVE_TS = str(int(LIVE_TIME.timestamp() * 1000))


@pytest.fixture
def native(world, monkeypatch):
    venue, provider = world[3:]
    venue.price = Decimal("82894")
    provider._clock = lambda: LIVE_TIME + timedelta(seconds=venue.clock_delta)
    original = venue.handle
    changes = {
        "order": {},
        "fill": {},
        "protection": {},
        "failure": None,
        "reads": [],
        "response": {},
    }

    def handler(request):
        changes["reads"].append((request.method, request.url.path))
        if venue.order is not None and changes["failure"] == request.url.path:
            return httpx.Response(400, json={"code": "51000", "msg": "dummy-secret raw payload"})
        result = original(request)
        content = result.json()
        if request.url.path.endswith("instruments"):
            content["data"][0].update(lotSize="0.1", minSize="0.1")
        if request.url.path.endswith("books"):
            content["data"][0]["ts"] = LIVE_TS
        if venue.order is not None:
            client = venue.order["clientOrderId"]
            if request.url.path.endswith("order-detail") or request.url.path.endswith(
                "orders-history"
            ):
                # Native order detail object, not a reflected POST request.
                content["data"] = {
                    "orderId": "28697026",
                    "clientOrderId": client,
                    "instId": "BTC-USDT",
                    "side": "buy",
                    "positionSide": "net",
                    "marginMode": "cross",
                    "orderType": "market",
                    "size": "0.100000000000000000",
                    "filledSize": "0.100000000000000000",
                    "averagePrice": "82894.000000000000000000",
                    "state": "filled",
                    "createTime": LIVE_TS,
                    "tpslId": "2411",
                    **changes["order"],
                }
                if request.url.path.endswith("orders-history"):
                    content["data"] = [content["data"]]
            elif request.url.path.endswith("fills-history"):
                content["data"] = [
                    {
                        "orderId": "28697026",
                        "tradeId": "7772187",
                        "instId": "BTC-USDT",
                        "side": "buy",
                        "positionSide": "net",
                        "fillPrice": "82894.000000000000000000",
                        "fillSize": "0.100000000000000000",
                        "fillPnl": "0.000000000000000000",
                        "fee": "0.004973640000000000",
                        # Explicit synthetic currency qualifies reported sums;
                        # this is not evidence that an actual omitted fee is USDT.
                        "feeCurrency": "USDT",
                        "ts": LIVE_TS,
                        **changes["fill"],
                    }
                ]
            elif request.url.path.endswith("orders-tpsl-pending"):
                content["data"] = [
                    {
                        "tpslId": "2411",
                        "clientOrderId": None,
                        "instId": "BTC-USDT",
                        "positionSide": "net",
                        "marginMode": "cross",
                        "side": "sell",
                        "size": "0.100000000000000000",
                        "state": "live",
                        "reduceOnly": "true",
                        "slTriggerPrice": "82000.000000000000000000",
                        "slOrderPrice": "-1.000000000000000000",
                        "tpTriggerPrice": "83000.000000000000000000",
                        "tpOrderPrice": "-1.000000000000000000",
                        "createTime": LIVE_TS,
                        **changes["protection"],
                    }
                ]
        if venue.order is not None and request.url.path in changes["response"]:
            content["data"] = changes["response"][request.url.path]
        return httpx.Response(200, json=content)

    monkeypatch.setattr(provider._client._transport, "handler", handler)
    return world, changes


def _service(world, session):
    return ManualDemoService(session, world[2], provider=world[4], clock=world[4]._clock)


def _submit(native):
    world, _ = native
    with world[0]() as session:
        plan = _service(world, session).preview(
            world[1],
            ManualDemoPreviewRequest(
                side="BUY",
                quantity="0.1",
                stop="82000",
                target="83000",
            ),
        )
    with world[0]() as session:
        status = _service(world, session).confirm(world[1], confirmation(plan))
    return plan, status


def _refresh(world, command_id):
    with world[0]() as session:
        return _service(world, session).reconcile(world[1], command_id)


def _mixed_paper(world, session):
    trade = JournalTrade(
        id=uuid4(),
        organization_id=world[1].organization_id,
        user_id=world[1].user_id,
        account_id=world[2].governed_blofin_demo_account_id,
        source=JournalTradeSource.PAPER_EXECUTION,
        exchange="PAPER_INTERNAL",
        symbol="BTCUSDT",
        timeframe="15m",
        direction=TradeDirection.LONG,
        entry_time=LIVE_TIME - timedelta(days=2),
        entry_price=Decimal("86073.10"),
        size=Decimal("0.005"),
        status=JournalTradeStatus.OPEN,
    )
    session.add(trade)
    session.commit()
    return trade


def _ask(world, session, message, **kwargs):
    return InteractiveAgentService(session, settings=world[2]).handle_turn(
        AgentTurnRequest(message=message, **kwargs),
        organization_id=world[1].organization_id,
        user_id=world[1].user_id,
    )


def test_native_fill_protection_repeated_reconciliation_and_journal(native):
    world, changes = native
    plan, first = _submit(native)
    assert first.status == "filled_protected", first.model_dump()
    assert first.venue_order_id == "28697026" and first.protection_order_ids == ("2411",)
    assert first.filled_quantity == Decimal("0.1") and first.average_fill_price == Decimal("82894")
    assert first.fees == Decimal("0.00497364") and not first.reconciliation_diagnostics
    changes["reads"].clear()
    for _ in range(3):
        current = _refresh(world, first.command_id)
        assert current.journal_trade_id == first.journal_trade_id
        assert current.venue_order_id == first.venue_order_id
    assert all(method == "GET" for method, _ in changes["reads"])
    assert world[3].post_count == 1
    with world[0]() as session:
        trade = session.get(JournalTrade, first.journal_trade_id)
        assert trade.trade_plan_revision_id == plan.revision_id
        assert trade.size == Decimal("0.0001") and trade.entry_time == LIVE_TIME
        assert trade.planned_targets == [{"price": "83000", "size_fraction": 1.0, "label": "TP1"}]
        assert trade.funding is trade.exit_price is trade.net_pnl is None
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
        assert session.scalar(select(func.count()).select_from(JournalTrade)) == 1
        assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 1
        receipts = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.redacted_metadata["operation"].as_string()
                    == "manual_demo_native_order_receipt"
                )
            )
        )
        assert len(receipts) == 1


@pytest.mark.parametrize("fee", ["-0.004973640000000000", "0.000000000000000000"])
def test_signed_native_fees_are_preserved_without_absolute_value_or_pnl(native, fee):
    world, changes = native
    changes["fill"]["fee"] = fee
    _, result = _submit(native)
    assert result.status == "filled_protected" and result.fees == Decimal(fee)
    with world[0]() as session:
        trade = session.get(JournalTrade, result.journal_trade_id)
        assert trade.fees == Decimal(fee) and trade.net_pnl is None


@pytest.mark.parametrize("currency", [None, "", "BTC"])
def test_unknown_entry_settlement_fee_preserves_raw_signed_fee_and_currency(native, currency):
    world, changes = native
    changes["fill"].update(fee="-0.00497364", feeCurrency=currency)
    _, result = _submit(native)
    assert result.status == "filled_protected"
    assert result.fees is result.entry_fees is result.fee_convention is None
    with world[0]() as session:
        trade = session.get(JournalTrade, result.journal_trade_id)
        assert trade.fees is trade.net_pnl is trade.gross_pnl is None
        raw = session.scalar(
            select(AuditLog).where(
                AuditLog.resource_id == str(result.command_id),
                AuditLog.redacted_metadata["operation"].as_string() == "manual_demo_actual_fill",
            )
        )
        assert Decimal(raw.redacted_metadata["fee"]) == Decimal("-0.00497364")
        assert raw.redacted_metadata["fee_currency"] == (currency or None)
        assert raw.redacted_metadata["fee_evidence_contract"] == "manual-demo-native-fee/v2"
        read = ManualDemoHistoryService(session).get(world[1], result.command_id).evidence
        assert read.fees is read.entry_fees is read.fee_convention is None
        assert any("entry fee currency" in reason for reason in read.missing_evidence)
    assert world[3].post_count == 1


def test_legacy_assumed_entry_currency_is_unavailable_until_actual_refresh(native, monkeypatch):
    world, _changes = native
    original = ManualDemoService._audit

    def legacy_entry(self, tenant, event, resource, action, metadata):
        if action == "manual_demo_actual_fill":
            metadata = dict(metadata)
            metadata.pop("fee_evidence_contract")
            metadata["fee_currency"] = "USDT"  # Legacy assumption, not native provenance.
        return original(self, tenant, event, resource, action, metadata)

    with monkeypatch.context() as scope:
        scope.setattr(ManualDemoService, "_audit", legacy_entry)
        _, first = _submit(native)
    assert first.entry_fees is first.fees is None
    with world[0]() as session:
        old = session.scalar(
            select(AuditLog).where(
                AuditLog.resource_id == str(first.command_id),
                AuditLog.redacted_metadata["operation"].as_string() == "manual_demo_actual_fill",
            )
        )
        old_id, old_metadata, old_hash = old.id, dict(old.redacted_metadata), old.payload_hash
        refreshed = _service(world, session).reconcile(world[1], first.command_id)
        assert refreshed.entry_fees == refreshed.fees == Decimal(".00497364")
        assert refreshed.fee_convention is None
        session.expire_all()
        preserved = session.get(AuditLog, old_id)
        assert preserved.redacted_metadata == old_metadata and preserved.payload_hash == old_hash
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
    assert world[3].post_count == 1


def test_native_entry_currency_change_cannot_overwrite_original_fee_receipt(native):
    world, changes = native
    _, first = _submit(native)
    changes["fill"]["feeCurrency"] = None
    with pytest.raises(TradingPolicyError, match="fee currency conflicts"):
        _refresh(world, first.command_id)
    with world[0]() as session:
        stored = session.scalar(
            select(AuditLog).where(
                AuditLog.resource_id == str(first.command_id),
                AuditLog.redacted_metadata["operation"].as_string() == "manual_demo_actual_fill",
            )
        )
        assert stored.redacted_metadata["fee_currency"] == "USDT"
        assert session.get(JournalTrade, first.journal_trade_id).fees == Decimal(".00497364")
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
    assert world[3].post_count == 1


@pytest.mark.parametrize(
    "endpoint,stage",
    [
        ("/api/v1/trade/order-detail", "order_lookup"),
        ("/api/v1/trade/fills-history", "fill_lookup"),
        ("/api/v1/trade/orders-tpsl-pending", "protection_lookup"),
    ],
)
def test_safe_structured_read_failure_then_same_order_recovery(native, endpoint, stage, caplog):
    world, changes = native
    world[3].open_position = True
    changes["failure"] = endpoint
    _, failed = _submit(native)
    assert failed.reconciliation_diagnostics[0].stage == stage
    diagnostic = failed.reconciliation_diagnostics[0]
    assert diagnostic.http_status == 400 and diagnostic.venue_error_code == "51000"
    assert diagnostic.endpoint_name == f"GET {endpoint}"
    assert "dummy-secret" not in failed.model_dump_json() + caplog.text
    assert "raw payload" not in failed.model_dump_json() + caplog.text
    assert "Do not resubmit" in " ".join(failed.missing_evidence)
    if stage == "protection_lookup":
        assert failed.journal_trade_id is not None and failed.filled_quantity == Decimal("0.1")
    else:
        assert failed.journal_trade_id is None and failed.filled_quantity == 0
    changes["failure"] = None
    recovered = _refresh(world, failed.command_id)
    assert recovered.status == "filled_protected"
    assert recovered.command_id == failed.command_id and not recovered.reconciliation_diagnostics
    if failed.journal_trade_id:
        assert recovered.journal_trade_id == failed.journal_trade_id
        with world[0]() as session:
            assert session.scalar(select(KillSwitchState.active))  # Never clear protection hold.
    assert world[3].post_count == 1


@pytest.mark.parametrize(
    "section,updates,stage,reason",
    [
        ("order", {"clientOrderId": "different"}, "order_parse", "order_identity_mismatch"),
        ("order", {"size": "1"}, "order_parse", "order_plan_mismatch"),
        ("order", {"filledSize": "NaN"}, "order_parse", "numeric_value_invalid"),
        ("order", {"state": "unknown"}, "order_parse", "order_state_unknown"),
        ("fill", {"orderId": "different"}, "fill_parse", "fill_identity_mismatch"),
        ("fill", {"fillSize": "0.2"}, "fill_parse", "fill_totals_incomplete"),
        ("fill", {"fee": "Infinity"}, "fill_parse", "numeric_value_invalid"),
        ("fill", {"ts": None}, "fill_parse", "native_timestamp_invalid"),
        ("fill", {"fillTime": "123"}, "fill_parse", "fill_timestamp_conflict"),
    ],
)
def test_invalid_native_order_and_fill_remain_explicit_holds(
    native, section, updates, stage, reason
):
    world, changes = native
    changes[section].update(updates)
    _, result = _submit(native)
    assert result.status == "reconciliation_unavailable_operator_hold"
    assert result.reconciliation_diagnostics[0].stage == stage
    assert result.reconciliation_diagnostics[0].reason_code == reason
    assert result.journal_trade_id is None
    with world[0]() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 0
    assert world[3].post_count == 1


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"tpslId": "unrelated"}, "protection_link_missing"),
        ({"slTriggerPrice": "81000"}, "protection_terms_mismatch"),
        ({"size": "0.01"}, "protection_terms_mismatch"),
        ({"slOrderPrice": None}, "numeric_value_invalid"),
        ({"createTime": "not-a-time"}, "native_timestamp_invalid"),
        ({"orderId": "another-entry"}, "protection_identity_conflict"),
    ],
)
def test_invalid_or_unlinked_protection_keeps_real_fill_and_journal(native, updates, reason):
    world, changes = native
    world[3].open_position = True
    changes["protection"].update(updates)
    _, result = _submit(native)
    assert result.status == "protection_failed_operator_hold"
    assert result.journal_trade_id and result.filled_quantity == Decimal("0.1")
    assert result.reconciliation_diagnostics[0].reason_code == reason
    assert result.protection != "verified" and world[3].post_count == 1


def test_changed_native_order_never_merges_two_fill_identities(native):
    world, changes = native
    _, first = _submit(native)
    changes["order"]["orderId"] = "another-entry"
    changes["fill"]["orderId"] = "another-entry"
    with pytest.raises(TradingPolicyError, match="No identities are merged"):
        _refresh(world, first.command_id)
    with world[0]() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
        assert session.get(JournalTrade, first.journal_trade_id).size == Decimal("0.0001")
    assert world[3].post_count == 1


def test_agent_selects_manual_demo_instead_of_older_simulator_and_followup(native):
    world, _ = native
    _, status = _submit(native)
    with world[0]() as session:
        paper = _mixed_paper(world, session)
        result = _ask(world, session, "Explain my latest manual BloFin demo BTCUSDT long trade")
        assert str(status.journal_trade_id) in {r.record_id for r in result.connections}
        assert str(paper.id) not in {r.record_id for r in result.connections}
        assert "86073.10" not in result.reply and "0.005" not in result.reply
        assert "0.1 contracts (0.0001 BTC)" in result.reply and "82,894.00" in result.reply
        assert "82000" in result.recorded_evidence and "83000" in result.recorded_evidence
        assert "recorded protection verified" in result.recorded_evidence
        assert "excluded from strategy performance" in result.recorded_evidence
        assert not result.execution_attempted and not result.authority_mutated
        session.commit()
        followup = _ask(
            world, session, "Explain that trade", conversation_id=result.conversation_id
        )
        assert str(status.journal_trade_id) in {r.record_id for r in followup.connections}
    assert world[3].post_count == 1


def test_later_read_outage_retains_fill_journal_and_reports_current_evidence_missing(native):
    world, changes = native
    _, first = _submit(native)
    changes["failure"] = "/api/v1/trade/order-detail"
    held = _refresh(world, first.command_id)
    assert (
        held.filled_quantity == Decimal("0.1") and held.journal_trade_id == first.journal_trade_id
    )
    assert "Previously verified fills are retained" in " ".join(held.missing_evidence)
    assert "No actual venue fill has been verified" not in " ".join(held.missing_evidence)
    with world[0]() as session:
        read = _ask(world, session, "Explain my latest manual BloFin demo trade")
        assert "0.1 contracts (0.0001 BTC)" in read.reply
        assert "latest native reconciliation" in read.recorded_evidence
        assert "recorded fills do not establish a current position" in read.reply
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
    assert world[3].post_count == 1


def test_verified_legacy_journal_link_and_target_shape_repair_stays_in_same_lifecycle(native):
    world, _ = native
    plan, first = _submit(native)
    with world[0]() as session:
        trade = session.get(JournalTrade, first.journal_trade_id)
        trade.trade_plan_revision_id = None
        trade.planned_targets = [{"price": "83000", "quantity_fraction": "1", "label": "TP1"}]
        session.commit()
    second = _refresh(world, first.command_id)
    assert second.journal_trade_id == first.journal_trade_id
    with world[0]() as session:
        trade = session.get(JournalTrade, first.journal_trade_id)
        assert trade.trade_plan_revision_id == plan.revision_id
        assert trade.planned_targets[0]["size_fraction"] == 1
        assert "quantity_fraction" not in trade.planned_targets[0]
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1


def test_regressed_native_fill_history_cannot_shrink_or_erase_journal(native):
    world, changes = native
    _, first = _submit(native)
    changes["order"].update(state="canceled", filledSize="0")
    changes["response"]["/api/v1/trade/fills-history"] = []
    with pytest.raises(TradingPolicyError, match="No identities are merged"):
        _refresh(world, first.command_id)
    with world[0]() as session:
        assert session.get(JournalTrade, first.journal_trade_id).size == Decimal("0.0001")
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
    assert world[3].post_count == 1


def test_native_partial_fills_smaller_than_submission_lot_converge_without_duplication(native):
    world, changes = native
    changes["order"].update(state="partially_filled", filledSize="0.05")
    changes["fill"].update(fillSize="0.05", fee="0.00248682")
    _, partial = _submit(native)
    assert partial.status == "partial_fill_protected_operator_hold"
    assert partial.filled_quantity == Decimal("0.05") and partial.remaining_quantity == Decimal(
        "0.05"
    )
    world[3].clock_delta = 1
    changes["order"].update(state="filled", filledSize="0.1")
    first_fill = {
        "orderId": "28697026",
        "tradeId": "7772187",
        "instId": "BTC-USDT",
        "side": "buy",
        "positionSide": "net",
        "fillPrice": "82894",
        "fillSize": "0.05",
        "fee": "0.00248682",
        "feeCurrency": "USDT",
        "ts": LIVE_TS,
    }
    changes["response"]["/api/v1/trade/fills-history"] = [
        first_fill,
        {**first_fill, "tradeId": "7772188", "ts": str(int(LIVE_TS) + 1000)},
    ]
    for _ in range(2):
        full = _refresh(world, partial.command_id)
        assert full.status == "filled_protected" and full.filled_quantity == Decimal("0.1")
        assert full.journal_trade_id == partial.journal_trade_id
    with world[0]() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 2
        assert session.scalar(select(func.count()).select_from(JournalTrade)) == 1
        trade = session.get(JournalTrade, partial.journal_trade_id)
        assert trade.size == Decimal("0.0001") and trade.fees == Decimal("0.00497364")
    assert world[3].post_count == 1


def test_equivalent_native_decimal_format_replays_existing_hash_without_rewriting_it(native):
    world, changes = native
    _, first = _submit(native)
    with world[0]() as session:
        original_hash = session.scalars(select(ExecutionFillFact.content_hash)).one()
    changes["fill"].update(fillSize="0.1", fillPrice="82894", fee="0.00497364")
    for _ in range(2):
        same = _refresh(world, first.command_id)
        assert same.journal_trade_id == first.journal_trade_id
        assert same.status == "filled_protected"
    with world[0]() as session:
        assert session.scalars(select(ExecutionFillFact.content_hash)).one() == original_hash
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
    assert world[3].post_count == 1


def test_replay_compares_full_native_precision_instead_of_rounded_database_amounts(native):
    world, changes = native
    changes["fill"]["fillPrice"] = "82894.000000001"
    _, first = _submit(native)
    changes["fill"]["fillPrice"] = "82894.000000002"
    with pytest.raises(ConflictError, match="conflicting content"):
        _refresh(world, first.command_id)
    with world[0]() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
        assert session.get(JournalTrade, first.journal_trade_id).size == Decimal("0.0001")
    assert world[3].post_count == 1


def test_exact_legacy_replay_can_record_precision_proof_without_rewriting_old_audit(native):
    world, _ = native
    _, first = _submit(native)
    with world[0]() as session:
        prior = session.scalars(
            select(AuditLog).where(
                AuditLog.redacted_metadata["operation"].as_string() == "manual_demo_actual_fill",
            )
        ).one()
        legacy_metadata = {
            k: v for k, v in prior.redacted_metadata.items() if k not in {"quantity", "price"}
        }
        prior.redacted_metadata = legacy_metadata
        legacy_id = prior.id
        session.commit()
    for _ in range(2):
        assert _refresh(world, first.command_id).journal_trade_id == first.journal_trade_id
    with world[0]() as session:
        assert session.get(AuditLog, legacy_id).redacted_metadata == legacy_metadata
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.redacted_metadata["operation"].as_string()
                    == "manual_demo_fill_representation_verified",
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    "endpoint,payload,stage,reason",
    [
        ("/api/v1/trade/order-detail", None, "order_lookup", "response_shape_invalid"),
        (
            "/api/v1/trade/fills-history",
            {"fillSize": "0.1"},
            "fill_lookup",
            "response_shape_invalid",
        ),
        ("/api/v1/trade/fills-history", ["unreadable"], "fill_lookup", "response_shape_invalid"),
        ("/api/v1/trade/fills-history", [{}] * 100, "fill_lookup", "native_identity_invalid"),
        ("/api/v1/trade/orders-tpsl-pending", None, "protection_lookup", "response_shape_invalid"),
        (
            "/api/v1/trade/orders-tpsl-pending",
            [{}] * 100,
            "protection_lookup",
            "response_page_incomplete",
        ),
    ],
)
def test_incomplete_or_malformed_native_pages_never_prove_absence(
    native, endpoint, payload, stage, reason
):
    world, changes = native
    changes["response"][endpoint] = payload
    _, status = _submit(native)
    assert status.reconciliation_diagnostics[0].stage == stage
    assert status.reconciliation_diagnostics[0].reason_code == reason
    if stage == "protection_lookup":
        assert status.filled_quantity == Decimal("0.1") and status.journal_trade_id
    else:
        assert status.filled_quantity == 0 and status.journal_trade_id is None
    assert world[3].post_count == 1


def test_manual_read_never_uses_model_or_previous_simulator_history(native):
    world, _ = native
    _, status = _submit(native)

    class ForbiddenResponder:
        def compose(self, **kwargs):
            pytest.fail("Manual native evidence must not be combined with model/history trades")

    with world[0]() as session:
        _mixed_paper(world, session)
        result = InteractiveAgentService(
            session, settings=world[2], responder=ForbiddenResponder()
        ).handle_turn(
            AgentTurnRequest(message="Explain my latest manual BloFin demo trade"),
            organization_id=world[1].organization_id,
            user_id=world[1].user_id,
        )
        assert str(status.journal_trade_id) in {r.record_id for r in result.connections}
        assert "Entry filled: 0.1 contracts (0.0001 BTC)" in result.reply


def test_agent_reports_existing_unreconciled_command_without_simulator_fallback(native):
    world, changes = native
    changes["failure"] = "/api/v1/trade/fills-history"
    _, status = _submit(native)
    with world[0]() as session:
        paper = _mixed_paper(world, session)
        result = _ask(world, session, "Explain my latest manual BloFin demo BTC long trade")
        assert str(status.command_id) in {r.record_id for r in result.connections}
        assert not any(r.record_id == str(paper.id) for r in result.connections)
        assert "No entry fill is verified" in result.reply
        assert "submitted order is not a filled position" in result.recorded_evidence
        assert (
            "86073" not in result.reply
            and "latest native reconciliation" in result.recorded_evidence
        )
    assert world[3].post_count == 1


def test_agent_missing_demo_match_does_not_use_a_model_to_substitute_paper(world):
    class ForbiddenResponder:
        def compose(self, **kwargs):
            pytest.fail(
                "No matching trade must not fall through to model history or simulator facts"
            )

    with world[0]() as session:
        _mixed_paper(world, session)
        result = InteractiveAgentService(
            session, settings=world[2], responder=ForbiddenResponder()
        ).handle_turn(
            AgentTurnRequest(message="Explain my latest manual BloFin demo trade"),
            organization_id=world[1].organization_id,
            user_id=world[1].user_id,
        )
        assert "No matching recorded manual BloFin demo" in result.reply
        assert "No internal simulator" in result.reply and not result.connections
        assert not result.execution_attempted and not result.authority_mutated


def test_explicit_venue_and_origin_constraints_apply_to_journal_ids(native):
    world, _ = native
    _, status = _submit(native)
    with world[0]() as session:
        paper = _mixed_paper(world, session)
        for trade_id, venue, origin in [
            (paper.id, "BLOFIN_DEMO", "manual_demo_test"),
            (status.journal_trade_id, "PAPER_INTERNAL", "manual_demo_test"),
            (status.journal_trade_id, "BLOFIN_DEMO", "paper_execution"),
        ]:
            read = read_recorded_trade(
                session,
                RecordedTradeInput(
                    journal_trade_id=trade_id,
                    execution_venue=venue,
                    trade_origin=origin,
                ),
                organization_id=world[1].organization_id,
                user_id=world[1].user_id,
            )
            assert not read.connections and "No recorded Journal trade matches" in read.reply


def test_explicit_demo_followup_cannot_inherit_previous_paper_identity(native):
    world, _ = native
    with world[0]() as session:
        paper = _mixed_paper(world, session)
        first = _ask(world, session, f"Explain recorded trade {paper.id}")
        session.commit()
        followup = _ask(
            world,
            session,
            "Explain that manual BloFin demo trade",
            conversation_id=first.conversation_id,
        )
        assert str(paper.id) not in {r.record_id for r in followup.connections}
        assert "No matching recorded manual BloFin demo" in followup.reply


@pytest.mark.parametrize(
    "message",
    [
        "Explain my latest manual BloFin demo trade",
        "Describe my last BTCUSDT long manual demo trade",
        "Explain my latest manual Blo Fin demo BTC long trade",
    ],
)
def test_manual_demo_routing_preserves_venue_origin_and_does_not_parse_them_as_symbols(message):
    request = route_recorded_trade(message, symbol=None)
    assert request.arguments["execution_venue"] == "BLOFIN_DEMO"
    assert request.arguments["trade_origin"] == "manual_demo_test"
    assert request.arguments["market_name"] not in {"MANUAL", "BLOFIN", "DEMO"}
