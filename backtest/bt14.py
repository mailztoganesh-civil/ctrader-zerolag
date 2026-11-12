#!/usr/bin/env python3
"""
High-win-rate trend pullback on EURUSD, sideways avoided.
Regime (trend only): ADX(14) > A and EMA50 above/below EMA200 (A=0 means no sideways filter).
Entry: in an uptrend, candle dips to EMA20 (low <= EMA20) and closes back above it -> buy at close;
       mirrored for downtrend.
Exit: TP = T x ATR(14), SL = S x ATR(14) (T < S gives a high win rate), time exit after 24 bars.
If SL and TP are both touched in one candle -> SL. Cost 0.9 pip. Results in R (1R = stop).
First half = fit, second half = check.
"""
import os
import sys
from itertools import product

sys.path.insert(0, os.path.dirname(__file__))
from bt2 import fetch  # noqa: E402
from bt6 import adx, ema  # noqa: E402

PIP, COST = 0.0001, 0.9


def atr(b, n=14):
    tr = [b[0][1] - b[0][2]] + [max(b[i][1] - b[i][2], abs(b[i][1] - b[i-1][3]), abs(b[i][2] - b[i-1][3])) for i in range(1, len(b))]
    o = [0.0] * len(b); o[n-1] = sum(tr[:n]) / n
    for i in range(n, len(b)): o[i] = (o[i-1] * (n - 1) + tr[i]) / n
    return o


def run(b, ind, A, S, T, hold=24):
    e20, e50, e200, ax, at = ind
    h = [x[1] for x in b]; l = [x[2] for x in b]; c = [x[3] for x in b]
    out, pos = [], None
    for i in range(210, len(b)):
        if pos:
            d, e, sl, tp, risk, i0 = pos
            x = None
            if (d == 1 and l[i] <= sl) or (d == -1 and h[i] >= sl): x = sl
            elif (d == 1 and h[i] >= tp) or (d == -1 and l[i] <= tp): x = tp
            elif i - i0 >= hold: x = c[i]
            if x is not None:
                out.append((b[i][0], ((x - e) / PIP * d - COST) / risk)); pos = None
            continue
        if ax[i] <= A: continue
        d = 0
        if e50[i] > e200[i] and c[i] > e50[i] and l[i] <= e20[i] and c[i] > e20[i]: d = 1
        elif e50[i] < e200[i] and c[i] < e50[i] and h[i] >= e20[i] and c[i] < e20[i]: d = -1
        if not d: continue
        risk = S * at[i] / PIP
        if risk < 3: continue
        pos = (d, c[i], c[i] - d * S * at[i], c[i] + d * T * at[i], risk, i)
    return out


def st(rs):
    if not rs: return dict(n=0, win=0, pf=0, net=0, dd=0)
    w = [r for r in rs if r > 0]; gl = -sum(r for r in rs if r <= 0)
    eq = pk = dd = 0.0
    for r in rs: eq += r; pk = max(pk, eq); dd = max(dd, pk - eq)
    return dict(n=len(rs), win=100 * len(w) / len(rs), pf=sum(w) / gl if gl > 0 else 99, net=sum(rs), dd=dd)


def fmt(s): return f"{s['n']:>4} tr | win {s['win']:4.1f}% | PF {s['pf']:4.2f} | {s['net']:+6.1f}R | DD {s['dd']:4.1f}R"


def study(label, b, min_n):
    c = [x[3] for x in b]
    ind = (ema(c, 20), ema(c, 50), ema(c, 200), adx(b), atr(b))
    mid = b[len(b) // 2][0]
    rows = []
    for A, S, T in product((0, 20, 25, 30, 35), (1.5, 2.0, 3.0), (0.5, 0.75, 1.0, 1.5)):
        res = run(b, ind, A, S, T)
        a = st([r for t, r in res if t < mid]); z = st([r for t, r in res if t >= mid])
        rows.append(((A, S, T), a, z))
    print(f"\n=== {label}: {len(b)} bars, {len(rows)} combos ===")
    print("Sideways filter effect (SL 2xATR, TP 1xATR), whole period:")
    for A in (0, 20, 25, 30, 35):
        r = next(x for x in rows if x[0] == (A, 2.0, 1.0))
        tot = st([])  # placeholder
        print(f"  ADX > {A:<2}  1st half {fmt(r[1])}  ||  2nd half {fmt(r[2])}")
    ok = [r for r in rows if r[1]["n"] >= min_n and r[1]["win"] >= 60]
    ok.sort(key=lambda r: -r[1]["pf"])
    print("Top 6 high-win-rate fits (ADX, SLxATR, TPxATR):")
    for p, a, z in ok[:6]:
        print(f"  {str(p):<16} FIT {fmt(a)}  ||  NEW {fmt(z)}")
    both = [r for r in rows if r[1]["n"] >= min_n and r[2]["n"] >= min_n // 2 and r[1]["pf"] > 1.05 and r[2]["pf"] > 1.05]
    print(f"Combos profitable (PF > 1.05) in BOTH halves: {len(both)} of {len(rows)}")
    for p, a, z in sorted(both, key=lambda r: -min(r[1]["pf"], r[2]["pf"]))[:5]:
        print(f"  {str(p):<16} 1st {fmt(a)}  ||  2nd {fmt(z)}")


def main():
    study("EURUSD H1 (~2.8 years)", fetch("EURUSD=X", "1h", "730d"), 40)
    study("EURUSD M15 (60 days)", fetch("EURUSD=X", "15m", "60d"), 20)
    study("EURUSD M5 (60 days)", fetch("EURUSD=X", "5m", "60d"), 40)


if __name__ == "__main__":
    main()
