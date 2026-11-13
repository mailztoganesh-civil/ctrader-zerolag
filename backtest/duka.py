#!/usr/bin/env python3
"""Parallel Dukascopy downloader: 1-minute BID candles -> M5 CSV (gzip).
Usage env: DUKA_SYM=EURUSD DUKA_DAYS=730 DUKA_OUT=backtest/data/eurusd_m5_2y.csv.gz"""
import gzip
import lzma
import os
import struct
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

SYM = os.environ.get("DUKA_SYM", "EURUSD")
DAYS = int(os.environ.get("DUKA_DAYS", "730"))
OUT = os.environ.get("DUKA_OUT", f"backtest/data/{SYM.lower()}_m5.csv.gz")
SCALE = 1e3 if "JPY" in SYM or SYM.startswith("XAU") else 1e5


def day(d):
    url = f"https://datafeed.dukascopy.com/datafeed/{SYM}/{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5"
    for k in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
            if not raw:
                return d, []
            buf = lzma.decompress(raw, format=lzma.FORMAT_ALONE)
            base = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
            out = []
            for i in range(0, len(buf) - 23, 24):
                t, o, c, lo, hi, v = struct.unpack(">iiiiif", buf[i:i + 24])
                if v > 0:
                    out.append((base + t, o / SCALE, hi / SCALE, lo / SCALE, c / SCALE))
            return d, out
        except Exception as e:
            err = e
            time.sleep(1.5 * (k + 1))
    print("failed", d, err)
    return d, None


def main():
    t0 = time.time()
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    dates = [end - timedelta(days=k) for k in range(DAYS) if (end - timedelta(days=k)).weekday() != 5]
    m5, ok, fail = {}, 0, 0
    with ThreadPoolExecutor(max_workers=24) as ex:
        for k, (d, rows) in enumerate(ex.map(day, dates)):
            if rows is None:
                fail += 1; continue
            ok += 1
            for t, o, h, l, c in rows:
                key = t - t % 300
                b = m5.get(key)
                if b is None: m5[key] = [o, h, l, c]
                else: b[1] = max(b[1], h); b[2] = min(b[2], l); b[3] = c
            if k % 100 == 0:
                print(f"{k}/{len(dates)} days, {time.time()-t0:.0f}s", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with gzip.open(OUT, "wt") as f:
        for k in sorted(m5):
            o, h, l, c = m5[k]
            f.write(f"{k},{o:.5f},{h:.5f},{l:.5f},{c:.5f}\n")
    ks = sorted(m5)
    print(f"done: {ok} days ok, {fail} failed, {len(m5)} M5 bars, "
          f"{datetime.fromtimestamp(ks[0], timezone.utc).date()} -> {datetime.fromtimestamp(ks[-1], timezone.utc).date()}, {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
