#!/usr/bin/env python3
"""Dump every trade of the fitted EMA 34/50 strategy (EURUSD M5, Yahoo 60 days) for charting."""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
os.environ["BT_SRC"] = "yahoo"
import bt11  # noqa: E402

FAST, SLOW, WS, WE, AM, RR, CLOSE_H = 34, 50, 7, 16, 3.0, 3.0, 20


def main():
    bars = bt11.load()
    ctx = bt11.Ctx(bars)
    ef, es = ctx.e(FAST), ctx.e(SLOW)
    t0, t1 = ctx.t[0], ctx.t[-1]
    split = t0 + int((t1 - t0) * 0.75)
    trades, pos = [], None
    for i in range(SLOW, len(ctx.c)):
        m = ctx.mins[i]
        if pos:
            x = bt11.manage(ctx, i, pos[:5])
            why = None
            if x is not None:
                why = "TP" if (pos[3] and x == pos[3]) else "SL"
            elif m + 5 >= CLOSE_H * 60:
                x, why = ctx.c[i], "Time"
            if x is not None:
                d, e, sl, tp, risk, ie = pos
                r = ((x - e) / bt11.PIP * d - bt11.COST) / risk
                trades.append(dict(ie=ie, ix=i, te=ctx.t[ie], tx=ctx.t[i], d=d, e=e, sl=sl, tp=tp, x=x,
                                   why=why, r=round(r, 2), risk=round(risk, 1), oos=ctx.t[ie] >= split))
                pos = None
            continue
        if not (WS * 60 <= m < WE * 60):
            continue
        up = ef[i] > es[i] and ef[i - 1] <= es[i - 1]
        dn = ef[i] < es[i] and ef[i - 1] >= es[i - 1]
        if not (up or dn):
            continue
        d = 1 if up else -1
        risk = AM * ctx.atr[i] / bt11.PIP
        if risk < 3:
            continue
        c = ctx.c[i]
        pos = (d, c, c - d * risk * bt11.PIP, c + d * risk * bt11.PIP * RR, risk, i)

    # example window: the 3 trading days around the best out-of-sample trade
    oos = [t for t in trades if t["oos"]]
    ex = max(oos, key=lambda t: t["r"]) if oos else trades[-1]
    a = max(0, ex["ie"] - 60); b = min(len(ctx.c), ex["ix"] + 40)
    sample = dict(t=ctx.t[a:b], h=ctx.h[a:b], l=ctx.l[a:b], c=ctx.c[a:b],
                  ef=[round(x, 5) for x in ef[a:b]], es=[round(x, 5) for x in es[a:b]], a=a)
    out = dict(split=split, trades=trades, sample=sample, ex=ex)
    path = os.environ.get("BT_DUMP", "backtest/ema_trades.json")
    with open(path, "w") as f:
        json.dump(out, f)
    print("dumped", len(trades), "trades")
    rs = [t["r"] for t in trades]
    w = [r for r in rs if r > 0]; gl = -sum(r for r in rs if r <= 0)
    eq = pk = dd = 0.0
    for r in rs: eq += r; pk = max(pk, eq); dd = max(dd, pk - eq)
    weeks = {}
    for t in trades:
        dt = datetime.fromtimestamp(t["te"], timezone.utc); k = dt.isocalendar()[1]
        weeks.setdefault(k, []).append(t["r"])
    print(f"SUMMARY {os.environ.get('BT_SYM','EURUSD')}: trades {len(rs)}, win {100*len(w)/len(rs):.1f}%, PF {sum(w)/gl:.2f}, net {sum(rs):+.1f}R, maxDD {dd:.1f}R, "
          f"weeks {len(weeks)}, per week {len(rs)/len(weeks):.1f}, positive weeks {sum(1 for v in weeks.values() if sum(v)>0)}")
    print("WEEKS " + " ".join(f"{len(v)}:{sum(v):+.1f}" for k, v in sorted(weeks.items())))


if __name__ == "__main__":
    main()
