# Backtest of SmaPullbackBot rules (same logic as SmaPullbackBot.cs)
# usage: python3 sma_pullback_bt.py data.csv.gz [timeframe_minutes]
import sys, gzip, itertools
import numpy as np, pandas as pd

def load(path, tf):
    df = pd.read_csv(path, header=None, names=["t", "o", "h", "l", "c"])
    df["t"] = pd.to_datetime(df["t"], unit="s")
    df = df.set_index("t")
    if tf != 5:
        df = df.resample(f"{tf}min").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    return df

def run(df, fast=9, slow=18, imp_atr=1.0, rr=2.0, max_pb=20, touch="fast", sess=(7, 20),
        cost_pips=0.8, pip=0.0001, buf_pips=1.0, min_sl_pips=5, max_sl_pips=40):
    o, h, l, c = (df[k].values for k in "ohlc")
    hours = df.index.hour.values
    f = pd.Series(c).rolling(fast).mean().values
    s = pd.Series(c).rolling(slow).mean().values
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1))))
    atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().values
    n = len(c); res = []
    d = 0; st = 0; ext = 0; pb = 0; tb = 0; used = False
    pos = None
    for i in range(slow + 15, n - 1):
        # manage open trade on bar i
        if pos:
            pd_, e, sl, tp = pos
            hit_sl = (l[i] <= sl) if pd_ == 1 else (h[i] >= sl)
            hit_tp = (h[i] >= tp) if pd_ == 1 else (l[i] <= tp)
            if hit_sl: res.append(-1 - cost_pips * pip / abs(e - sl)); pos = None
            elif hit_tp: res.append(rr - cost_pips * pip / abs(e - sl)); pos = None
        up = f[i] > s[i] and f[i - 1] <= s[i - 1]
        dn = f[i] < s[i] and f[i - 1] >= s[i - 1]
        if up or dn:
            d = 1 if up else -1; st = 1; ext = h[i] if up else l[i]; used = False
            continue
        if st == 0 or used: continue
        line = f[i] if touch == "fast" else s[i]
        if st == 1:  # waiting for the move away
            ext = max(ext, h[i]) if d == 1 else min(ext, l[i])
            if d * (ext - f[i]) >= imp_atr * atr[i]: st = 2
        elif st == 2:  # waiting for pullback to MA
            if (d == 1 and l[i] <= line) or (d == -1 and h[i] >= line):
                st = 3; pb = l[i] if d == 1 else h[i]; tb = i
                if d * (c[i] - s[i]) < 0: st = 0
        elif st == 3:  # waiting for bounce
            pb = min(pb, l[i]) if d == 1 else max(pb, h[i])
            if d * (c[i] - s[i]) < 0 or i - tb > max_pb: st = 0; continue
            bounce = (c[i] > h[i - 1] and c[i] > f[i]) if d == 1 else (c[i] < l[i - 1] and c[i] < f[i])
            if bounce and pos is None and sess[0] <= hours[i] < sess[1]:
                e = o[i + 1]; sl = pb - d * buf_pips * pip
                dist = abs(e - sl) / pip
                if min_sl_pips <= dist <= max_sl_pips and d * (e - sl) > 0:
                    pos = (d, e, sl, e + d * rr * abs(e - sl)); used = True
                st = 2 if not used else 0
    r = np.array(res)
    if len(r) == 0: return 0, 0, 0, 0
    pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else 99
    return len(r), (r > 0).mean() * 100, r.sum(), pf

if __name__ == "__main__":
    tf = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    df = load(sys.argv[1], tf)
    pip = 0.01 if "JPY" in sys.argv[1] else 0.0001
    print(f"{sys.argv[1]} M{tf} bars={len(df)}")
    for imp, rr, touch in itertools.product([0.5, 1.0, 1.5], [1.5, 2.0, 3.0], ["fast", "slow"]):
        t, w, R, pf = run(df, imp_atr=imp, rr=rr, touch=touch, pip=pip)
        print(f"imp {imp} rr {rr} touch {touch:4}: trades {t:4} win {w:5.1f}% net {R:+7.1f}R PF {pf:.2f}")
