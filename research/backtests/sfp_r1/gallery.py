"""PNG chart of one trade: candles, level, sweep wick, entry, stop, targets, exits."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from data import TF_MIN, tunis  # noqa: E402

MIN = 60_000


def chart(data, t: dict, path: str, title: str, reason: str | None = None) -> None:
    span = t["exit_ms"] - t["sweep_ms"]
    tf = "15m" if span <= 2 * 86_400_000 else "1h" if span <= 8 * 86_400_000 else "4h"
    b = data.bars[tf]
    step = TF_MIN[tf] * MIN
    a = int(np.searchsorted(b["t"], t["sweep_ms"] - 40 * step))
    after = max(16 * step, 86_400_000 if t["r"] < 0 else 0)  # losses: the 24 h classified
    z = int(np.searchsorted(b["t"], t["exit_ms"] + after))
    tt, o, h, lo, c = (b[k][a:z] for k in ("t", "o", "h", "l", "c"))
    x = (tt - tt[0]) / step
    fig, ax = plt.subplots(figsize=(11, 5.6), dpi=110)
    up = c >= o
    ax.vlines(x, lo, h, color=np.where(up, "#2a9d8f", "#c0504d"), linewidth=0.8)
    ax.bar(x, np.abs(c - o), bottom=np.minimum(o, c), width=0.7,
           color=np.where(up, "#2a9d8f", "#c0504d"), edgecolor="none")

    def xpos(ms):
        return (ms - tt[0]) / step

    s = t["sign"]
    ax.axhline(t["lvl"], color="#6a4c93", lw=1.4, label=f"level: {t['level']} {t['lvl']:,.1f}")
    ax.plot([xpos(t["sweep_ms"])], [t["ext"]], marker="v" if s < 0 else "^", ms=11,
            color="#6a4c93", ls="none", label=f"sweep wick {t['ext']:,.1f}")
    ax.axhline(t["stop"], color="#c0504d", lw=1.1, ls="--", label=f"stop {t['stop']:,.1f}")
    for k, (p, f) in enumerate(t["ladder"], 1):
        ax.axhline(p, color="#2a9d8f", lw=0.9, ls=":",
                   label=f"TP{k} {int(round(f * 100))}% {p:,.1f}")
    ax.plot([xpos(t["entry_ms"])], [t["entry"]], marker=">", ms=11, color="#1d3557", ls="none",
            label=f"entry {t['entry']:,.1f} ({tunis(t['entry_ms'], '%d %b %H:%M')})")
    for ms_, f, p, st in t["fills"]:
        ax.plot([xpos(ms_)], [p], marker="x", ms=10, mew=2.2,
                color="#c0504d" if st else "#2a9d8f", ls="none")
    ax.plot([], [], marker="x", color="#555", ls="none", label="exits (red = stop/trail)")
    ticks = np.linspace(0, len(x) - 1, 7).astype(int)
    ax.set_xticks(x[ticks], [tunis(int(tt[k]), "%d %b %H:%M") for k in ticks], fontsize=8)
    ax.set_xlim(-1, len(x))
    ax.grid(alpha=0.25)
    ax.legend(loc="best", fontsize=7.5, framealpha=0.85)
    ax.set_title(title, fontsize=10.5)
    sub = (f"{tf} candles, Tunis time | {'long' if s > 0 else 'short'} | "
           f"stop {t['stop_pct']:.2f}% | result {t['r']:+.2f}R after costs")
    if reason:
        sub += f" | {reason}"
    fig.text(0.5, 0.01, sub, ha="center", fontsize=8.5)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path)
    plt.close(fig)
