"""A7a: read-only BloFin demo journal backfill plan (A6 §3-§7, K1)."""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models import JournalTrade, Organization, User
from app.schemas.common import JournalTradeSource, JournalTradeStatus, TradeDirection
from app.services.blofin_demo_journal_backfill import (
    VenuePages,
    build_plan,
    read_venue,
    stored_rows,
)
from tests.test_blofin_demo_manual_mirror import _FIXTURE, NOW, ORG, USER, _apply, _engine

SINCE = datetime(2026, 10, 1, tzinfo=UTC)
FUNDING_BEGIN = NOW - timedelta(days=7)


@pytest.fixture
def session() -> Iterator[Session]:
    with _engine()() as current:
        current.add(Organization(id=ORG, name="Backfill Org"))
        current.add(User(id=USER, email="backfill@test.example", hashed_password="not-a-hash"))
        current.commit()
        yield current


def _pages(bundle: dict[str, Any], *, funding_begin: datetime = FUNDING_BEGIN) -> VenuePages:
    return VenuePages(
        instruments=_FIXTURE["instruments"],
        positions=bundle["positions"],
        history=bundle["history"],
        fills=bundle["fills"],
        orders=bundle["orders"],
        funding=bundle["funding"],
        window_begin=SINCE,
        window_end=NOW,
        funding_begin=funding_begin,
    )


def _plan(bundle: dict[str, Any], rows: list[JournalTrade] = (), **kwargs: Any) -> tuple:  # type: ignore[assignment]
    return build_plan(_pages(bundle, **kwargs), list(rows), annotated=set(), user_id=USER)


def _row(**values: Any) -> JournalTrade:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "organization_id": ORG,
        "user_id": USER,
        "source": JournalTradeSource.BLOFIN_DEMO_MANUAL,
        "status": JournalTradeStatus.CLOSED,
        "symbol": "BTCUSDT",
        "timeframe": "manual",
        "direction": TradeDirection.LONG,
        "tags": [],
    }
    base.update(values)
    return JournalTrade(**base)


def test_partial_closes_plan_one_trade_with_summed_economics() -> None:
    lines, summary = _plan(_FIXTURE["partial_flat"])
    assert [line["action"] for line in lines] == ["create"]
    line = lines[0]
    assert line["lifetime"] == "p-part:1792195200000"
    assert line["legs"] == ["h-part-1", "h-part-2"]
    assert line["ref_after"] == "blofin-demo-history:h-part-1"
    assert line["verified"] is True
    after = {name: change[1] for name, change in line["fields"].items()}
    assert after == {
        "status": "closed",
        "size": "0.02000000",
        "entry_price": "100.00000000",
        "exit_price": "107.50000000",
        "gross_pnl": "0.15000000",
        "fees": "0.00300000",
        "funding": "0.00200000",
        "net_pnl": "0.14500000",
        "exit_reason": "closed",
    }
    assert summary["legs_per_lifetime"] == {"1": 0, "2": 1, "3+": 0}
    assert summary["plan_digest"].startswith("sha256:")


def test_rerun_against_the_planned_row_is_a_no_op() -> None:
    lines, _ = _plan(_FIXTURE["partial_flat"])
    after = {name: change[1] for name, change in lines[0]["fields"].items()}
    stored = _row(
        external_ref="blofin-demo-history:h-part-1",
        tags=["blofin-history:h-part-1", "blofin-history:h-part-2"],
        exit_reason=after.pop("exit_reason"),
        **{name: Decimal(value) for name, value in after.items() if name != "status"},
    )
    again, summary = _plan(_FIXTURE["partial_flat"], [stored])
    assert [line["action"] for line in again] == ["skip"]
    assert again[0]["fields"] == {}
    assert (summary["creates"], summary["updates"]) == (0, 0)


def test_legacy_mirror_rows_plan_a_merge_without_any_write(session: Session) -> None:
    _apply(session, _FIXTURE["partial_open"])
    _apply(session, _FIXTURE["partial_flat"])
    statements: list[str] = []
    engine = session.get_bind()

    def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        statements.append(statement.split()[0].upper())

    event.listen(engine, "before_cursor_execute", record)
    try:
        rows, annotated = stored_rows(session, organization_id=ORG, user_id=USER)
        lines, summary = build_plan(
            _pages(_FIXTURE["partial_flat"]), rows, annotated=annotated, user_id=USER
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert set(statements) == {"SELECT"}
    by_ref = {row.external_ref: row for row in rows}
    merge = next(line for line in lines if line["action"] == "merge")
    assert merge["ghost_class"] == "G1"
    assert merge["journal_trade_id"] == str(by_ref["blofin-demo-history:h-part-1"].id)
    assert merge["ref_after"] == "blofin-demo-history:h-part-1"
    superseded = {line["ref_before"] for line in lines if line["action"] == "supersede"}
    assert superseded == {"blofin-demo-open:p-part:1792195200000", "blofin-demo-history:h-part-2"}
    assert summary["ghosts"]["G1"] == 1 and summary["supersedes"] == 2
    assert summary["tracker_open_time_matches_history"] == 1
    assert summary["tracker_open_time_differs_from_history"] == 0


def test_annotated_survivor_wins_and_two_annotated_rows_are_only_listed(session: Session) -> None:
    _apply(session, _FIXTURE["partial_open"])
    _apply(session, _FIXTURE["partial_flat"])
    rows, _ = stored_rows(session, organization_id=ORG, user_id=USER)
    by_ref = {row.external_ref: row for row in rows}
    later = by_ref["blofin-demo-history:h-part-2"]
    later.notes = "Took profit into resistance."
    session.flush()
    rows, annotated = stored_rows(session, organization_id=ORG, user_id=USER)
    lines, _ = build_plan(_pages(_FIXTURE["partial_flat"]), rows, annotated=annotated, user_id=USER)
    merge = next(line for line in lines if line["action"] == "merge")
    assert merge["journal_trade_id"] == str(later.id)

    by_ref["blofin-demo-history:h-part-1"].notes = "Scaled out early."
    session.flush()
    rows, annotated = stored_rows(session, organization_id=ORG, user_id=USER)
    lines, _ = build_plan(_pages(_FIXTURE["partial_flat"]), rows, annotated=annotated, user_id=USER)
    assert [line["action"] for line in lines] == ["list_ghost"]
    assert lines[0]["reasons"] == ["annotated_conflict"]
    session.rollback()


def test_reused_position_id_is_two_lifetimes() -> None:
    bundle = copy.deepcopy(_FIXTURE["long"])
    reused = copy.deepcopy(bundle["history"][0])
    reused.update(historyId="h-long-2", createTime="1792242000000", updateTime="1792245600000")
    bundle["history"].append(reused)
    lines, summary = _plan(bundle)
    assert [line["lifetime"] for line in lines] == ["p-long:1792195200000", "p-long:1792242000000"]
    assert summary["creates"] == 2 and summary["position_id_reused"] == 1


def test_open_position_without_create_time_is_skipped_not_keyed_na() -> None:
    bundle = copy.deepcopy(_FIXTURE["partial_open"])
    bundle["history"] = []
    del bundle["positions"][0]["createTime"]
    lines, summary = _plan(bundle)
    assert lines == []
    assert summary["open_positions_missing_create_time"] == 1
    assert ":na" not in json.dumps(lines)


def test_history_id_on_two_live_rows_is_withheld() -> None:
    rows = [
        _row(external_ref="blofin-demo-history:h-long", tags=["blofin-history:h-long"]),
        _row(external_ref=None, tags=["blofin-history:h-long"]),
    ]
    lines, summary = _plan(_FIXTURE["long"], rows)
    assert [(line["action"], line["reasons"]) for line in lines] == [
        ("skip", ["history_on_two_rows"])
    ]
    assert summary["history_on_two_rows"] == 1


def test_leg_older_than_funding_coverage_keeps_exit_and_withholds_net() -> None:
    lines, _ = _plan(_FIXTURE["long"], funding_begin=datetime(2026, 10, 17, 6, tzinfo=UTC))
    line = lines[0]
    assert line["verified"] is False
    assert "funding_before_coverage" in line["reasons"]
    assert line["fields"]["exit_price"] == [None, "110.00000000"]
    assert "net_pnl" not in line["fields"] and "funding" not in line["fields"]


def test_unmatched_cancelled_tracker_and_na_ref_are_listed_only() -> None:
    rows = [
        _row(
            external_ref="blofin-demo-open:p-gone:1790000000000",
            status=JournalTradeStatus.CANCELLED,
            exit_reason="flat_on_venue",
        ),
        _row(external_ref="blofin-demo-open:p-old:na", status=JournalTradeStatus.OPEN),
    ]
    lines, summary = _plan(_FIXTURE["long"], rows)
    ghosts = sorted((line["action"], line["ghost_class"]) for line in lines if line["ghost_class"])
    assert ghosts == [("list_ghost", "G3"), ("list_ghost", "G4")]
    assert summary["ghosts"] == {"G1": 0, "G2": 0, "G3": 1, "G4": 1}


class _ChunkClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []

    def request(self, method: str, path: str, *, params: Any = None, **_: Any) -> Any:
        self.calls.append((method, path, params))
        if path == "/api/v1/market/instruments":
            return _FIXTURE["instruments"]
        if path == "/api/v1/account/positions":
            return [{"positionId": "p", "instId": "BTC-USDT", "positions": "1", "uid": "123"}]
        return []


def test_read_venue_walks_seven_day_chunks_with_get_only() -> None:
    client = _ChunkClient()
    pages = read_venue(client, since=NOW - timedelta(days=15), now=NOW)
    assert {method for method, _path, _params in client.calls} == {"GET"}
    paths = [path for _method, path, _params in client.calls]
    assert paths.count("/api/v1/account/positions-history") == 3
    assert paths.count("/api/v1/account/funding-fees") == 1
    assert pages.funding_begin > NOW - timedelta(days=7)
    captured = pages.capture()
    assert "uid" not in captured["pages"]["positions"][0]
    replayed = VenuePages.replay(json.loads(json.dumps(captured)))
    assert replayed.window_begin == pages.window_begin
    assert replayed.positions == captured["pages"]["positions"]


def _without_side(bundle: dict[str, Any], position_side: str) -> dict[str, Any]:
    """Live positions-history rows carry positionSide but no side (staging, 2026-10-27)."""
    bundle = copy.deepcopy(bundle)
    for row in bundle["history"]:
        row.pop("side", None)
        row["positionSide"] = position_side
    return bundle


def test_live_history_row_without_side_still_plans_from_position_side() -> None:
    expected, _ = _plan(_FIXTURE["partial_flat"])
    lines, summary = _plan(_without_side(_FIXTURE["partial_flat"], "long"))
    assert summary["legs_read"] == 2
    assert [line["fields"] for line in lines] == [line["fields"] for line in expected]


def test_net_mode_row_without_side_takes_the_opening_fill_side() -> None:
    bundle = _without_side(_FIXTURE["partial_flat"], "net")
    first = bundle["history"][0]
    opening = {
        "instId": first["instId"],
        "side": "buy",
        "ts": first["createTime"],
        "orderId": "o-open",
        "tradeId": "t-open",
    }
    closing = {**opening, "side": "sell", "ts": first["updateTime"], "tradeId": "t-close"}
    bundle["fills"] = [closing, opening, *bundle["fills"]]
    expected, _ = _plan(_FIXTURE["partial_flat"])
    lines, summary = _plan(bundle)
    assert summary["legs_read"] == 2
    assert [line["fields"]["gross_pnl"] for line in lines] == [
        line["fields"]["gross_pnl"] for line in expected
    ]


def test_net_mode_row_without_side_or_fills_is_still_skipped() -> None:
    bundle = _without_side(_FIXTURE["partial_flat"], "net")
    bundle["fills"] = []
    lines, summary = _plan(bundle)
    assert summary["legs_read"] == 0
    assert lines == []
