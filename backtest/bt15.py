#!/usr/bin/env python3
"""M1 study on EURUSD (Yahoo keeps ~30 days of 1-minute data, fetched in 7-day chunks).
 1) bt14 trend-pullback grid (ADX filter, EMA20 pullback, TP < SL) on M1.
 2) EMA crossover with ATR stop and RR target, 07:00-16:00 UTC, flat 20:00 UTC.
Cost 0.9 pip per trade. First half vs second half."""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from itertools import product

sys.path.insert(0, os.path.dirname(__file__))
import bt14  # noqa: E402
from bt6 import ema  # noqa: E402

PIP, COST = 0.0001, 0.9


def fetch_1m(sym="EURUSD=X"):
    now = int(time.time()); bars = {}
    for k in range(4):
        p2 = now - k * 7 * 86400; p1 = p2 - 7 * 86400
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&period1={p1}&period2={p2}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (backtest)"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)["chart"]["result"][0]
            q = d["indicators"]["quote"][0]
            for t, h, l, c in zip(d["timestamp"], q["high"], q["low"], q["close"]):
                if None not in (h, l, c): bars[int(t)] = (int(t), float(h), float(l), float(c))
        except Exception as e:
            print("chunk", k, "failed:", e)
        time.sleep(1)
    return [bars[t] for t in sorted(bars)][:-1]


def ema_cross(b, at, fast, slow, am, rr):
    c = [x[3] for x in b]; ef, es = ema(c, fast), ema(c, slow)
    out, pos, risks = [], None, []
    for i in range(slow + 5, len(b)):
        dt = datetime.fromtimestamp(b[i][0], timezone.utc); m = dt.hour * 60 + dt.minute
        if pos:
            d, e, sl, tp, risk = pos; x = None
            if (d == 1 and b[i][2] <= sl) or (d == -1 and b[i][1] >= sl): x = sl
            elif (d == 1 and b[i][1] >= tp) or (d == -1 and b[i][2] <= tp): x = tp
            elif m >= 1200: x = c[i]
            if x is not None: out.append((b[i][0], ((x - e) / PIP * d - COST) / risk)); pos = None
            continue
        if not (420 <= m < 960): continue
        up = ef[i] > es[i] and ef[i-1] <= es[i-1]; dn = ef[i] < es[i] and ef[i-1] >= es[i-1]
        if not (up or dn): continue
        d = 1 if up else -1; risk = am * at[i] / PIP
        if risk < 2: continue
        risks.append(risk)
        pos = (d, c[i], c[i] - d * risk * PIP, c[i] + d * risk * PIP * rr, risk)
    return out, (sum(risks) / len(risks) if risks else 0)


def main():
    b = fetch_1m()
    d0 = datetime.fromtimestamp(b[0][0], timezone.utc).date(); d1 = datetime.fromtimestamp(b[-1][0], timezone.utc).date()
    print(f"EURUSD M1 {d0} -> {d1}, {len(b)} bars")
    bt14.study("EURUSD M1 trend pullback", b, 40)
    at = bt14.atr(b); mid = b[len(b) // 2][0]
    print("\n=== EURUSD M1 EMA crossover (fast, slow, SLxATR, RR) ===")
    for fast, slow, am, rr in product(((34, 50), (50, 200), (100, 300)), (3.0, 6.0), (1.0, 3.0)):
        res, avg = ema_cross(b, at, fast[0], fast[1], am, rr) if False else ema_cross(b, at, fast[0], fast[1], am, rr), 0
        res, avg = res
        a = bt14.st([r for t, r in res if t < mid]); z = bt14.st([r for t, r in res if t >= mid])
        print(f"  ({fast[0]},{fast[1]},{am},{rr}) avg SL {avg:4.1f}p  1st {bt14.fmt(a)}  ||  2nd {bt14.fmt(z)}")


if __name__ == "__main__":
    main()
