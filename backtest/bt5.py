#!/usr/bin/env python3
"""
Session range breakout study on M5 (last ~60 days).
 ASIA : range 00:00-07:00 UTC, entries 07:00-12:00 UTC, flat by 20:00 UTC
 TBR  : range 08:10-09:10 New York, entries 09:10-12:00 NY, flat by 16:00 NY
Entry: first M5 close beyond range high/low (one trade per day).
SL: opposite side of range ("far") or range middle ("mid"). TP: 1R / 1.5R / 2R.
Cost 0.9 pip per trade. If SL and TP touch in the same candle -> SL.
"""
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from bt2 import fetch  # noqa: E402

COST = 0.9
NY = ZoneInfo("America/New_York")


def minutes(ts, tz):
    d = datetime.fromtimestamp(ts, tz)
    return d.date(), d.hour * 60 + d.minute


def sessions(bars, kind):
    """Yield (day, range_hi, range_lo, list_of_trade_bar_indices, flat_minute) per day."""
    tz = timezone.utc if kind == "ASIA" else NY
    if kind == "ASIA":
        r0, r1, e1, flat = 0, 7 * 60, 12 * 60, 20 * 60
    else:
        r0, r1, e1, flat = 8 * 60 + 10, 9 * 60 + 10, 12 * 60, 16 * 60
    days = {}
    for i, b in enumerate(bars):
        day, m = minutes(b[0], tz)
        days.setdefault(day, []).append((i, m))
    for day, items in sorted(days.items()):
        rng = [i for i, m in items if r0 <= m < r1]
        after = [(i, m) for i, m in items if m >= r1 and m < flat]
        if len(rng) < (r1 - r0) // 5 * 0.8 or not after:
            continue
        hi = max(bars[i][1] for i in rng)
        lo = min(bars[i][2] for i in rng)
        yield day, hi, lo, after, e1


def run(bars, pip, kind, sl_mode, rr, min_rng=0, max_rng=1e9):
    out = []
    for day, rh, rl, after, e1 in sessions(bars, kind):
        width = (rh - rl) / pip
        if width < min_rng or width > max_rng:
            continue
        pos = None
        for idx, (i, m) in enumerate(after):
            h, l, c = bars[i][1], bars[i][2], bars[i][3]
            if pos:
                d, e, sl, tp, risk = pos
                hit = None
                if d == 1:
                    if l <= sl: hit = sl
                    elif h >= tp: hit = tp
                else:
                    if h >= sl: hit = sl
                    elif l <= tp: hit = tp
                last = idx == len(after) - 1
                if hit is None and last:
                    hit = c
                if hit is not None:
                    out.append(((hit - e) / pip * d - COST) / risk)
                    break
                continue
            if m >= e1:
                break
            d = 1 if c > rh else (-1 if c < rl else 0)
            if d == 0:
                continue
            stop = (rl if d == 1 else rh) if sl_mode == "far" else (rh + rl) / 2
            risk = abs(c - stop) / pip
            if risk < 3:
                continue
            pos = (d, c, stop, c + d * risk * pip * rr, risk)
    return out


def stats(rs):
    if not rs:
        return "trades   0"
    w = [r for r in rs if r > 0]
    gl = -sum(r for r in rs if r <= 0)
    pf = sum(w) / gl if gl > 0 else 99
    eq = pk = dd = 0.0
    for r in rs:
        eq += r; pk = max(pk, eq); dd = max(dd, pk - eq)
    return f"trades {len(rs):>3} | win {100*len(w)/len(rs):5.1f}% | PF {pf:4.2f} | net {sum(rs):+6.1f}R | maxDD {dd:4.1f}R"


def main():
    for sym in ["EURUSD=X", "GBPUSD=X", "EURJPY=X"]:
        pip = 0.01 if "JPY" in sym else 0.0001
        bars = fetch(sym, "5m", "60d")
        for kind in ("ASIA", "TBR"):
            print(f"\n=== {sym.replace('=X','')} {kind} breakout (M5, 60 days) ===")
            for sl_mode in ("far", "mid"):
                for rr in (1.0, 1.5, 2.0):
                    print(f"SL {sl_mode:<3} TP {rr:<3}R  {stats(run(bars, pip, kind, sl_mode, rr))}")


if __name__ == "__main__":
    main()
