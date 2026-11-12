#!/usr/bin/env python3
"""
Backtest of the PDF strategy "EUR/USD 5-Minute Trading Strategy" (v1.0).
Rules coded as precisely as the document allows:
  - Sessions (fixed UTC): London 07:00-11:00, New York 12:00-16:00. Flat at 20:00 UTC.
  - 15m context (last CLOSED 15m bar): long if close > EMA200 and EMA20 > EMA50 (structure proxy); short mirrored.
  - 5m: long if EMA20 > EMA50 and close > EMA200; short mirrored.
  - ADX(14) on 5m > 20.
  - Liquidity levels: Asian range high/low (00:00-07:00 UTC), previous day high/low,
    last confirmed 5m swing high/low (fractal, 2 bars each side).
  - Sweep + reclaim: a 5m candle trades beyond a level and CLOSES back inside it.
  - BOS: within 12 candles after the sweep, a candle CLOSES beyond the last confirmed
    swing high (long) / swing low (short) existing at the sweep. Entry at that close.
  - SL: beyond the sweep extreme + 1 pip buffer. Skip if stop < 3 or > 25 pips.
  - TP: RR x risk (default 2R).
  - Max 2 trades/day, stop for the day after 2 losses, one position at a time.
Cost 0.9 pip per trade. If SL and TP both touch in one candle -> SL.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from bt2 import fetch  # noqa: E402

COST = 0.9


def ema(vals, n):
    a = 2.0 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(a * v + (1 - a) * out[-1])
    return out


def adx(bars, n=14):
    h = [b[1] for b in bars]; l = [b[2] for b in bars]; c = [b[3] for b in bars]
    m = len(bars)
    tr = [0.0] * m; pdm = [0.0] * m; ndm = [0.0] * m
    for i in range(1, m):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        ndm[i] = dn if dn > up and dn > 0 else 0.0
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))

    def rma(x):
        o = [0.0] * m
        o[n] = sum(x[1:n + 1]) / n
        for i in range(n + 1, m):
            o[i] = (o[i - 1] * (n - 1) + x[i]) / n
        return o

    atr, p, q = rma(tr), rma(pdm), rma(ndm)
    dx = [0.0] * m
    for i in range(n, m):
        if atr[i] > 0:
            pdi, ndi = 100 * p[i] / atr[i], 100 * q[i] / atr[i]
            dx[i] = 100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi > 0 else 0
    out = [0.0] * m
    s = 2 * n
    if m > s:
        out[s] = sum(dx[n + 1:s + 1]) / n
        for i in range(s + 1, m):
            out[i] = (out[i - 1] * (n - 1) + dx[i]) / n
    return out


def run(m5, m15, pip, rr=2.0, use_htf=True, use_ema=True, use_adx=True, sessions=True, max_sl=25.0):
    n = len(m5)
    t = [b[0] for b in m5]; h = [b[1] for b in m5]; l = [b[2] for b in m5]; c = [b[3] for b in m5]
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    ax = adx(m5)
    # 15m context aligned to each 5m close
    c15 = [b[3] for b in m15]
    f20, f50, f200 = ema(c15, 20), ema(c15, 50), ema(c15, 200)
    ctx = [0] * n
    j = -1
    for i in range(n):
        close_t = t[i] + 300
        while j + 1 < len(m15) and m15[j + 1][0] + 900 <= close_t:
            j += 1
        if j >= 200:
            if c15[j] > f200[j] and f20[j] > f50[j]: ctx[i] = 1
            elif c15[j] < f200[j] and f20[j] < f50[j]: ctx[i] = -1

    day = [datetime.fromtimestamp(x, timezone.utc).date() for x in t]
    mins = [datetime.fromtimestamp(x, timezone.utc).hour * 60 + datetime.fromtimestamp(x, timezone.utc).minute for x in t]

    # daily levels
    asia, prev = {}, {}
    days_sorted = sorted(set(day))
    dh, dl = {}, {}
    for i in range(n):
        d = day[i]
        dh[d] = max(dh.get(d, -1e9), h[i]); dl[d] = min(dl.get(d, 1e9), l[i])
        if mins[i] < 420:
            a = asia.get(d, [-1e9, 1e9]); asia[d] = [max(a[0], h[i]), min(a[1], l[i])]
    for k in range(1, len(days_sorted)):
        prev[days_sorted[k]] = (dh[days_sorted[k - 1]], dl[days_sorted[k - 1]])

    res = []
    pos = None
    pend = {1: None, -1: None}      # pending sweep: (start_idx, extreme, bos_level)
    sw_hi = sw_lo = None            # last confirmed swing high/low (price)
    cur_day, trades_today, losses_today = None, 0, 0

    for i in range(210, n):
        if day[i] != cur_day:
            cur_day, trades_today, losses_today = day[i], 0, 0
            pend = {1: None, -1: None}
        # confirm fractal at i-2
        k = i - 2
        if h[k] > max(h[k - 2], h[k - 1], h[k + 1], h[k + 2]): sw_hi = h[k]
        if l[k] < min(l[k - 2], l[k - 1], l[k + 1], l[k + 2]): sw_lo = l[k]

        # manage open position
        if pos:
            d, e, sl, tp, risk = pos
            out = None
            if d == 1:
                if l[i] <= sl: out = sl
                elif h[i] >= tp: out = tp
            else:
                if h[i] >= sl: out = sl
                elif l[i] <= tp: out = tp
            if out is None and mins[i] + 5 >= 1200:
                out = c[i]
            if out is not None:
                r = ((out - e) / pip * d - COST) / risk
                res.append(r)
                if r < 0: losses_today += 1
                pos = None
            continue

        if mins[i] >= 1200:
            continue

        # levels for today
        lv_lo, lv_hi = [], []
        if mins[i] >= 420 and day[i] in asia:
            lv_hi.append(asia[day[i]][0]); lv_lo.append(asia[day[i]][1])
        if day[i] in prev:
            lv_hi.append(prev[day[i]][0]); lv_lo.append(prev[day[i]][1])
        if sw_lo is not None: lv_lo.append(sw_lo)
        if sw_hi is not None: lv_hi.append(sw_hi)

        # new sweeps
        if any(l[i] < L < c[i] for L in lv_lo) and sw_hi is not None:
            pend[1] = (i, l[i], sw_hi)
        if any(h[i] > L > c[i] for L in lv_hi) and sw_lo is not None:
            pend[-1] = (i, h[i], sw_lo)

        for d in (1, -1):
            p = pend[d]
            if not p:
                continue
            s, ext, bos = p
            if i - s > 12:
                pend[d] = None; continue
            ext = min(ext, l[i]) if d == 1 else max(ext, h[i])
            pend[d] = (s, ext, bos)
            if (d == 1 and c[i] < ext) or (d == -1 and c[i] > ext):
                pend[d] = None; continue
            broke = c[i] > bos if d == 1 else c[i] < bos
            if not broke or i == s:
                continue
            pend[d] = None
            # filters
            in_sess = (420 <= mins[i] < 660) or (720 <= mins[i] < 960)
            if sessions and not in_sess: continue
            if trades_today >= 2 or losses_today >= 2: continue
            if use_htf and ctx[i] != d: continue
            if use_ema and not ((e20[i] > e50[i] and c[i] > e200[i]) if d == 1 else (e20[i] < e50[i] and c[i] < e200[i])): continue
            if use_adx and ax[i] <= 20: continue
            entry = c[i]
            sl = ext - d * pip
            risk = abs(entry - sl) / pip
            if risk < 3 or risk > max_sl: continue
            pos = (d, entry, sl, entry + d * risk * pip * rr, risk)
            trades_today += 1
            break
    return res


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
    for sym in ["EURUSD=X", "GBPUSD=X"]:
        pip = 0.01 if "JPY" in sym else 0.0001
        m5 = fetch(sym, "5m", "60d")
        m15 = fetch(sym, "15m", "60d")
        print(f"\n=== {sym.replace('=X','')} PDF strategy (M5, 60 days) ===")
        rows = [
            ("Full rules, TP 2R", dict()),
            ("Full rules, TP 1.5R", dict(rr=1.5)),
            ("Full rules, TP 3R", dict(rr=3.0)),
            ("No ADX filter", dict(use_adx=False)),
            ("No 15m filter", dict(use_htf=False)),
            ("No EMA/ADX/15m (sweep+BOS only)", dict(use_htf=False, use_ema=False, use_adx=False)),
            ("Full rules, any time 07-20 UTC", dict(sessions=False)),
        ]
        for label, kw in rows:
            print(f"{label:<34} {stats(run(m5, m15, pip, **kw))}")


if __name__ == "__main__":
    main()
