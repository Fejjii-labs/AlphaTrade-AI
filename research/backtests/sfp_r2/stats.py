"""Summary statistics of a list of trades (R after all costs, funding included)."""

from __future__ import annotations

import math
from collections import defaultdict

from data import month_key


def summary(trades: list[dict], period: tuple[int, int]) -> dict:
    months_total = sum(1 for _ in _months(period))
    r = [t["r"] for t in sorted(trades, key=lambda x: x["entry_ms"])]
    n = len(r)
    out = {"trades": n, "per_month": n / months_total if months_total else 0.0}
    if not n:
        return {**out, "win": 0.0, "mean": 0.0, "net": 0.0, "t": 0.0, "dd": 0.0, "streak": 0,
                "green": f"0/{months_total}"}
    mean = sum(r) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / (n - 1)) if n > 1 else 0.0
    eq = peak = dd = 0.0
    streak = worst = 0
    for x in r:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        streak = streak + 1 if x < 0 else 0
        worst = max(worst, streak)
    by_month = defaultdict(float)
    for t in trades:
        by_month[month_key(t["entry_ms"])] += t["r"]
    green = sum(1 for v in by_month.values() if v > 0)
    return {**out, "win": sum(1 for x in r if x > 0) / n, "mean": mean, "net": sum(r),
            "t": mean / (sd / math.sqrt(n)) if sd > 0 else 0.0, "dd": dd, "streak": worst,
            "green": f"{green}/{months_total}"}


def _months(period):
    a, b = month_key(period[0]), month_key(period[1] - 1)
    y, m = map(int, a.split("-"))
    while f"{y:04d}-{m:02d}" <= b:
        yield f"{y:04d}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def by_coin_share(trades: list[dict]) -> dict:
    net = defaultdict(float)
    for t in trades:
        net[t["sym"]] += t["r"]
    pos = sum(v for v in net.values() if v > 0)
    return {k: {"net": v, "share": (v / pos if pos > 0 and v > 0 else 0.0)} for k, v in net.items()}
