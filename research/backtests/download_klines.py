"""Download Binance USD-M perpetual klines from data.binance.vision into one folder.

Monthly archives for whole months, daily archives for the current month.
Files land as <SYMBOL>-<interval>-<YYYY-MM>[-DD].csv, the layout the studies read.

Usage:
    python download_klines.py <out_dir> <SYMBOLS,...> <intervals,...> <first YYYY-MM> <last YYYY-MM-DD>
Example (one year, the five demo coins):
    python download_klines.py data BTCUSDT,ETHUSDT,ZECUSDT,TAOUSDT,HYPEUSDT 1m,15m,30m,1h,2h,4h 2025-11 2026-11-24
Downloads are untrusted data: keep the folder separate from code.
"""

import io
import os
import sys
import urllib.request
import zipfile
from datetime import date, timedelta

BASE = "https://data.binance.vision/data/futures/um"


def fetch(url: str, out_dir: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read()
    except Exception:
        return False
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            if name.endswith(".csv") and "/" not in name:
                with open(os.path.join(out_dir, name), "wb") as handle:
                    handle.write(archive.read(name))
    return True


def main() -> None:
    out_dir, symbols, intervals, first, last = sys.argv[1:6]
    os.makedirs(out_dir, exist_ok=True)
    year, month = map(int, first.split("-"))
    end = date.fromisoformat(last)
    months = []
    while (year, month) < (end.year, end.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    days = [end.replace(day=1) + timedelta(days=i) for i in range(end.day)]
    for symbol in symbols.split(","):
        for interval in intervals.split(","):
            for m in months:
                name = f"{symbol}-{interval}-{m}"
                if not os.path.exists(os.path.join(out_dir, name + ".csv")):
                    if not fetch(f"{BASE}/monthly/klines/{symbol}/{interval}/{name}.zip", out_dir):
                        print("missing", name, flush=True)
            for d in days:
                name = f"{symbol}-{interval}-{d.isoformat()}"
                if not os.path.exists(os.path.join(out_dir, name + ".csv")):
                    if not fetch(f"{BASE}/daily/klines/{symbol}/{interval}/{name}.zip", out_dir):
                        print("missing", name, flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
