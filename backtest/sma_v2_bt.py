# SMA 9/18 pullback v2 engine: trend filters + exit modes, judged on ~10 years (EURUSD, EURJPY)
import numpy as np, pandas as pd, glob
from sma_filters_bt import adx, chop

def loadall(pair, tf):
    fs = sorted(glob.glob(f"data/{pair}_m5_365d_skip*.csv.gz")) + [f"data/{pair}_m5_730d.csv.gz"]
    df = pd.concat([pd.read_csv(f, header=None, names=list("tohlc")) for f in fs]).drop_duplicates("t").sort_values("t")
    df["t"] = pd.to_datetime(df.t, unit="s"); df = df.set_index("t")
    return df.resample(f"{tf}min").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()

def run(df, pip, filt=(), exit="rr", rr=3.0, imp=1.0, touch="slow", sess=(0, 24), cost=1.0,
        maxsl_atr=3.0, d1per=50):
    o, h, l, c = (df[k].values for k in "ohlc")
    t = df.index; hrs = t.hour.values
    f9 = pd.Series(c).rolling(9).mean().values; s18 = pd.Series(c).rolling(18).mean().values
    m200 = pd.Series(c).rolling(200).mean().values; slope = m200 - np.roll(m200, 20)
    ax, atr = adx(h, l, c)
    # daily trend: SMA(d1per) of daily closes, known only after the day closes (shifted)
    dc = df.c.resample("1D").last().dropna()
    dma = dc.rolling(d1per).mean(); dtrend = np.sign(dc - dma).shift(1)
    dslope = np.sign(dma - dma.shift(5)).shift(1)
    dt = dtrend.reindex(t.normalize()).values; ds = dslope.reindex(t.normalize()).values
    res = []; tt = []; d = st = 0; used = False; pos = None
    for i in range(260, len(c) - 1):
        if pos is not None:
            dd, e, sl, tp, r0, bi = pos
            done = None
            if (l[i] <= sl if dd == 1 else h[i] >= sl): done = dd * (sl - e)
            elif exit == "rr" and (h[i] >= tp if dd == 1 else l[i] <= tp): done = dd * (tp - e)
            elif exit == "trail" and dd * (c[i] - s18[i]) < 0: done = dd * (o[i + 1] - e)
            elif exit == "trail9" and dd * (c[i] - f9[i]) < 0 and i > bi + 1: done = dd * (o[i + 1] - e)
            if done is not None:
                res.append(done / r0 - cost * pip / r0); tt.append(t[i]); pos = None
            elif exit.startswith("trail") and dd * (c[i] - e) >= r0 and dd * (sl - e) < 0:
                pos = (dd, e, e, tp, r0, bi)          # breakeven after 1R
        up = f9[i] > s18[i] and f9[i - 1] <= s18[i - 1]; dn = f9[i] < s18[i] and f9[i - 1] >= s18[i - 1]
        if up or dn:
            d = 1 if up else -1; used = False; ext = h[i] if up else l[i]
            ok = True
            if "s200" in filt: ok &= d * (c[i] - m200[i]) > 0
            if "slope" in filt: ok &= d * slope[i] > 0
            if "adx" in filt: ok &= ax[i] > 25
            if "d1" in filt: ok &= dt[i] == d
            if "d1s" in filt: ok &= ds[i] == d
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
                e = o[i + 1]; sl = pb - d * pip; r0 = d * (e - sl)
                if 0.3 * atr[i] <= r0 <= maxsl_atr * atr[i]:
                    pos = (d, e, sl, e + d * rr * r0, r0, i); used = True
                st = 0 if used else 2
    return pd.Series(res, index=pd.DatetimeIndex(tt), dtype=float)

def summary(r):
    if len(r) == 0: return "0 trades"
    pf = r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9)
    yrs = r.groupby(r.index.year).sum()
    pos_yrs = (yrs > 0).sum()
    return f"{len(r):4} tr {r.sum():+6.1f}R PF {pf:.2f} avg {r.mean():+.2f}R  +yrs {pos_yrs}/{len(yrs)}"
