# SMA 9/18 pullback with trend / anti-sideways filters. Filter is checked at the crossover candle.
import numpy as np, pandas as pd, sys
from sma_pullback_bt import load

def adx(h, l, c, n=14):
    up = np.diff(h, prepend=h[0]); dn = -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0); ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1))))
    w = lambda x: pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().values
    atr = w(tr); pdi = 100 * w(pdm) / atr; ndi = 100 * w(ndm) / atr
    dx = 100 * abs(pdi - ndi) / np.maximum(pdi + ndi, 1e-12)
    return w(dx), atr

def chop(h, l, c, atr1, n=14):
    s = pd.Series(atr1).rolling(n).sum().values
    rng = pd.Series(h).rolling(n).max().values - pd.Series(l).rolling(n).min().values
    return 100 * np.log10(s / rng) / np.log10(n)

def run(df, filt, pip, rr=2.0, imp=1.0, touch="slow", sess=(7, 20), cost=0.8, maxsl=40):
    o, h, l, c = (df[k].values for k in "ohlc")
    hrs = df.index.hour.values
    f9 = pd.Series(c).rolling(9).mean().values; s18 = pd.Series(c).rolling(18).mean().values
    m200 = pd.Series(c).rolling(200).mean().values
    ax, atr = adx(h, l, c)
    tr1 = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1))))
    ch = chop(h, l, c, tr1)
    slope200 = m200 - np.roll(m200, 20)
    # higher timeframe (x4 bars ~ H1 for M15, x12 for M5) trend: SMA50 of HTF closes approximated by SMA(50*k)
    k = 12 if (df.index[1] - df.index[0]).seconds == 300 else (4 if (df.index[1] - df.index[0]).seconds == 900 else 4)
    htf = pd.Series(c).rolling(50 * k).mean().values
    res = []; d = st = 0; used = False; pos = None; idx = []
    for i in range(50 * k + 25, len(c) - 1):
        if pos:
            dd, e, sl, tp, t = pos
            if (l[i] <= sl if dd == 1 else h[i] >= sl): res.append(-1 - cost * pip / abs(e - sl)); idx.append(t); pos = None
            elif (h[i] >= tp if dd == 1 else l[i] <= tp): res.append(rr - cost * pip / abs(e - sl)); idx.append(t); pos = None
        up = f9[i] > s18[i] and f9[i - 1] <= s18[i - 1]; dn = f9[i] < s18[i] and f9[i - 1] >= s18[i - 1]
        if up or dn:
            d = 1 if up else -1; used = False; ext = h[i] if up else l[i]
            ok = True
            if "s200" in filt: ok &= d * (c[i] - m200[i]) > 0
            if "slope" in filt: ok &= d * slope200[i] > 0
            if "adx20" in filt: ok &= ax[i] > 20
            if "adx25" in filt: ok &= ax[i] > 25
            if "chop" in filt: ok &= ch[i] < 50
            if "htf" in filt: ok &= d * (c[i] - htf[i]) > 0
            st = 1 if ok else 0; continue
        if st == 0 or used: continue
        line = s18[i] if touch == "slow" else f9[i]
        if st == 1:
            ext = max(ext, h[i]) if d == 1 else min(ext, l[i])
            if d * (ext - f9[i]) >= imp * atr[i]: st = 2
        elif st == 2:
            if (d == 1 and l[i] <= line) or (d == -1 and h[i] >= line):
                st = 3; pb = l[i] if d == 1 else h[i]; tb = i
                if d * (c[i] - s18[i]) < 0: st = 0
        elif st == 3:
            pb = min(pb, l[i]) if d == 1 else max(pb, h[i])
            if d * (c[i] - s18[i]) < 0 or i - tb > 20: st = 0; continue
            bo = (c[i] > h[i - 1] and c[i] > f9[i]) if d == 1 else (c[i] < l[i - 1] and c[i] < f9[i])
            if bo and pos is None and sess[0] <= hrs[i] < sess[1]:
                e = o[i + 1]; sl = pb - d * pip; dist = abs(e - sl) / pip
                if 5 <= dist <= maxsl and d * (e - sl) > 0: pos = (d, e, sl, e + d * rr * abs(e - sl), i); used = True
                st = 0 if used else 2
    r = np.array(res); idx = np.array(idx)
    half = len(c) // 2
    out = []
    for part in (r, r[idx < half] if len(r) else r, r[idx >= half] if len(r) else r):
        if len(part) == 0: out.append((0, 0, 0)); continue
        pf = part[part > 0].sum() / max(-part[part < 0].sum(), 1e-9)
        out.append((len(part), part.sum(), pf))
    return out

if __name__ == "__main__":
    filters = [(), ("s200",), ("adx20",), ("adx25",), ("chop",), ("htf",), ("s200", "slope"),
               ("s200", "adx20"), ("s200", "adx25"), ("htf", "adx25"), ("s200", "slope", "adx25"), ("htf", "chop")]
    pairs = sys.argv[1].split(",")
    for tf in [5, 15]:
        data = {p: load(f"data/{p}_m5_730d.csv.gz", tf) for p in pairs}
        print(f"\n===== M{tf}  (total R / PF  | year1 PF | year2 PF), all pairs combined")
        for flt in filters:
            T = R = R1 = R2 = 0; line = []
            for p in pairs:
                pip = 0.01 if "JPY" in p else 0.0001
                a, b, c2 = run(data[p], flt, pip)
                T += a[0]; R += a[1]; R1 += b[1]; R2 += c2[1]; line.append(f"{p[:6]} {a[1]:+4.0f}")
            print(f"{'+'.join(flt) or 'none':20} {T:4} tr {R:+5.0f}R (y1 {R1:+4.0f} y2 {R2:+4.0f}) | " + " ".join(line))
