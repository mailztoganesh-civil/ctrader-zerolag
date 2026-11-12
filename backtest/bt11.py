#!/usr/bin/env python3
"""
Curve-fitting study on ~1 year of EURUSD M5 (built from Dukascopy 1-minute BID candles).
Fit (in-sample) = first 9 months; check (out-of-sample) = last 3 months.
Families and grids:
  F1 Session range breakout: range start/end hour, last entry hour, SL mid/far, RR.
  F2 EMA cross in a session window, ATR stop, RR target.
  F3 Bollinger mean reversion in a session window, ATR stop, exit at middle band.
Cost 0.9 pip per trade (BID data, so this covers spread + commission). Results in R.
"""
import gzip
import lzma
import os
import struct
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from itertools import product

DATA = os.path.join(os.path.dirname(__file__), "data", "eurusd_m5.csv.gz")
PIP = 0.0001
COST = 0.9


# ---------------- data ----------------
def dukascopy_day(d):
    url = f"https://datafeed.dukascopy.com/datafeed/EURUSD/{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            if not raw:
                return []
            buf = lzma.decompress(raw, format=lzma.FORMAT_ALONE)
            base = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
            out = []
            for k in range(0, len(buf) - 23, 24):
                t, o, c, lo, hi, v = struct.unpack(">iiiiif", buf[k:k + 24])
                if v <= 0:
                    continue
                out.append((base + t, o / 1e5, hi / 1e5, lo / 1e5, c / 1e5))
            return out
        except Exception:
            time.sleep(2)
    return []


def build_data():
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=372)
    m5 = {}
    d = start
    n = 0
    while d <= end:
        if d.weekday() != 5:
            for t, o, h, l, c in dukascopy_day(d):
                k = t - t % 300
                if k not in m5:
                    m5[k] = [o, h, l, c]
                else:
                    b = m5[k]; b[1] = max(b[1], h); b[2] = min(b[2], l); b[3] = c
            n += 1
        d += timedelta(days=1)
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with gzip.open(DATA, "wt") as f:
        for k in sorted(m5):
            o, h, l, c = m5[k]
            f.write(f"{k},{o:.5f},{h:.5f},{l:.5f},{c:.5f}\n")
    print(f"downloaded {n} days, {len(m5)} M5 bars")


def load():
    if not os.path.exists(DATA):
        build_data()
    bars = []
    with gzip.open(DATA, "rt") as f:
        for line in f:
            t, o, h, l, c = line.split(",")
            bars.append((int(t), float(o), float(h), float(l), float(c)))
    return bars


# ---------------- helpers ----------------
def ema(v, n):
    a = 2 / (n + 1); o = [v[0]]
    for x in v[1:]: o.append(a * x + (1 - a) * o[-1])
    return o


def atr(h, l, c, n=14):
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1])) for i in range(1, len(c))]
    o = [0.0] * len(c); o[n-1] = sum(tr[:n]) / n
    for i in range(n, len(c)): o[i] = (o[i-1] * (n - 1) + tr[i]) / n
    return o


def bands(c, n, k):
    m = [None] * len(c); u = [None] * len(c); lo = [None] * len(c)
    s = s2 = 0.0
    for i, x in enumerate(c):
        s += x; s2 += x * x
        if i >= n: s -= c[i-n]; s2 -= c[i-n] ** 2
        if i >= n - 1:
            mu = s / n; sd = max(s2 / n - mu * mu, 0) ** 0.5
            m[i], u[i], lo[i] = mu, mu + k * sd, mu - k * sd
    return m, u, lo


class Ctx:
    def __init__(self, bars):
        self.t = [b[0] for b in bars]; self.o = [b[1] for b in bars]; self.h = [b[2] for b in bars]
        self.l = [b[3] for b in bars]; self.c = [b[4] for b in bars]
        self.mins = []; self.day = []
        for x in self.t:
            dt = datetime.fromtimestamp(x, timezone.utc)
            self.mins.append(dt.hour * 60 + dt.minute); self.day.append(dt.date())
        self.atr = atr(self.h, self.l, self.c)
        self._ema = {}; self._bb = {}

    def e(self, n):
        if n not in self._ema: self._ema[n] = ema(self.c, n)
        return self._ema[n]

    def bb(self, n, k):
        if (n, k) not in self._bb: self._bb[(n, k)] = bands(self.c, n, k)
        return self._bb[(n, k)]


def manage(ctx, i, pos):
    d, e, sl, tp, risk = pos
    h, l = ctx.h[i], ctx.l[i]
    if d == 1:
        if l <= sl: return sl
        if tp and h >= tp: return tp
    else:
        if h >= sl: return sl
        if tp and l <= tp: return tp
    return None


# ---------------- families ----------------
def f1(ctx, rs, re, ee, slm, rr, close_h=20):
    out = []  # (time, R)
    days = {}
    for i, d in enumerate(ctx.day):
        days.setdefault(d, []).append(i)
    for d, idx in days.items():
        rng = [i for i in idx if rs * 60 <= ctx.mins[i] < re * 60]
        if len(rng) < (re - rs) * 12 * 0.8: continue
        hi = max(ctx.h[i] for i in rng); lo = min(ctx.l[i] for i in rng)
        pos = None
        for i in idx:
            m = ctx.mins[i]
            if m < re * 60: continue
            if pos:
                x = manage(ctx, i, pos)
                if x is None and m + 5 >= close_h * 60: x = ctx.c[i]
                if x is not None:
                    out.append((ctx.t[i], ((x - pos[1]) / PIP * pos[0] - COST) / pos[4])); break
                continue
            if m + 5 > ee * 60: break
            c = ctx.c[i]
            dr = 1 if c > hi else (-1 if c < lo else 0)
            if not dr: continue
            stop = (hi + lo) / 2 if slm == "mid" else (lo if dr == 1 else hi)
            risk = abs(c - stop) / PIP
            if risk < 3: continue
            pos = (dr, c, stop, c + dr * risk * PIP * rr, risk)
    return out


def f2(ctx, fast, slow, ws, we, am, rr, close_h=20):
    ef, es = ctx.e(fast), ctx.e(slow)
    out, pos = [], None
    for i in range(slow, len(ctx.c)):
        m = ctx.mins[i]
        if pos:
            x = manage(ctx, i, pos)
            if x is None and m + 5 >= close_h * 60: x = ctx.c[i]
            if x is not None:
                out.append((ctx.t[i], ((x - pos[1]) / PIP * pos[0] - COST) / pos[4])); pos = None
            continue
        if not (ws * 60 <= m < we * 60): continue
        up = ef[i] > es[i] and ef[i-1] <= es[i-1]
        dn = ef[i] < es[i] and ef[i-1] >= es[i-1]
        if not (up or dn): continue
        dr = 1 if up else -1
        risk = am * ctx.atr[i] / PIP
        if risk < 3: continue
        c = ctx.c[i]
        pos = (dr, c, c - dr * risk * PIP, c + dr * risk * PIP * rr, risk)
    return out


SESS = {"asia": (0, 6), "london": (7, 11), "ny": (12, 16), "lon+ny": (7, 16)}


def f3(ctx, n, k, sess, am):
    m_, u, lo = ctx.bb(n, k)
    s0, s1 = SESS[sess]
    out, pos = [], None
    for i in range(n, len(ctx.c)):
        m = ctx.mins[i]
        if pos:
            x = manage(ctx, i, pos)
            c = ctx.c[i]
            if x is None and ((pos[0] == 1 and c >= m_[i]) or (pos[0] == -1 and c <= m_[i])): x = c
            if x is None and m >= s1 * 60 + 60: x = c
            if x is not None:
                out.append((ctx.t[i], ((x - pos[1]) / PIP * pos[0] - COST) / pos[4])); pos = None
            continue
        if not (s0 * 60 <= m < s1 * 60): continue
        c = ctx.c[i]
        dr = 1 if c < lo[i] else (-1 if c > u[i] else 0)
        if not dr: continue
        risk = am * ctx.atr[i] / PIP
        if risk < 3: continue
        pos = (dr, c, c - dr * risk * PIP, None, risk)
    return out


# ---------------- evaluation ----------------
def st(rs):
    if not rs: return dict(n=0, pf=0, net=0, win=0, dd=0)
    w = [r for r in rs if r > 0]; gl = -sum(r for r in rs if r <= 0)
    eq = pk = dd = 0.0
    for r in rs: eq += r; pk = max(pk, eq); dd = max(dd, pk - eq)
    return dict(n=len(rs), pf=(sum(w) / gl if gl > 0 else 99), net=sum(rs), win=100 * len(w) / len(rs), dd=dd)


def fmt(s):
    return f"{s['n']:>4} tr | win {s['win']:4.1f}% | PF {s['pf']:4.2f} | {s['net']:+6.1f}R | DD {s['dd']:4.1f}R"


def main():
    bars = load()
    ctx = Ctx(bars)
    t0, t1 = ctx.t[0], ctx.t[-1]
    split = t0 + int((t1 - t0) * 0.75)
    print(f"EURUSD M5 {datetime.fromtimestamp(t0, timezone.utc).date()} -> {datetime.fromtimestamp(t1, timezone.utc).date()}, "
          f"{len(bars)} bars. Fit until {datetime.fromtimestamp(split, timezone.utc).date()}, then out-of-sample.")

    grids = {
        "F1 range breakout": (f1, [p for p in product(range(0, 7), (6, 7, 8), (10, 12, 14), ("mid", "far"), (1.0, 1.5, 2.0, 3.0))
                                   if p[1] - p[0] >= 3]),
        "F2 EMA cross": (f2, list(product((8, 13, 20, 34), (50, 100, 200), (7, 8, 12), (11, 16), (1.5, 2.0, 3.0), (1.5, 2.0, 3.0)))),
        "F3 Bollinger MR": (f3, list(product((20, 50), (2.0, 2.5, 3.0), tuple(SESS), (1.5, 2.0, 3.0)))),
    }
    allres = []
    for fam, (fn, grid) in grids.items():
        t_start = time.time()
        rows = []
        for p in grid:
            if fam == "F2 EMA cross" and p[0] >= p[1]: continue
            res = fn(ctx, *p)
            ins = [r for t, r in res if t < split]; oos = [r for t, r in res if t >= split]
            rows.append((p, st(ins), st(oos)))
        ok = [r for r in rows if r[1]["n"] >= 60]
        ok.sort(key=lambda r: -r[1]["pf"])
        print(f"\n=== {fam}: {len(rows)} combos tested ({time.time() - t_start:.0f}s); top 5 by in-sample PF ===")
        for p, a, b in ok[:5]:
            print(f"{str(p):<34} FIT {fmt(a)}  ||  NEW DATA {fmt(b)}")
        # how did the in-sample top-10% do out-of-sample on average?
        top = ok[:max(1, len(ok) // 10)]
        if not top:
            continue
        avg_pf_oos = sum(b["pf"] for _, _, b in top) / len(top)
        frac_pos = sum(1 for _, _, b in top if b["net"] > 0) / len(top)
        print(f"Top 10% of fits on new data: avg PF {avg_pf_oos:.2f}, {100*frac_pos:.0f}% profitable")
        allres += [(fam, *r) for r in ok]
    if not allres:
        return
    allres.sort(key=lambda r: -r[2]["pf"])
    fam, p, a, b = allres[0]
    print(f"\nBEST FIT OVERALL: {fam} {p}\n  fit period:  {fmt(a)}\n  new data:    {fmt(b)}")


if __name__ == "__main__":
    main()
