#!/usr/bin/env python3
"""
Fit filters on the LOSING trades of the EMA 34/50 strategy (EURUSD + USDCAD, Yahoo M5 60 days).
Features at entry: UTC hour, weekday, ATR pips, EMA50 slope (12 bars, in ATR), H1-trend agreement
(close vs EMA600 on M5 ~ EMA50 on H1), ADX(14), previous trade lost, distance close-EMA50 (in ATR).
Each single filter is fitted on the first 75% of trades (pooled) and checked on the last 25%.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
os.environ["BT_SRC"] = "yahoo"
import bt11  # noqa: E402
from bt6 import adx  # noqa: E402

FAST, SLOW, WS, WE, AM, RR, CLOSE_H = 34, 50, 7, 16, 3.0, 3.0, 20
COSTS = {"EURUSD": 0.9, "USDCAD": 1.1}


def trades_for(sym):
    os.environ["BT_SYM"] = sym
    bars = bt11.load()
    ctx = bt11.Ctx(bars)
    ef, es, e600 = ctx.e(FAST), ctx.e(SLOW), ctx.e(600)
    ax = adx([(b[0], b[2], b[3], b[4]) for b in bars])
    out, pos, prev_loss = [], None, False
    for i in range(650, len(ctx.c)):
        m = ctx.mins[i]
        if pos:
            x = bt11.manage(ctx, i, pos[:5])
            if x is None and m + 5 >= CLOSE_H * 60: x = ctx.c[i]
            if x is not None:
                d, e, sl, tp, risk, f = pos
                r = ((x - e) / bt11.PIP * d - COSTS[sym]) / risk
                f["r"] = r; out.append(f); prev_loss = r < 0; pos = None
            continue
        if not (WS * 60 <= m < WE * 60): continue
        up = ef[i] > es[i] and ef[i-1] <= es[i-1]
        dn = ef[i] < es[i] and ef[i-1] >= es[i-1]
        if not (up or dn): continue
        d = 1 if up else -1
        a = ctx.atr[i]
        risk = AM * a / bt11.PIP
        if risk < 3: continue
        c = ctx.c[i]
        dt = datetime.fromtimestamp(ctx.t[i], timezone.utc)
        f = dict(sym=sym, t=ctx.t[i], hour=dt.hour, wd=dt.weekday(), atr=a / bt11.PIP,
                 slope=d * (es[i] - es[i-12]) / a, h1=1 if d * (c - e600[i]) > 0 else 0,
                 adx=ax[i], prevloss=1 if prev_loss else 0, dist=d * (c - es[i]) / a)
        pos = (d, c, c - d * risk * bt11.PIP, c + d * risk * bt11.PIP * RR, risk, f)
    return out


def st(rs):
    if not rs: return (0, 0.0, 0.0)
    w = sum(r for r in rs if r > 0); l = -sum(r for r in rs if r <= 0)
    return (len(rs), w / l if l > 0 else 99.0, sum(rs))


def fmt(s): return f"{s[0]:>3} tr PF {s[1]:4.2f} {s[2]:+6.1f}R"


def main():
    T = sorted(trades_for("EURUSD") + trades_for("USDCAD"), key=lambda f: f["t"])
    k = int(len(T) * 0.75)
    IS, OOS = T[:k], T[k:]
    print(f"{len(T)} trades pooled (EURUSD+USDCAD). Fit on first {len(IS)}, check on last {len(OOS)} "
          f"(from {datetime.fromtimestamp(OOS[0]['t'], timezone.utc).date()})")
    print(f"NO FILTER                          fit {fmt(st([f['r'] for f in IS]))} || new {fmt(st([f['r'] for f in OOS]))}")

    # describe losers vs winners on fit data
    win = [f for f in IS if f["r"] > 0]; los = [f for f in IS if f["r"] <= 0]
    print("\nAverage features, winners vs losers (fit period):")
    for key in ("hour", "atr", "slope", "h1", "adx", "prevloss", "dist"):
        aw = sum(f[key] for f in win) / len(win); al = sum(f[key] for f in los) / len(los)
        print(f"  {key:<9} winners {aw:7.2f} | losers {al:7.2f}")

    def q(key, p):
        v = sorted(f[key] for f in IS); return v[int(p * (len(v) - 1))]

    filters = {}
    for h0, h1 in ((7, 10), (10, 13), (13, 16)):
        filters[f"skip hours {h0}-{h1} UTC"] = (lambda f, a=h0, b=h1: not (a <= f["hour"] < b))
    for wd, nm in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri")):
        filters[f"skip {nm}"] = (lambda f, w=wd: f["wd"] != w)
    for p in (0.33, 0.5, 0.67):
        thr = q("atr", p)
        filters[f"ATR > {thr:.1f} pips (q{int(p*100)})"] = (lambda f, t=thr: f["atr"] > t)
        filters[f"ATR < {thr:.1f} pips (q{int(p*100)})"] = (lambda f, t=thr: f["atr"] < t)
        thr = q("slope", p)
        filters[f"EMA50 slope > {thr:.2f} (q{int(p*100)})"] = (lambda f, t=thr: f["slope"] > t)
        thr = q("adx", p)
        filters[f"ADX > {thr:.0f} (q{int(p*100)})"] = (lambda f, t=thr: f["adx"] > t)
        filters[f"ADX < {thr:.0f} (q{int(p*100)})"] = (lambda f, t=thr: f["adx"] < t)
        thr = q("dist", p)
        filters[f"dist from EMA50 < {thr:.2f} ATR (q{int(p*100)})"] = (lambda f, t=thr: f["dist"] < t)
    filters["with H1 trend only"] = lambda f: f["h1"] == 1
    filters["against H1 trend only"] = lambda f: f["h1"] == 0
    filters["skip after a loss"] = lambda f: f["prevloss"] == 0
    filters["only after a loss"] = lambda f: f["prevloss"] == 1

    rows = []
    for name, fn in filters.items():
        a = st([f["r"] for f in IS if fn(f)]); b = st([f["r"] for f in OOS if fn(f)])
        if a[0] >= 25: rows.append((name, a, b))
    rows.sort(key=lambda r: -r[1][1])
    print("\nSingle filters, sorted by fitted PF (min 25 fitted trades):")
    for name, a, b in rows:
        print(f"{name:<34} fit {fmt(a)} || new {fmt(b)}")
    top = rows[:5]
    print(f"\nTop-5 fitted filters on new data: avg PF {sum(b[1] for _, _, b in top)/len(top):.2f}, "
          f"{sum(1 for _, _, b in top if b[2] > 0)}/5 profitable; baseline new-data PF {st([f['r'] for f in OOS])[1]:.2f}")


if __name__ == "__main__":
    main()
