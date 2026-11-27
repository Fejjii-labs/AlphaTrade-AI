"""Why a losing trade lost. One reason per loss, checked in this order:

1. ``fees``      fees too large vs the stop: before fees, slippage and funding the trade
                 made zero or more;
2. ``gave_back`` target too far: it reached TP1 (+1R) and turned back to the stop before
                 TP2 (only possible with a ladder);
3. after the stop, the next 24 hours of 1-minute prices decide:
   ``hunted``   stop hunted then reversed: price came back to the trade's +1R price before
                going 1R beyond the stop;
   ``broke``    level broke for real: price went 1R beyond the stop first;
   ``chop``     neither within 24 hours (chop, no follow-through);
4. ``other``     anything else (e.g. still open at the end of the data).
"Target too close / R:R refused" cannot describe a trade that was taken: the live checks
refuse those before entry. Refused signals are counted and replayed separately.
"""

from __future__ import annotations

REASONS = {
    "broke": "Level broke for real",
    "hunted": "Stop hunted, then reversed (wick retest)",
    "gave_back": "Target too far: reached +1R, then back to the stop",
    "refused": "Target too close / R:R refused (refused before entry, not traded)",
    "fees": "Fees too large vs the stop",
    "chop": "Chop, no follow-through",
    "other": "Other",
}
WINDOW_MIN = 24 * 60


def pre_cost_r(t: dict) -> float:
    risk = abs(t["entry"] - t["stop"])
    return sum(f * (p - t["entry"]) * t["sign"] for _, f, p, _ in t["fills"]) / risk


def classify(M, t: dict) -> str:
    s, entry, stop = t["sign"], t["entry"], t["stop"]
    risk = abs(entry - stop)
    if pre_cost_r(t) >= 0:
        return "fees"
    if t["hits"] >= 1:
        return "gave_back"
    last = t["fills"][-1]
    if not last[3]:
        return "other"
    a = M.index(last[0])
    z = min(M.n, a + WINDOW_MIN)
    up, down = entry + s * risk, stop - s * risk
    for j in range(a, z):
        hi, lo = M.h[j], M.l[j]
        if hi != hi:
            continue
        fav = hi if s > 0 else lo
        adv = lo if s > 0 else hi
        if (adv - down) * s <= 0:
            return "broke"
        if (fav - up) * s >= 0:
            return "hunted"
    return "chop"
