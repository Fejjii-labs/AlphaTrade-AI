"""Round 2: round 1's primary variant V05 with the four pre-registered exits E0-E3.

Signals, filters, entry, stop, live checks and the 1-minute simulation are round 1's
(``engine.py``, copied unchanged). Only the exit changes:

- E0  round 1: TP1 15 % at 1R, TP2 50 % at the next level, TP3 20 % at the one after,
      15 % runner on the 5-candle trail, no break-even (control);
- E1  TP1 50 % at 1R, then stop to break-even, TP2 30 % at the next level, 20 % runner;
- E2  TP1 50 % at 1R, then break-even, the other 50 % on the 5-candle trail only;
- E3  TP1 33 % at 1R, then break-even, TP2 33 % at the next level, 34 % runner.
"Next level" is round 1's TP2 price (the next untaken level beyond TP1, or 2R when there
is none). Break-even = entry plus the round-trip taker fee (0.12 %), from the minute after
TP1 fills, as the live bot's ``breakeven_stop`` and ``bt3y_common.simulate``.

Which signals are traded does not depend on the exit: the live pre-trade checks are judged
on round 1's ladder (E0), so E0-E3 trade the same signals and differ only in how they exit.
(The bot's reward check counts a runner as zero, so E2's own ladder - 50 % at 1R - could
never pass it; ``own_checks=True`` reports that case.) One position per coin still applies,
so an exit that ends trades sooner can free the coin for a later signal.
"""

from __future__ import annotations

from bt3y_common import live_checks, simulate
from engine import DEFAULT, SLIP_BPS, _result, ready_index, signals_for
from entries import ENTRIES
from exits import TRAIL, ladder, target_levels
from stops import stop_price

V05 = {"exec": "1h", "levels": ["1d", "1w"], "equal": False, "life": "prev",
       "reclaim": "next", "entry": "c", "stop": "b", "exit": "default", "vol": False, "conf": 2}
EXITS = ("E0", "E1", "E2", "E3")


def exit_ladder(kind: str, lad: list) -> tuple[list, bool]:
    """(ladder, break-even) of exit ``kind`` from round 1's ladder ``lad``."""
    tp1, tp2 = lad[0][0], lad[1][0]
    if kind == "E0":
        return list(lad), False
    if kind == "E1":
        return [(tp1, 0.50), (tp2, 0.30)], True
    if kind == "E2":
        return [(tp1, 0.50)], True
    if kind == "E3":
        return [(tp1, 0.33), (tp2, 0.33)], True
    raise ValueError(kind)


def candidates(data, period, v: dict = V05, fill_at_level: bool = False) -> list[dict]:
    """Every signal of ``v`` in ``period`` that passes the filters and round 1's live checks,
    with its entry, stop and round-1 ladder (no one-position rule yet).
    ``fill_at_level``: a limit fill never improves on the limit price (step 0 check)."""
    v = {**DEFAULT, **v}
    tf = v["exec"]
    sigs, eqs = signals_for(data, v)
    b, atr = data.bars[tf], data.atr[tf]
    out = []
    for sig in sigs:
        t_sig = int(b["t"][sig.r])
        if not (period[0] - 86_400_000 <= t_sig < period[1]):
            continue
        if v["vol"] and sig.vol_ratio < 1.5 or sig.conf < v["conf"]:
            continue
        r0 = ready_index(data, tf, sig, v["stop"])
        e = ENTRIES[v["entry"]](data, tf, sig, r0) if r0 is not None else None
        if e is None or not (period[0] <= e["t"] < period[1]):
            continue
        s = sig.sign
        if fill_at_level and e["maker"]:
            e = {**e, "price": sig.level.price}
        stop = stop_price(v["stop"], s, e["ext"], float(atr[e["k"]]), data.tick)
        entry = e["price"]
        if stop is None or (entry - stop) * s <= 0:
            continue
        targets = target_levels(data, v["levels"], eqs, s, e["t"], entry)
        lad = ladder(v["exit"], s, entry, stop, sig, targets, data.tick)
        if live_checks(s, entry, stop, lad) is not None:
            continue
        out.append({"sym": data.symbol, "sign": s, "level": sig.level.tag,
                    "lvl": sig.level.price, "conf": sig.conf, "reclaim_ms": t_sig,
                    "entry_ms": e["t"], "entry": entry, "stop": stop, "maker": e["maker"],
                    "r1_ladder": lad, "stop_pct": round(abs(entry - stop) / entry * 100, 3)})
    return out


def sim(data, c: dict, kind: str, *, own_checks: bool = False, through: bool = False):
    """Simulate candidate ``c`` with exit ``kind``. ``through``: targets fill only once price
    trades one tick beyond them. Returns the trade row, or None if refused."""
    lad, be = exit_ladder(kind, c["r1_ladder"])
    if own_checks and live_checks(c["sign"], c["entry"], c["stop"], lad) is not None:
        return None
    if through:
        lad = [(p + c["sign"] * data.tick, f) for p, f in lad]
    res = simulate(data.M, data.bars["1h"], "1h", c["sign"], c["entry_ms"], c["entry"],
                   c["stop"], lad, be=be, trail=TRAIL, maker=c["maker"],
                   fill_minute_stop_only=c["maker"], funding=data.funding, slip_bps=SLIP_BPS)
    if res is None:
        return None
    return {**c, "exit": kind, "ladder": lad, **_result(res)}


def portfolio(rows: list, by_fill: bool = False) -> list:
    """One position per coin: a trade is skipped while an earlier one is still open.
    Signals are taken in reclaim order, as round 1 (``by_fill``: in entry-fill order)."""
    taken, busy_until = [], 0
    key = (lambda x: x["entry_ms"]) if by_fill else (lambda x: (x["reclaim_ms"], -x["sign"]))
    for t in sorted((x for x in rows if x), key=key):
        if t["entry_ms"] < busy_until:
            continue
        taken.append(t)
        busy_until = t["exit_ms"]
    return taken
