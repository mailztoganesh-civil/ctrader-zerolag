#!/usr/bin/env python3
"""
First-pullback-only study (M5, last 60 days).
No trade on the flip. After each flip, take ONLY the first pullback entry,
and only if the flip "worked" by then:
  impulse : price moved >= 1 ATR in flip direction since the flip
  better  : pullback entry price better than the flip close (buy: above it)
  both    : impulse and better
  none    : no condition (first pullback always)
Exits: band SL (min 5 pips) with TP 2R / 1.5R, or opposite flip (with band SL).
Cost 0.9 pip per trade. Results in R (1R = stop distance).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "alerts"))
import zerolag_alert as za  # noqa: E402
from bt2 import fetch  # noqa: E402

COST = 0.9
MIN_SL = 5.0


def atr_rma(bars, L):
    n = len(bars)
    tr = [bars[0][1] - bars[0][2]] + [max(bars[i][1] - bars[i][2], abs(bars[i][1] - bars[i - 1][3]),
                                          abs(bars[i][2] - bars[i - 1][3])) for i in range(1, n)]
    a = [0.0] * n
    a[L - 1] = sum(tr[:L]) / L
    for i in range(L, n):
        a[i] = (a[i - 1] * (L - 1) + tr[i]) / L
    return a


def run(bars, pip, cond="impulse", exit_mode="tp2"):
    z = za.zero_lag(bars)
    hi = [b[1] for b in bars]
    lo = [b[2] for b in bars]
    cl, zl, vol, t = z["cl"], z["zl"], z["vol"], z["trd"]
    atr = atr_rma(bars, za.LENGTH)
    res = []
    pos = None
    d = 0
    flip_close = extreme = 0.0
    taken = True
    for i in range(z["start"] + 1, len(bars)):
        # manage open trade
        if pos:
            pd, e, sl, tp, slp = pos
            out = None
            if pd == 1:
                if lo[i] <= sl: out = sl
                elif tp and hi[i] >= tp: out = tp
            else:
                if hi[i] >= sl: out = sl
                elif tp and lo[i] <= tp: out = tp
            if out is None and t[i] != t[i - 1] and t[i] == -pd:
                out = cl[i]   # opposite flip exit
            if out is not None:
                res.append(((out - e) / pip * pd - COST) / slp)
                pos = None
        # new flip -> arm
        if t[i] != t[i - 1] and t[i] != 0:
            d = t[i]
            flip_close = cl[i]
            extreme = hi[i] if d == 1 else lo[i]
            taken = False
            continue
        if d == 0 or taken:
            continue
        extreme = max(extreme, hi[i]) if d == 1 else min(extreme, lo[i])
        pb = (cl[i] > zl[i] and cl[i - 1] <= zl[i - 1]) if d == 1 else (cl[i] < zl[i] and cl[i - 1] >= zl[i - 1])
        if not pb:
            continue
        taken = True   # only the FIRST pullback counts, traded or not
        imp = ((extreme - flip_close) if d == 1 else (flip_close - extreme)) >= atr[i]
        better = (cl[i] > flip_close) if d == 1 else (cl[i] < flip_close)
        ok = {"none": True, "impulse": imp, "better": better, "both": imp and better}[cond]
        if not ok or pos:
            continue
        e = cl[i]
        band = zl[i] - vol[i] if d == 1 else zl[i] + vol[i]
        if (d == 1 and band >= e) or (d == -1 and band <= e):
            continue
        slp = max(abs(e - band) / pip, MIN_SL)
        sl = e - d * slp * pip
        tp = {"tp2": e + d * slp * pip * 2, "tp15": e + d * slp * pip * 1.5, "flip": None}[exit_mode]
        pos = (d, e, sl, tp, slp)
    return res


def stats(rs):
    if not rs:
        return "trades    0"
    w = [r for r in rs if r > 0]
    gl = -sum(r for r in rs if r <= 0)
    pf = sum(w) / gl if gl > 0 else 99
    eq = pk = dd = 0.0
    for r in rs:
        eq += r; pk = max(pk, eq); dd = max(dd, pk - eq)
    return f"trades {len(rs):>3} | win {100*len(w)/len(rs):5.1f}% | PF {pf:4.2f} | net {sum(rs):+6.1f}R | maxDD {dd:5.1f}R"


def main():
    for sym in ["EURUSD=X", "EURJPY=X", "GBPUSD=X"]:
        pip = 0.01 if "JPY" in sym else 0.0001
        bars = fetch(sym, "5m", "60d")
        print(f"\n=== {sym.replace('=X','')} M5 first pullback only (60 days) ===")
        for cond in ("none", "impulse", "better", "both"):
            for ex, exname in (("tp2", "TP 2R"), ("tp15", "TP 1.5R"), ("flip", "exit on flip")):
                print(f"{cond:<8} {exname:<13} {stats(run(bars, pip, cond, ex))}")


if __name__ == "__main__":
    main()
