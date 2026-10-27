"""Read-only backfill plan for BloFin demo trades in the journal (A6 §7, A7a).

Venue legs are grouped into position lifetimes ``(positionId, openTimeMs)``,
matched to stored mirror rows, and printed as a JSON Lines diff with a plan
digest. Nothing here writes the journal: the applied run is A7b and needs
Sofien's approval of this output. See ``docs/blofin_demo_trades_journal.md``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import JournalTrade, JournalTradeAttachment, JournalTradeObservation
from app.schemas.common import JournalTradeStatus
from app.services.blofin_demo_manual_mirror import (
    _FILLS,
    _FUNDING,
    _HISTORY,
    _INSTRUMENTS,
    _MIRROR_SOURCES,
    _ORDERS,
    _POSITIONS,
    _WINDOW,
    BloFinDemoManualMirror,
    MirrorReadError,
    _assign_funding,
    _attach_orders,
    _claim_manual,
    _decimal,
    _draft_from_slice,
    _history_pages,
    _instruments,
    _money,
    _parse_slices_detailed,
    _price_pnl,
    _Slice,
    _time,
)
from app.services.risk.mirror_economics import MIRROR_REF_PREFIX, exact_prefix

SUPERSEDED_PREFIX = "blofin-superseded-by:"
_DIGEST_VERSION = 2
_UNSAFE_KEYS = frozenset({"uid", "userid", "apikey", "passphrase", "secret", "email"})
_COMPARED = (
    "status",
    "size",
    "entry_price",
    "exit_price",
    "gross_pnl",
    "fees",
    "funding",
    "net_pnl",
    "exit_reason",
)


@dataclass(frozen=True, slots=True)
class VenuePages:
    """Raw GET pages for one plan. ``funding_begin`` is the oldest funding coverage."""

    instruments: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    history: list[dict[str, Any]]
    fills: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    funding: list[dict[str, Any]]
    window_begin: datetime
    window_end: datetime
    funding_begin: datetime

    def capture(self) -> dict[str, Any]:
        """Response bodies only, without identity fields, for a later replay."""
        pages = {
            name: [_scrub(row) for row in getattr(self, name)]
            for name in ("instruments", "positions", "history", "fills", "orders", "funding")
        }
        window = [self.window_begin, self.window_end, self.funding_begin]
        return {"window": [moment.isoformat() for moment in window], "pages": pages}

    @classmethod
    def replay(cls, captured: dict[str, Any]) -> VenuePages:
        begin, end, funding_begin = (datetime.fromisoformat(item) for item in captured["window"])
        return cls(
            **captured["pages"], window_begin=begin, window_end=end, funding_begin=funding_begin
        )


def _scrub(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key.lower() not in _UNSAFE_KEYS}


def read_venue(
    client: Any,
    *,
    since: datetime,
    now: datetime,
    organization_id: uuid.UUID,
    settings: Settings,
) -> VenuePages:
    """GET history in 7-day chunks from ``since``; funding only for the live window.

    The #304 org pin and UID check run first, inside this function, so no
    caller can read account data without it. A refusal raises before any
    account GET.
    """
    BloFinDemoManualMirror(client, now=now)._require_internal_read_pin(
        organization_id=organization_id, settings=settings
    )
    end = now.astimezone(UTC)
    begin = since.astimezone(UTC)
    instruments = client.request("GET", _INSTRUMENTS, params=None, signed=False)
    positions = client.request("GET", _POSITIONS, params=None, signed=True)
    if not isinstance(instruments, list) or not isinstance(positions, list):
        raise MirrorReadError("instrument or position payload was not a list")
    pages: dict[str, list[dict[str, Any]]] = {_HISTORY: [], _FILLS: [], _ORDERS: []}
    chunk_begin = begin
    while chunk_begin < end:
        chunk_end = min(chunk_begin + _WINDOW, end)
        for path, rows in pages.items():
            found, capped = _history_pages(client, path, _ms(chunk_begin), _ms(chunk_end))
            if capped:
                raise MirrorReadError("history page cap reached")
            rows.extend(found)
        chunk_begin = chunk_end + timedelta(milliseconds=1)
    funding_begin = max(begin, end - _WINDOW)
    funding, capped = _history_pages(client, _FUNDING, _ms(funding_begin), _ms(end))
    if capped:
        raise MirrorReadError("funding page cap reached")
    return VenuePages(
        instruments=[row for row in instruments if isinstance(row, dict)],
        positions=[row for row in positions if isinstance(row, dict)],
        history=pages[_HISTORY],
        fills=pages[_FILLS],
        orders=pages[_ORDERS],
        funding=funding,
        window_begin=begin,
        window_end=end,
        funding_begin=funding_begin,
    )


def _ms(moment: datetime) -> str:
    return str(int(moment.timestamp() * 1000))


def stored_rows(
    session: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[list[JournalTrade], set[uuid.UUID]]:
    """Mirror rows for one user, and the ids that carry user notes or evidence."""
    rows = list(
        session.scalars(
            select(JournalTrade)
            .where(
                JournalTrade.organization_id == organization_id,
                JournalTrade.user_id == user_id,
                or_(
                    exact_prefix(JournalTrade.external_ref, MIRROR_REF_PREFIX),
                    JournalTrade.source.in_(_MIRROR_SOURCES),
                ),
            )
            .order_by(JournalTrade.id)
        ).all()
    )
    ids = [row.id for row in rows]
    annotated = {row.id for row in rows if (row.notes or "").strip() or (row.thesis or "").strip()}
    for model in (JournalTradeObservation, JournalTradeAttachment):
        found = session.execute(
            select(model.journal_trade_id, func.count())
            .where(model.journal_trade_id.in_(ids))
            .group_by(model.journal_trade_id)
        )
        annotated.update(trade_id for trade_id, _count in found)
    return rows, annotated


@dataclass(slots=True)
class _Lifetime:
    position_id: str
    open_ms: int
    legs: list[_Slice] = field(default_factory=list)
    position: dict[str, Any] | None = None

    @property
    def key(self) -> str:
        return f"{self.position_id}:{self.open_ms}"


def _open_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(_money(value), "f")
    return str(getattr(value, "value", value))


def _weighted(legs: list[_Slice], attr: str) -> Decimal | None:
    total = sum((leg.contracts for leg in legs), Decimal("0"))
    if not legs or total <= 0:
        return None
    return _money(sum((getattr(leg, attr) * leg.contracts for leg in legs), Decimal("0")) / total)


def _total(values: list[Decimal | None]) -> Decimal | None:
    """Sum of every leg, or None when there are no legs or one is unknown."""
    if not values or any(value is None for value in values):
        return None
    return sum((value for value in values if value is not None), Decimal("0"))


def _proposed(
    life: _Lifetime, pages: VenuePages, meta: dict[str, Any]
) -> tuple[dict[str, Any], bool, list[str]]:
    """A6 §4 lifetime economics, whether it is verified, and why not."""
    legs = sorted(life.legs, key=lambda leg: (leg.closed_at, leg.history_id))
    drafts = [_draft_from_slice(leg) for leg in legs]
    reasons: set[str] = set()
    if any(leg.opened_at < pages.funding_begin for leg in legs):
        reasons.add("funding_before_coverage")
    if any(not draft.price_matches for draft in drafts):
        reasons.add("price_unverified")
    sizes = [leg.contracts * leg.contract_value for leg in legs if leg.contract_value is not None]
    size: Decimal | None = sum(sizes, Decimal("0")) if len(sizes) == len(legs) else None
    entry = _weighted(legs, "open_price")
    if life.position is not None:
        quantity = _decimal(life.position.get("positions"))
        instrument = meta.get(str(life.position.get("instId")))
        if size is not None and quantity is not None and instrument is not None:
            size += abs(quantity) * instrument.contract_value
        else:
            size = None
        if not legs:
            entry = _decimal(life.position.get("averagePrice"))
    closed = life.position is None
    full = bool(legs) and sum(leg.contracts for leg in legs) == max(
        leg.max_contracts for leg in legs
    )
    if closed and not full:
        reasons.add("partially_closed_incomplete")
    gross = _total([_price_pnl(leg) for leg in legs])
    if "price_unverified" in reasons:
        gross = None
    fees = _total([leg.fee for leg in legs])
    funding = _total([leg.funding if leg.funding_ok else None for leg in legs])
    verified = closed and full and all(draft.verified for draft in drafts) and not reasons
    net = None
    if verified and gross is not None and fees is not None and funding is not None:
        net = gross - fees - funding
    if closed:
        status = JournalTradeStatus.CLOSED
        reason = "closed" if full else "partially_closed_incomplete"
    else:
        status = JournalTradeStatus.OPEN
        reason = None
    fields = {
        "status": status,
        "size": size,
        "entry_price": entry,
        "exit_price": _weighted(legs, "close_price") if closed else None,
        "gross_pnl": gross if verified else None,
        "fees": fees if verified else None,
        "funding": funding if verified else None,
        "net_pnl": net,
        "exit_reason": reason,
    }
    return fields, verified, sorted(reasons)


def _history_tags(row: JournalTrade) -> set[str]:
    return {
        tag.split(":", 1)[1]
        for tag in row.tags or []
        if isinstance(tag, str) and tag.startswith("blofin-history:")
    }


def _tracker_key(row: JournalTrade) -> str | None:
    ref = row.external_ref or ""
    if not ref.startswith("blofin-demo-open:"):
        return None
    return ref.removeprefix("blofin-demo-open:")


def _kept_ref(row: JournalTrade) -> str | None:
    """A history ref never changes again once a row holds one (A6 §3.2)."""
    ref = row.external_ref or ""
    return ref if ref.startswith("blofin-demo-history:") else None


def _superseded(row: JournalTrade) -> bool:
    return any(isinstance(tag, str) and tag.startswith(SUPERSEDED_PREFIX) for tag in row.tags or [])


def _diff(row: JournalTrade | None, fields: dict[str, Any]) -> dict[str, list[str | None]]:
    changed = {}
    for name in _COMPARED:
        before = _text(getattr(row, name)) if row is not None else None
        after = _text(fields[name])
        if before != after:
            changed[name] = [before, after]
    return changed


def build_plan(
    pages: VenuePages,
    rows: Iterable[JournalTrade],
    *,
    annotated: set[uuid.UUID],
    user_id: uuid.UUID,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Plan lines and the summary object. Pure: reads its inputs, writes nothing."""
    meta, _conflict = _instruments(pages.instruments)
    slices, skipped_rows, _incomplete = _parse_slices_detailed(pages.history, meta, pages.fills)
    _assign_funding(slices, pages.funding, window_begin=pages.funding_begin)
    _attach_orders(slices, pages.fills, pages.orders)
    lifetimes: dict[tuple[str, int], _Lifetime] = {}
    for leg in slices:
        key = (leg.position_id, _open_ms(leg.opened_at))
        lifetimes.setdefault(key, _Lifetime(*key)).legs.append(leg)
    missing_open_time = 0
    for position in pages.positions:
        position_id = position.get("positionId")
        opened = _time(position.get("createTime"))
        quantity = _decimal(position.get("positions"))
        if not isinstance(position_id, str) or quantity is None or quantity == 0:
            continue
        if opened is None:
            missing_open_time += 1
            continue
        key = (position_id, _open_ms(opened))
        lifetimes.setdefault(key, _Lifetime(*key)).position = position

    live = [row for row in rows if not _superseded(row)]
    by_history: dict[str, list[JournalTrade]] = defaultdict(list)
    by_tracker: dict[str, list[JournalTrade]] = defaultdict(list)
    for row in live:
        for history_id in _history_tags(row):
            by_history[history_id].append(row)
        tracker = _tracker_key(row)
        if tracker is not None:
            by_tracker[tracker].append(row)
        for tag in row.tags or []:
            if isinstance(tag, str) and tag.startswith("blofin-position:"):
                by_tracker[tag.removeprefix("blofin-position:")].append(row)

    # A6 §3.4: does positions.createTime (legacy tracker stamp) equal the legs' createTime?
    tracker_matches = tracker_differs = 0
    leg_times: dict[str, set[int]] = defaultdict(set)
    for position_id, open_ms in lifetimes:
        if lifetimes[(position_id, open_ms)].legs:
            leg_times[position_id].add(open_ms)
    for row in live:
        position_id, _, stamp = (_tracker_key(row) or "").rpartition(":")
        if position_id in leg_times:
            if stamp.isdigit() and int(stamp) in leg_times[position_id]:
                tracker_matches += 1
            else:
                tracker_differs += 1

    lines: list[dict[str, Any]] = []
    seen_rows: set[uuid.UUID] = set()
    ghosts = {"G1": 0, "G2": 0, "G3": 0, "G4": 0}
    counts = {"creates": 0, "updates": 0, "merges": 0, "supersedes": 0, "skips": 0}
    legs_per = {"1": 0, "2": 0, "3+": 0}
    per_position: dict[str, int] = defaultdict(int)
    open_time_mismatch = history_conflicts = 0
    for life in sorted(lifetimes.values(), key=lambda item: (item.open_ms, item.position_id)):
        per_position[life.position_id] += 1
        if life.legs:
            legs_per[str(len(life.legs)) if len(life.legs) < 3 else "3+"] += 1
        fields, verified, reasons = _proposed(life, pages, meta)
        legs = sorted(leg.history_id for leg in life.legs)
        base = {"lifetime": life.key, "legs": legs, "verified": verified, "reasons": reasons}
        holders = {row.id: row for leg in legs for row in by_history.get(leg, [])}
        trackers = {row.id: row for row in by_tracker.get(life.key, [])}
        mismatch = life.position is not None and any(
            other.position_id == life.position_id
            and other.open_ms != life.open_ms
            and other.legs
            and max(leg.closed_at for leg in other.legs)
            > datetime.fromtimestamp(life.open_ms / 1000, UTC)
            for other in lifetimes.values()
        )
        double = [leg for leg in legs if len(by_history.get(leg, [])) > 1]
        candidates = {**holders, **trackers}
        seen_rows.update(candidates)
        if mismatch or double:
            open_time_mismatch += int(mismatch)
            history_conflicts += len(double)
            why = ["open_time_mismatch"] if mismatch else ["history_on_two_rows"]
            lines.append({"action": "skip", **base, "reasons": why, "journal_trade_id": None})
            counts["skips"] += 1
            continue
        if not candidates and life.legs and life.position is None:
            leg_draft = _draft_from_slice(sorted(life.legs, key=lambda leg: leg.closed_at)[0])
            claimed = [row for row in live if _claim_manual(row, leg_draft, user_id=user_id)]
            if len(claimed) == 1:
                candidates = {claimed[0].id: claimed[0]}
                seen_rows.add(claimed[0].id)
        first_ref = f"blofin-demo-history:{legs[0]}" if legs and life.position is None else None
        open_ref = f"blofin-demo-open:{life.key}"
        if not candidates:
            ref_after = first_ref or open_ref
            lines.append(
                {
                    "action": "create",
                    **base,
                    "journal_trade_id": None,
                    "ref_before": None,
                    "ref_after": ref_after,
                    "fields": _diff(None, fields),
                    "ghost_class": None,
                }
            )
            counts["creates"] += 1
            continue
        ordered = sorted(candidates.values(), key=lambda row: str(row.id))
        if len(ordered) == 1:
            row = ordered[0]
            ref_after = _kept_ref(row) or first_ref or open_ref
            changed = _diff(row, fields)
            action = (
                "skip"
                if not changed and ref_after == row.external_ref
                else (
                    "close"
                    if life.position is None and row.status is not JournalTradeStatus.CLOSED
                    else "attach"
                )
            )
            counts["skips" if action == "skip" else "updates"] += 1
            lines.append(
                {
                    "action": action,
                    **base,
                    "journal_trade_id": str(row.id),
                    "ref_before": row.external_ref,
                    "ref_after": ref_after,
                    "fields": changed,
                    "ghost_class": None,
                }
            )
            continue
        cancelled = [row for row in ordered if row.id in trackers]
        cancelled = [row for row in cancelled if row.status is JournalTradeStatus.CANCELLED]
        ghost = "G1" if cancelled else "G2"
        ghosts[ghost] += 1
        notes = [row for row in ordered if row.id in annotated]
        if len(notes) > 1:
            lines.append(
                {
                    "action": "list_ghost",
                    **base,
                    "journal_trade_id": None,
                    "reasons": ["annotated_conflict"],
                    "ghost_class": ghost,
                    "rows": [str(row.id) for row in notes],
                }
            )
            continue
        if notes:
            survivor = notes[0]
        else:
            closing = [row for row in ordered if row.id in holders]
            survivor = min(
                closing or ordered,
                key=lambda row: (row.exit_time or datetime.max.replace(tzinfo=UTC), str(row.id)),
            )
        ref_after = _kept_ref(survivor) or first_ref or open_ref
        lines.append(
            {
                "action": "merge",
                **base,
                "journal_trade_id": str(survivor.id),
                "ref_before": survivor.external_ref,
                "ref_after": ref_after,
                "fields": _diff(survivor, fields),
                "ghost_class": ghost,
            }
        )
        counts["merges"] += 1
        for row in ordered:
            if row.id == survivor.id:
                continue
            lines.append(
                {
                    "action": "supersede",
                    "lifetime": life.key,
                    "legs": [],
                    "journal_trade_id": str(row.id),
                    "ref_before": row.external_ref,
                    "ref_after": row.external_ref,
                    "verified": False,
                    "reasons": [],
                    "fields": {
                        "status": [_text(row.status), "cancelled"],
                        "exit_reason": [row.exit_reason, "superseded"],
                    },
                    "ghost_class": ghost,
                }
            )
            counts["supersedes"] += 1

    for row in sorted(live, key=lambda item: str(item.id)):
        tracker = _tracker_key(row)
        if row.id in seen_rows or tracker is None:
            continue
        if tracker.endswith(":na"):
            ghost = "G4"
        elif row.status is JournalTradeStatus.CANCELLED and row.exit_reason == "flat_on_venue":
            ghost = "G3"
        else:
            continue
        ghosts[ghost] += 1
        lines.append(
            {
                "action": "list_ghost",
                "lifetime": tracker,
                "legs": [],
                "journal_trade_id": str(row.id),
                "ref_before": row.external_ref,
                "ref_after": row.external_ref,
                "verified": False,
                "reasons": [],
                "ghost_class": ghost,
            }
        )
    leg_directions = [
        {
            "history_id": leg.history_id,
            "inst_id": leg.inst_id,
            "direction": leg.direction.value,
            "source": leg.side_source,
        }
        for leg in sorted(slices, key=lambda item: item.history_id)
    ]
    # Digest v2: the lines plus every leg's direction and its source, so a
    # long/short flip on an unverified create cannot keep an approved digest.
    digest_parts = [
        json.dumps({"digest_version": _DIGEST_VERSION}, separators=(",", ":")),
        *(json.dumps(line, sort_keys=True, separators=(",", ":")) for line in lines),
        json.dumps({"leg_directions": leg_directions}, sort_keys=True, separators=(",", ":")),
    ]
    digest = hashlib.sha256("\n".join(digest_parts).encode()).hexdigest()
    summary = {
        "window": [pages.window_begin.isoformat(), pages.window_end.isoformat()],
        "funding_coverage_begin": pages.funding_begin.isoformat(),
        "legs_read": len(slices),
        "history_rows_skipped": len(skipped_rows),
        "skipped_rows": [row.as_dict() for row in skipped_rows],
        "leg_directions": leg_directions,
        "lifetimes": len(lifetimes),
        **counts,
        "ghosts": ghosts,
        "position_id_reused": sum(1 for count in per_position.values() if count > 1),
        "open_time_mismatch": open_time_mismatch,
        "history_on_two_rows": history_conflicts,
        "open_positions_missing_create_time": missing_open_time,
        "tracker_open_time_matches_history": tracker_matches,
        "tracker_open_time_differs_from_history": tracker_differs,
        "fee_mismatch": None,
        "legs_per_lifetime": legs_per,
        "plan_digest_version": _DIGEST_VERSION,
        "plan_digest": f"sha256:{digest}",
    }
    return lines, summary
