#!/usr/bin/env python3
"""
FX fixing reversal (Krohn, Mueller & Whelan) on EURUSD.
USD tends to rise into each fix and fall after it, so for EURUSD:
  pre_tokyo : SELL EURUSD 17:00 New York -> 00:55 UTC (Tokyo 09:55 fix)
  post_tokyo: BUY  EURUSD 00:55 UTC -> 02:00 New York
  pre_ecb   : SELL EURUSD 02:00 New York -> 08:15 New York (ECB 14:15 CET)
  post_ldn  : BUY  EURUSD 11:00 New York (London 4pm) -> 17:00 New York
Pure time-based entries/exits, no stop. Cost 0.9 pip per trade.
Runs on 2y H1 (times rounded to the hour) and 60d M5 (exact).
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from bt2 import fetch  # noqa: E402

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
COST = 0.9
PIP = 0.0001


def close_map(bars, step):
    return {b[0] + step: b[3] for b in bars}


def at(cm, dt, step):
    ts = int(dt.timestamp())
    ts -= ts % step
    return cm.get(ts)


def windows(day_ny):
    """Return dict name -> (direction, start_dt, end_dt) for the trading day starting at 17:00 NY of day_ny."""
    d = day_ny
    nxt = d + timedelta(days=1)
    t17 = datetime(d.year, d.month, d.day, 17, 0, tzinfo=NY)
    tok = datetime(nxt.year, nxt.month, nxt.day, 0, 55, tzinfo=UTC)
    t02 = datetime(nxt.year, nxt.month, nxt.day, 2, 0, tzinfo=NY)
    ecb = datetime(nxt.year, nxt.month, nxt.day, 8, 15, tzinfo=NY)
    ldn = datetime(nxt.year, nxt.month, nxt.day, 11, 0, tzinfo=NY)
    end = datetime(nxt.year, nxt.month, nxt.day, 17, 0, tzinfo=NY)
    return {"pre_tokyo": (-1, t17, tok), "post_tokyo": (1, tok, t02),
            "pre_ecb": (-1, t02, ecb), "post_ldn": (1, ldn, end)}


def run(bars, step):
    cm = close_map(bars, step)
    first = datetime.fromtimestamp(bars[0][0], NY).date()
    last = datetime.fromtimestamp(bars[-1][0], NY).date()
    out = {k: [] for k in ("pre_tokyo", "post_tokyo", "pre_ecb", "post_ldn")}
    d = first
    while d < last:
        if d.weekday() in (6, 0, 1, 2, 3):   # Sun-Thu evening start -> Mon-Fri sessions
            for name, (dr, s, e) in windows(d).items():
                p0, p1 = at(cm, s, step), at(cm, e, step)
                if p0 is None or p1 is None:
                    continue
                out[name].append((s.year, (p1 - p0) / PIP * dr - COST))
        d += timedelta(days=1)
    return out


def line(name, rs):
    p = [x for _, x in rs]
    if not p:
        return f"{name:<22} no trades"
    w = [x for x in p if x > 0]
    l = [x for x in p if x <= 0]
    pf = sum(w) / -sum(l) if l and sum(l) < 0 else 99
    eq = pk = dd = 0.0
    for x in p:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    yrs = {}
    for y, x in rs:
        yrs[y] = yrs.get(y, 0) + x
    ys = " ".join(f"{y}:{v:+.0f}" for y, v in sorted(yrs.items()))
    return (f"{name:<22} trades {len(p):>4} | win {100*len(w)/len(p):5.1f}% | PF {pf:4.2f} | "
            f"net {sum(p):+7.1f} pips | avg {sum(p)/len(p):+5.2f} | maxDD {dd:5.1f} | {ys}")


def main():
    for label, iv, rng, step in (("H1, ~2 years", "1h", "730d", 3600), ("M5, 60 days", "5m", "60d", 300)):
        bars = fetch("EURUSD=X", iv, rng)
        res = run(bars, step)
        print(f"\n=== EURUSD fixing reversal ({label}), pips after 0.9 cost ===")
        for k, v in res.items():
            print(line(k, v))
        combo = sorted(res["pre_ecb"] + res["post_ldn"])
        print(line("pre_ecb + post_ldn", res["pre_ecb"] + res["post_ldn"]))
        print(line("all four windows", sum(res.values(), [])))
        gross = {k: [(y, x + COST) for y, x in v] for k, v in res.items()}
        print("-- gross (no cost) --")
        for k, v in gross.items():
            print(line(k, v))


if __name__ == "__main__":
    main()
