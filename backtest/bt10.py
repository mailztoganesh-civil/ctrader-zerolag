#!/usr/bin/env python3
"""
"GR Rotation" - multi-pair trend-breakout system that chooses which pairs to trade.
Data: Yahoo H1, ~730 days, 16 pairs. Standard (not optimised) settings.

Weekly selection (Monday 00:00 UTC, using only past data):
  score = efficiency ratio of last 20 daily closes  x  (daily ATR in pips / cost in pips)
  trade only the top N pairs that week (N=4). Baseline: all pairs.
Entry (H1 close): long if close > highest high of previous 24 bars and close > EMA200(H1);
                  short mirrored.
Stop: 2 x ATR(14, H1). Exit: close below lowest low of last 12 bars (long) / above highest
high (short), or stop. Max 4 open positions total, one per pair. Cost per pair (pips) below.
Results in R (1R = initial stop distance); first and second half reported separately.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from bt2 import fetch  # noqa: E402

COST = {"EURUSD": 0.8, "GBPUSD": 1.0, "USDJPY": 0.9, "AUDUSD": 0.9, "USDCAD": 1.1, "USDCHF": 1.1,
        "NZDUSD": 1.2, "EURJPY": 1.3, "GBPJPY": 1.8, "EURGBP": 1.1, "AUDJPY": 1.4, "EURAUD": 1.6,
        "EURCHF": 1.4, "GBPCHF": 2.0, "CADJPY": 1.6, "AUDNZD": 1.8}


def ema(v, n):
    a = 2 / (n + 1); o = [v[0]]
    for x in v[1:]: o.append(a * x + (1 - a) * o[-1])
    return o


def atr(b, n=14):
    tr = [b[0][1] - b[0][2]] + [max(b[i][1] - b[i][2], abs(b[i][1] - b[i-1][3]), abs(b[i][2] - b[i-1][3])) for i in range(1, len(b))]
    o = [0.0] * len(b); o[n - 1] = sum(tr[:n]) / n
    for i in range(n, len(b)): o[i] = (o[i-1] * (n - 1) + tr[i]) / n
    return o


def daily_from_h1(b):
    days = {}
    for t, h, l, c in b:
        d = datetime.fromtimestamp(t, timezone.utc).date()
        if d not in days: days[d] = [h, l, c]
        else: days[d][0] = max(days[d][0], h); days[d][1] = min(days[d][1], l); days[d][2] = c
    ks = sorted(days)
    return ks, [days[k] for k in ks]


def weekly_scores(name, b, pip):
    ks, dv = daily_from_h1(b)
    out = {}
    for i in range(21, len(ks)):
        if ks[i].weekday() != 0:
            continue
        closes = [dv[j][2] for j in range(i - 20, i)]
        path = sum(abs(closes[j] - closes[j - 1]) for j in range(1, len(closes)))
        er = abs(closes[-1] - closes[0]) / path if path > 0 else 0
        rngs = [(dv[j][0] - dv[j][1]) / pip for j in range(i - 14, i)]
        out[ks[i]] = er * (sum(rngs) / len(rngs)) / COST[name]
    return out


def signals(name, b, pip):
    h = [x[1] for x in b]; l = [x[2] for x in b]; c = [x[3] for x in b]
    e200 = ema(c, 200); a = atr(b)
    trades = []   # (entry_time, exit_time, R)
    pos = None
    for i in range(201, len(b)):
        if pos:
            d, e, sl, risk, t0 = pos
            out = None
            if (d == 1 and l[i] <= sl) or (d == -1 and h[i] >= sl):
                out = sl
            else:
                lo12, hi12 = min(l[i-12:i]), max(h[i-12:i])
                if d == 1 and c[i] < lo12: out = c[i]
                if d == -1 and c[i] > hi12: out = c[i]
            if out is not None:
                trades.append((t0, b[i][0], ((out - e) / pip * d - COST[name]) / risk))
                pos = None
            continue
        hi24, lo24 = max(h[i-24:i]), min(l[i-24:i])
        if c[i] > hi24 and c[i] > e200[i]:
            sl = c[i] - 2 * a[i]; pos = (1, c[i], sl, (c[i] - sl) / pip, b[i][0])
        elif c[i] < lo24 and c[i] < e200[i]:
            sl = c[i] + 2 * a[i]; pos = (-1, c[i], sl, (sl - c[i]) / pip, b[i][0])
    return trades


def portfolio(cands, allowed, max_open=4):
    """cands: list of (entry, exit, R, pair). Take in entry order if pair allowed that week and slots free."""
    taken, open_ = [], []
    for e, x, r, p in sorted(cands):
        open_ = [(xx, pp) for xx, pp in open_ if xx > e]
        if len(open_) >= max_open or any(pp == p for _, pp in open_):
            continue
        if allowed is not None and not allowed(p, e):
            continue
        taken.append((x, r, p)); open_.append((x, p))
    return sorted(taken)


def stats(label, tk, t_mid):
    def one(rs):
        if not rs: return "trades   0"
        w = [r for r in rs if r > 0]; gl = -sum(r for r in rs if r <= 0)
        pf = sum(w) / gl if gl > 0 else 99
        eq = pk = dd = 0.0
        for r in rs: eq += r; pk = max(pk, eq); dd = max(dd, pk - eq)
        return f"trades {len(rs):>4} | win {100*len(w)/len(rs):5.1f}% | PF {pf:4.2f} | net {sum(rs):+6.1f}R | maxDD {dd:5.1f}R"
    a = [r for x, r, _ in tk if x < t_mid]; b = [r for x, r, _ in tk if x >= t_mid]
    return f"{label:<26} ALL {one([r for _, r, _ in tk])}\n{'':<26} 1st half {one(a)}\n{'':<26} 2nd half {one(b)}"


def main():
    data, cands, scores = {}, [], {}
    for p in COST:
        try:
            b = fetch(p + "=X", "1h", "730d")
        except Exception as e:
            print("skip", p, e); continue
        pip = 0.01 if "JPY" in p else 0.0001
        data[p] = b
        for e, x, r in signals(p, b, pip):
            cands.append((e, x, r, p))
        scores[p] = weekly_scores(p, b, pip)
    t0 = min(b[0][0] for b in data.values()); t1 = max(b[-1][0] for b in data.values())
    t_mid = (t0 + t1) // 2
    print(f"{len(data)} pairs, {datetime.fromtimestamp(t0, timezone.utc).date()} -> {datetime.fromtimestamp(t1, timezone.utc).date()}, "
          f"split at {datetime.fromtimestamp(t_mid, timezone.utc).date()}")

    # weekly top-N selection
    weeks = sorted({w for s in scores.values() for w in s})
    def top_n(n):
        sel = {}
        for w in weeks:
            ranked = sorted((s[w], p) for p, s in scores.items() if w in s)
            sel[w] = {p for _, p in ranked[-n:]}
        def allowed(p, e):
            d = datetime.fromtimestamp(e, timezone.utc).date()
            ws = [w for w in weeks if w <= d]
            return bool(ws) and p in sel[ws[-1]]
        return allowed, sel

    print(stats("Baseline: all 16 pairs", portfolio(cands, None), t_mid))
    for n in (3, 4, 6):
        allowed, sel = top_n(n)
        print(stats(f"GR Rotation top {n}", portfolio(cands, allowed), t_mid))
    # show which pairs were picked most often (top 4)
    _, sel = top_n(4)
    cnt = {}
    for s in sel.values():
        for p in s: cnt[p] = cnt.get(p, 0) + 1
    print("Most selected (top 4):", ", ".join(f"{p} {c}" for p, c in sorted(cnt.items(), key=lambda x: -x[1])[:8]))
    # per-pair raw results (no selection, no slot limit) for context
    print("Per pair, every signal:")
    for p in data:
        rs = [r for e, x, r, q in cands if q == p]
        w = [r for r in rs if r > 0]; gl = -sum(r for r in rs if r <= 0)
        print(f"  {p}: trades {len(rs)}, PF {sum(w)/gl if gl else 0:.2f}, net {sum(rs):+.1f}R")


if __name__ == "__main__":
    main()
