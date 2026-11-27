"""Step 0 negative controls: plant a known look-ahead bug and check the truncation test
(``step0.py``) catches it. A test that finds nothing is only worth something if it would
have found a real bug.

- ``targets``: round 1's untaken-target check as it was before commit be25c52 (it read the
  1h candle still open at the entry);
- ``atr``: the stop uses the ATR of the candle after the reclaim candle (not closed yet when
  the limit order is placed).

Usage: python -I step0_controls.py <data_root> <out_dir> targets|atr
Prints how many BTC V05 trades the truncation test flags (0 would mean a blind test).
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import data as D  # noqa: E402
import numpy as np  # noqa: E402
import step0  # noqa: E402
from levels import NAKED_CANDLES  # noqa: E402


def targets_before_fix(data, tfs, eq_levels, sign, t_ms, entry):
    """exits.target_levels before be25c52 (no equal levels: V05 has none)."""
    h1 = data.bars["1h"]
    end = int(np.searchsorted(h1["t"], t_ms, side="left"))  # includes the open 1h candle
    out = []
    for tf in tfs:
        b, close = data.bars[tf], data.close[tf]
        k = int(np.searchsorted(close, t_ms, side="right"))
        for q in range(max(0, k - NAKED_CANDLES), k):
            a = int(np.searchsorted(h1["t"], close[q], side="left"))
            for p in (float(b["h"][q]), float(b["l"][q])):
                if (p - entry) * sign <= 0:
                    continue
                seg = h1["h"][a:end] if sign > 0 else h1["l"][a:end]
                if len(seg) and (seg.max() >= p if sign > 0 else seg.min() <= p):
                    continue
                out.append(p)
    return out


def shift_atr(cls):
    """Make ``atr[k]`` read the ATR of candle k + 1."""
    init = cls.__init__

    def patched(self, *args, **kwargs):
        init(self, *args, **kwargs)
        self.atr = {k: np.append(v[1:], np.nan) for k, v in self.atr.items()}

    cls.__init__ = patched


def main() -> None:
    root, out_dir, mode = sys.argv[1:4]
    os.makedirs(out_dir, exist_ok=True)
    if mode == "targets":
        step0.target_levels = targets_before_fix
    elif mode == "atr":
        shift_atr(D.Data)
        shift_atr(step0.CutData)
    else:
        sys.exit("mode: targets | atr")
    sym, n, bad = step0.check_symbol((root, out_dir, "BTCUSDT"))
    rows = json.load(open(os.path.join(out_dir, f"step0_{sym}.json")))
    print(f"{mode}: {bad} of {n} BTC trades flagged",
          dict(Counter(m for r in rows for m in r["mismatch"])))


if __name__ == "__main__":
    main()
