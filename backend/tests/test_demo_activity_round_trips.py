"""Closed round-trip win rate from fixture fills, including partials and fees."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.db.blofin_activity import BloFinActivityAccount, BloFinActivityCursor, BloFinActivityFact
from app.schemas.common import MembershipRole
from app.schemas.dashboard_demo_account import DashboardDemoAccount
from app.security.tenant import TenantContext
from app.services.blofin_activity_config import BloFinActivitySettings
from app.services.dashboard.activity_round_trips import (
    ActivityFill,
    ActivityOrder,
    account_round_trips,
)
from app.services.dashboard.demo_activity_performance import apply_activity_performance


def fill(native_id: str, **overrides: object) -> ActivityFill:
    values: dict[str, object] = {
        "native_id": native_id,
        "order_id": native_id,
        "instrument": "BTC-USDT",
        "side": "buy",
        "position_side": "net",
        "quantity": Decimal("1"),
        "fee": Decimal("-0.01"),
        "fee_currency": "USDT",
        "realized_pnl": Decimal("0"),
        "occurred_at_ms": 1_000,
        "settlement_currency": "USDT",
    }
    values.update(overrides)
    return ActivityFill(**values)  # type: ignore[arg-type]


def test_partial_fills_and_fees_make_one_win_and_one_loss() -> None:
    fills = [
        fill(
            "f1",
            order_id="o1",
            quantity=Decimal("0.1"),
            fee=Decimal("-0.01"),
            occurred_at_ms=1_000,
        ),
        fill(
            "f2",
            order_id="o1",
            quantity=Decimal("0.3"),
            fee=Decimal("-0.02"),
            occurred_at_ms=1_001,
        ),
        fill(
            "f3",
            order_id="o2",
            side="sell",
            quantity=Decimal("0.2"),
            fee=Decimal("-0.04"),
            realized_pnl=Decimal("2"),
            occurred_at_ms=2_000,
        ),
        fill(
            "f4",
            order_id="o2",
            side="sell",
            quantity=Decimal("0.2"),
            fee=Decimal("-0.04"),
            realized_pnl=Decimal("4"),
            occurred_at_ms=2_001,
        ),
        fill(
            "e1",
            order_id="e-open",
            instrument="ETH-USDT",
            quantity=Decimal("1"),
            fee=Decimal("-0.01"),
            occurred_at_ms=3_000,
        ),
        fill(
            "e2",
            order_id="e-close",
            instrument="ETH-USDT",
            side="sell",
            quantity=Decimal("1"),
            fee=Decimal("-0.01"),
            realized_pnl=Decimal("-3"),
            occurred_at_ms=4_000,
        ),
        fill(
            "open",
            order_id="still-open",
            quantity=Decimal("0.5"),
            occurred_at_ms=5_000,
        ),
    ]
    orders = [
        ActivityOrder("o1", Decimal("0.4")),
        ActivityOrder("o2", Decimal("0.4")),
        ActivityOrder("e-open", Decimal("1")),
        ActivityOrder("e-close", Decimal("1")),
        ActivityOrder("still-open", Decimal("0.5")),
    ]
    report = account_round_trips(fills, orders)
    assert report.trade_count == 2
    assert report.wins == 1 and report.losses == 1 and report.scratches == 0
    assert report.win_rate == Decimal("0.5000")
    assert report.realized_pnl == Decimal("2.87000000")
    assert report.fees == Decimal("-0.13000000")
    assert report.unresolved == 0
    assert report.window_start == datetime.fromtimestamp(1, tz=UTC)
    assert report.window_end == datetime.fromtimestamp(4, tz=UTC)


def test_crossing_fill_splits_fee_and_pnl_and_leaves_the_remainder_open() -> None:
    report = account_round_trips(
        [
            fill("open", quantity=Decimal("1"), fee=Decimal("-0.10"), occurred_at_ms=1_000),
            fill(
                "cross",
                order_id="cross",
                side="sell",
                quantity=Decimal("2"),
                fee=Decimal("-0.20"),
                realized_pnl=Decimal("4"),
                occurred_at_ms=2_000,
            ),
        ],
        [ActivityOrder("open", Decimal("1")), ActivityOrder("cross", Decimal("2"))],
    )
    assert report.trade_count == 1 and report.wins == 1
    assert report.realized_pnl == Decimal("1.80000000")
    assert report.fees == Decimal("-0.20000000")


def test_missing_fee_or_currency_is_unresolved_and_a_size_mismatch_is_excluded() -> None:
    missing_fee = account_round_trips(
        [
            fill("a", fee=None, fee_currency=None, occurred_at_ms=1),
            fill("b", side="sell", realized_pnl=Decimal("1"), occurred_at_ms=2),
        ],
        [ActivityOrder("a", Decimal("1")), ActivityOrder("b", Decimal("1"))],
    )
    assert missing_fee.trade_count == 0 and missing_fee.unresolved == 1
    assert missing_fee.win_rate is None and missing_fee.realized_pnl is None

    wrong_currency = account_round_trips(
        [
            fill("a", fee_currency="BTC", settlement_currency="USDT"),
            fill(
                "b",
                side="sell",
                realized_pnl=Decimal("1"),
                fee_currency="BTC",
                settlement_currency="USDT",
            ),
        ],
        [ActivityOrder("a", Decimal("1")), ActivityOrder("b", Decimal("1"))],
    )
    assert wrong_currency.unresolved == 1 and wrong_currency.trade_count == 0

    mismatch = account_round_trips(
        [
            fill("a", order_id="bad", quantity=Decimal("0.5")),
            fill(
                "b",
                order_id="bad",
                side="sell",
                quantity=Decimal("0.5"),
                realized_pnl=Decimal("1"),
            ),
        ],
        [ActivityOrder("bad", Decimal("9"))],
    )
    assert mismatch.order_mismatches == 1
    assert mismatch.trade_count == 0 and mismatch.unresolved == 1


def test_activity_disabled_withholds_numbers() -> None:
    account = DashboardDemoAccount(status="ok", message="snapshot")

    class ExplodingSession:
        def get(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("disabled activity queried history")

        def scalars(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("disabled activity queried history")

    apply_activity_performance(
        account,
        session=ExplodingSession(),  # type: ignore[arg-type]
        tenant=TenantContext(uuid4(), uuid4(), "owner@example.test", MembershipRole.OWNER),
        config=BloFinActivitySettings(_env_file=None, enabled=False),
    )
    assert account.performance.activity_sync_status == "disabled"
    assert account.performance.win_rate is None
    assert account.performance.trade_count is None
    assert account.performance.realized_pnl is None
    assert "disabled" in account.performance.activity_coverage.lower()


def test_verified_fixture_rows_report_the_win_rate() -> None:
    org = uuid4()
    now = datetime(2026, 10, 18, tzinfo=UTC)
    account_row = BloFinActivityAccount(
        organization_id=org,
        environment="demo",
        account_uid="demo-uid",
        credential_binding="binding",
        identity_verified_at=now,
    )
    cursor = BloFinActivityCursor(
        organization_id=org,
        environment="demo",
        account_uid="demo-uid",
        kind="fill",
        window_begin_ms=1_000,
        window_end_ms=4_000,
        seen_cursors=[],
        window_complete=True,
        covered_begin_ms=1_000,
        covered_end_ms=4_000,
        gap_detected=False,
        last_successful_sync=now,
    )
    rows = [_row(org, kind, native_id, payload) for kind, native_id, payload in _payloads()]

    class Session:
        def get(self, model: object, _key: object) -> object:
            if model is BloFinActivityAccount:
                return account_row
            if model is BloFinActivityCursor:
                return cursor
            raise AssertionError(model)

        def scalars(self, _stmt: object) -> list[BloFinActivityFact]:
            return rows

    view = DashboardDemoAccount(status="ok", message="snapshot")
    apply_activity_performance(
        view,
        session=Session(),  # type: ignore[arg-type]
        tenant=TenantContext(uuid4(), org, "owner@example.test", MembershipRole.OWNER),
        config=BloFinActivitySettings(
            _env_file=None, enabled=True, organization_id=org, expected_uid="demo-uid"
        ),
    )
    performance = view.performance
    assert performance.activity_sync_status == "ready"
    assert performance.win_rate == Decimal("0.5000")
    assert performance.trade_count == 2
    assert performance.wins == 1 and performance.losses == 1
    assert performance.realized_pnl == Decimal("2.87000000")
    assert performance.window_start == datetime.fromtimestamp(1, tz=UTC)
    assert performance.window_end == datetime.fromtimestamp(4, tz=UTC)


def test_incomplete_history_does_not_invent_zero_pnl() -> None:
    org = uuid4()
    now = datetime(2026, 10, 18, tzinfo=UTC)
    account_row = BloFinActivityAccount(
        organization_id=org,
        environment="demo",
        account_uid="demo-uid",
        credential_binding="binding",
        identity_verified_at=now,
    )
    cursor = BloFinActivityCursor(
        organization_id=org,
        environment="demo",
        account_uid="demo-uid",
        kind="fill",
        window_begin_ms=1_000,
        window_end_ms=2_000,
        seen_cursors=[],
        window_complete=True,
        covered_begin_ms=1_000,
        covered_end_ms=2_000,
        gap_detected=False,
        last_successful_sync=now,
    )
    rows = [_row(org, "fill", "bad", {"kind": "nope", "order_id": "bad", "occurred_at_ms": "1000"})]

    class Session:
        def get(self, model: object, _key: object) -> object:
            if model is BloFinActivityAccount:
                return account_row
            if model is BloFinActivityCursor:
                return cursor
            raise AssertionError(model)

        def scalars(self, _stmt: object) -> list[BloFinActivityFact]:
            return rows

    view = DashboardDemoAccount(status="ok", message="snapshot")
    apply_activity_performance(
        view,
        session=Session(),  # type: ignore[arg-type]
        tenant=TenantContext(uuid4(), org, "owner@example.test", MembershipRole.OWNER),
        config=BloFinActivitySettings(
            _env_file=None, enabled=True, organization_id=org, expected_uid="demo-uid"
        ),
    )
    assert view.performance.activity_sync_status == "partial"
    assert view.performance.trade_count == 0
    assert view.performance.win_rate is None
    assert view.performance.realized_pnl is None


def _row(org: object, kind: str, native_id: str, payload: dict[str, object]) -> BloFinActivityFact:
    return BloFinActivityFact(
        organization_id=org,
        environment="demo",
        account_uid="demo-uid",
        kind=kind,
        native_id=native_id,
        order_id=str(payload["order_id"]),
        occurred_at_ms=int(str(payload["occurred_at_ms"])),
        content_hash="a" * 64,
        payload=payload,
        instrument_metadata={"settlement_currency": "USDT"},
        first_observed_at=datetime(2026, 10, 18, tzinfo=UTC),
    )


def _payloads() -> list[tuple[str, str, dict[str, object]]]:
    def item(kind: str, native_id: str, **extra: object) -> tuple[str, str, dict[str, object]]:
        payload: dict[str, object] = {
            "kind": kind,
            "native_id": native_id,
            "order_id": extra.pop("order_id", native_id),
            "instrument": extra.pop("instrument", "BTC-USDT"),
            "side": "buy",
            "position_side": "net",
            "occurred_at_ms": "1000",
            "quantity": "1",
            "fee": "-0.01",
            "fee_currency": "USDT",
            "realized_pnl": "0",
        }
        payload.update(extra)
        return kind, native_id, payload

    return [
        item("order", "o1", filled_quantity="0.4", quantity="0.4", occurred_at_ms="1000"),
        item("fill", "f1", order_id="o1", quantity="0.1", fee="-0.01", occurred_at_ms="1000"),
        item("fill", "f2", order_id="o1", quantity="0.3", fee="-0.02", occurred_at_ms="1001"),
        item(
            "order",
            "o2",
            side="sell",
            filled_quantity="0.4",
            quantity="0.4",
            occurred_at_ms="2000",
        ),
        item(
            "fill",
            "f3",
            order_id="o2",
            side="sell",
            quantity="0.2",
            fee="-0.04",
            realized_pnl="2",
            occurred_at_ms="2000",
        ),
        item(
            "fill",
            "f4",
            order_id="o2",
            side="sell",
            quantity="0.2",
            fee="-0.04",
            realized_pnl="4",
            occurred_at_ms="2001",
        ),
        item("order", "e-open", instrument="ETH-USDT", filled_quantity="1", occurred_at_ms="3000"),
        item(
            "fill",
            "e1",
            order_id="e-open",
            instrument="ETH-USDT",
            occurred_at_ms="3000",
        ),
        item(
            "order",
            "e-close",
            instrument="ETH-USDT",
            side="sell",
            filled_quantity="1",
            occurred_at_ms="4000",
        ),
        item(
            "fill",
            "e2",
            order_id="e-close",
            instrument="ETH-USDT",
            side="sell",
            fee="-0.01",
            realized_pnl="-3",
            occurred_at_ms="4000",
        ),
    ]
