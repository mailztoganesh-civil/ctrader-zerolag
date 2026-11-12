#!/usr/bin/env python3
"""
Five more intraday strategy ideas, tested with costs.
 S1 Asian mean reversion (M5): 23:00-05:00 UTC, close outside Bollinger(20,2) -> fade,
    exit at middle band / stop 2xATR(14) / 06:00 UTC. Cost 1.2 pip (wider Asian spreads).
 S2 London-morning momentum: move 07:00->12:00 UTC > threshold -> trade same direction
    12:00->16:00 UTC (and the fade version). No stop. Cost 0.9.
 S3 Previous-day high/low fake-out (M5, 07:00-16:00 UTC): wick beyond PDH/PDL, close back
    inside -> fade. SL beyond wick +1 pip, TP 2R, flat 20:00 UTC. One per side per day.
 S4 Asian breakout (mid stop, 2R) only when Asian range < 80% of its 20-day median.
 S5 RSI(2) pullback with H1 EMA200 trend (07:00-16:00 UTC): RSI2<10 buy in uptrend,
    >90 sell in downtrend; exit RSI2 crosses 60/40 or close beyond SMA5; SL 2xATR; flat 20:00.
Results in R (S1, S3, S4, S5) or pips (S2).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from bt2 import fetch  # noqa: E402
import bt5  # noqa: E402

UTC = timezone.utc


def ema(v, n):
    a = 2 / (n + 1); o = [v[0]]
    for x in v[1:]: o.append(a * x + (1 - a) * o[-1])
    return o


def sma(v, n):
    o = [None] * len(v); s = 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n: s -= v[i - n]
        if i >= n - 1: o[i] = s / n
    return o


def std(v, n, m):
    o = [None] * len(v)
    for i in range(n - 1, len(v)):
        w = v[i - n + 1:i + 1]; mu = m[i]
        o[i] = (sum((x - mu) ** 2 for x in w) / n) ** 0.5
    return o


def atr(b, n=14):
    tr = [b[0][1] - b[0][2]] + [max(b[i][1] - b[i][2], abs(b[i][1] - b[i-1][3]), abs(b[i][2] - b[i-1][3])) for i in range(1, len(b))]
    o = [0.0] * len(b); o[n - 1] = sum(tr[:n]) / n
    for i in range(n, len(b)): o[i] = (o[i-1] * (n - 1) + tr[i]) / n
    return o


def rsi(c, n=2):
    o = [50.0] * len(c); g = l = 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i-1]
        up, dn = max(d, 0), max(-d, 0)
        if i <= n: g += up / n; l += dn / n
        else: g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n
        if i >= n: o[i] = 100 if l == 0 else 100 - 100 / (1 + g / l)
    return o


def hm(ts):
    d = datetime.fromtimestamp(ts, UTC); return d.date(), d.hour * 60 + d.minute


def stats(rs, unit="R"):
    if not rs: return "trades   0"
    w = [r for r in rs if r > 0]; gl = -sum(r for r in rs if r <= 0)
    pf = sum(w) / gl if gl > 0 else 99
    eq = pk = dd = 0.0
    for r in rs: eq += r; pk = max(pk, eq); dd = max(dd, pk - eq)
    return f"trades {len(rs):>4} | win {100*len(w)/len(rs):5.1f}% | PF {pf:4.2f} | net {sum(rs):+8.1f}{unit} | maxDD {dd:6.1f}{unit}"


# ---------------- S1 ----------------
def s1(b, pip, cost=1.2):
    c = [x[3] for x in b]; h = [x[1] for x in b]; l = [x[2] for x in b]
    m = sma(c, 20); sd = std(c, 20, m); a = atr(b)
    res, pos = [], None
    for i in range(30, len(b)):
        _, mn = hm(b[i][0]); mn_close = (mn + 5) % 1440
        if pos:
            d, e, sl = pos; out = None
            if (d == 1 and l[i] <= sl) or (d == -1 and h[i] >= sl): out = sl
            elif (d == 1 and c[i] >= m[i]) or (d == -1 and c[i] <= m[i]): out = c[i]
            elif 300 <= mn_close <= 420 and mn_close >= 360: out = c[i]
            if out is not None:
                res.append(((out - e) / pip * d - cost) / (abs(e - sl) / pip)); pos = None
            continue
        asian = mn_close >= 23 * 60 or mn_close < 5 * 60
        if not asian or sd[i] is None: continue
        if c[i] < m[i] - 2 * sd[i]: pos = (1, c[i], c[i] - 2 * a[i])
        elif c[i] > m[i] + 2 * sd[i]: pos = (-1, c[i], c[i] + 2 * a[i])
    return res


# ---------------- S2 ----------------
def s2(b, pip, thr, step, fade=False, end_h=16, cost=0.9):
    cm = {x[0] + step: x[3] for x in b}
    days = sorted({hm(x[0])[0] for x in b})
    res = []
    for d in days:
        base = datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()
        p7, p12, pe = cm.get(int(base + 7 * 3600)), cm.get(int(base + 12 * 3600)), cm.get(int(base + end_h * 3600))
        if None in (p7, p12, pe): continue
        mv = (p12 - p7) / pip
        if abs(mv) < thr: continue
        d0 = (1 if mv > 0 else -1) * (-1 if fade else 1)
        res.append((pe - p12) / pip * d0 - cost)
    return res


# ---------------- S3 ----------------
def s3(b, pip, rr=2.0, cost=0.9):
    h = [x[1] for x in b]; l = [x[2] for x in b]; c = [x[3] for x in b]
    day_hl = {}
    for x in b:
        d, _ = hm(x[0]); hh, ll = day_hl.get(d, (-1e9, 1e9)); day_hl[d] = (max(hh, x[1]), min(ll, x[2]))
    days = sorted(day_hl); prev = {days[k]: day_hl[days[k-1]] for k in range(1, len(days))}
    res, pos, used = [], None, set()
    for i in range(1, len(b)):
        d, mn = hm(b[i][0])
        if pos:
            dr, e, sl, tp, r = pos; out = None
            if (dr == 1 and l[i] <= sl) or (dr == -1 and h[i] >= sl): out = sl
            elif (dr == 1 and h[i] >= tp) or (dr == -1 and l[i] <= tp): out = tp
            elif mn + 5 >= 1200: out = c[i]
            if out is not None: res.append(((out - e) / pip * dr - cost) / r); pos = None
            continue
        if d not in prev or not (420 <= mn < 960): continue
        pdh, pdl = prev[d]
        if h[i] > pdh and c[i] < pdh and (d, -1) not in used:
            sl = h[i] + pip; r = (sl - c[i]) / pip
            if 3 <= r <= 30: pos = (-1, c[i], sl, c[i] - r * pip * rr, r); used.add((d, -1))
        elif l[i] < pdl and c[i] > pdl and (d, 1) not in used:
            sl = l[i] - pip; r = (c[i] - sl) / pip
            if 3 <= r <= 30: pos = (1, c[i], sl, c[i] + r * pip * rr, r); used.add((d, 1))
    return res


# ---------------- S4 ----------------
def s4(b, pip, ratio=0.8):
    widths = [(d, (rh - rl) / pip) for d, rh, rl, _, _ in bt5.sessions(b, "ASIA")]
    ok = set()
    for k in range(20, len(widths)):
        hist = sorted(w for _, w in widths[k-20:k]); med = hist[10]
        if widths[k][1] < ratio * med: ok.add(widths[k][0])
    all_r = bt5.run(b, pip, "ASIA", "mid", 2.0)
    filt = [t["r"] for t in bt5.LOG if t["day"] in {str(x) for x in ok}]
    return all_r, filt


# ---------------- S5 ----------------
def s5(b, h1, pip, cost=0.9):
    c = [x[3] for x in b]; h = [x[1] for x in b]; l = [x[2] for x in b]
    r2 = rsi(c, 2); s5_ = sma(c, 5); a = atr(b)
    hc = [x[3] for x in h1]; he = ema(hc, 200)
    trend, j = [0] * len(b), -1
    for i, x in enumerate(b):
        while j + 1 < len(h1) and h1[j + 1][0] + 3600 <= x[0] + 300: j += 1
        if j >= 200: trend[i] = 1 if hc[j] > he[j] else -1
    res, pos = [], None
    for i in range(30, len(b)):
        _, mn = hm(b[i][0])
        if pos:
            d, e, sl = pos; out = None
            if (d == 1 and l[i] <= sl) or (d == -1 and h[i] >= sl): out = sl
            elif (d == 1 and (r2[i] > 60 or c[i] > s5_[i])) or (d == -1 and (r2[i] < 40 or c[i] < s5_[i])): out = c[i]
            elif mn + 5 >= 1200: out = c[i]
            if out is not None: res.append(((out - e) / pip * d - cost) / (abs(e - sl) / pip)); pos = None
            continue
        if not (420 <= mn < 960): continue
        if trend[i] == 1 and r2[i] < 10: pos = (1, c[i], c[i] - 2 * a[i])
        elif trend[i] == -1 and r2[i] > 90: pos = (-1, c[i], c[i] + 2 * a[i])
    return res


def main():
    for sym in ["EURUSD=X", "GBPUSD=X"]:
        pip = 0.0001; nm = sym.replace("=X", "")
        m5 = fetch(sym, "5m", "60d"); h1 = fetch(sym, "1h", "730d")
        print(f"\n=========== {nm} ===========")
        print(f"S1 Asian mean reversion  M5 60d   {stats(s1(m5, pip))}")
        for thr in (10, 20, 30):
            print(f"S2 London momentum >{thr}p  H1 ~2.8y {stats(s2(h1, pip, thr, 3600), 'p')}")
            print(f"S2 London FADE     >{thr}p  H1 ~2.8y {stats(s2(h1, pip, thr, 3600, fade=True), 'p')}")
        print(f"S2 London momentum >20p  M5 60d   {stats(s2(m5, pip, 20, 300), 'p')}")
        print(f"S3 PDH/PDL fake-out      M5 60d   {stats(s3(m5, pip))}")
        print(f"S3 PDH/PDL fake-out      H1 ~2.8y {stats(s3(h1, pip))}")
        a, f = s4(m5, pip)
        print(f"S4 Asian BO all days     M5 60d   {stats(a)}")
        print(f"S4 Asian BO narrow only  M5 60d   {stats(f)}")
        a, f = s4(h1, pip)
        print(f"S4 Asian BO all days     H1 ~2.8y {stats(a)}")
        print(f"S4 Asian BO narrow only  H1 ~2.8y {stats(f)}")
        print(f"S5 RSI2 + H1 trend       M5 60d   {stats(s5(m5, h1, pip))}")


if __name__ == "__main__":
    main()
