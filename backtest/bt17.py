#!/usr/bin/env python3
"""
XAUUSD M5 (Yahoo, ~60 days): SMA 10/20 trend + pullback, only OUTSIDE the LPL-LPH zone (Structural Pivots Method).
Daily pivots from the previous UTC day: P=(H+L+C)/3, BC=(H+L)/2, TC=2P-BC, R1=2P-L, S1=2P-H.
Zones: none | cpr (between TC and BC) | s1r1 (between S1 and R1).
Long: SMA10 > SMA20, close above zone top, candle low <= SMA(touch), close > SMA10 -> buy at close.
Short mirrored. SL = k x ATR(14), TP = RR x SL. One position at a time, flat at 20:00 UTC.
Cost $0.25 per ounce (spread + commission). Results in R. Fit = first 75%, check = last 25%.
"""
import os
import sys
from datetime import datetime, timezone
from itertools import product

sys.path.insert(0, os.path.dirname(__file__))
from bt2 import fetch  # noqa: E402

SYM = os.environ.get("BT_SYM", "")
IS_FX = SYM not in ("", "XAUUSD")
COST = 0.00009 if IS_FX else 0.25
BUF = 0.00003 if IS_FX else 0.3


def sma(v, n):
    o = [None] * len(v); s = 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n: s -= v[i - n]
        if i >= n - 1: o[i] = s / n
    return o


def atr(b, n=14):
    tr = [b[0][1] - b[0][2]] + [max(b[i][1] - b[i][2], abs(b[i][1] - b[i-1][3]), abs(b[i][2] - b[i-1][3])) for i in range(1, len(b))]
    o = [0.0] * len(b); o[n-1] = sum(tr[:n]) / n
    for i in range(n, len(b)): o[i] = (o[i-1] * (n - 1) + tr[i]) / n
    return o


def prep(b):
    c = [x[3] for x in b]
    days, dhlc = [], {}
    for t, h, l, cc in b:
        d = datetime.fromtimestamp(t, timezone.utc).date()
        if d not in dhlc: dhlc[d] = [h, l, cc]; days.append(d)
        else: x = dhlc[d]; x[0] = max(x[0], h); x[1] = min(x[1], l); x[2] = cc
    piv = {}
    for k in range(1, len(days)):
        H, L, C = dhlc[days[k-1]]
        P = (H + L + C) / 3; BC = (H + L) / 2; TC = 2 * P - BC
        piv[days[k]] = dict(cpr=(min(TC, BC), max(TC, BC)), s1r1=(2 * P - H, 2 * P - L))
    # ---- Structural Pivots Method (small + large pivots), computed bar by bar ----
    h_, l_ = [x[1] for x in b], [x[2] for x in b]
    n = len(b)
    LPH = [None] * n; LPL = [None] * n; SPH = [None] * n; SPL = [None] * n
    look = "sph"; anc = 0; cnt = 0
    last_sph = last_spl = None
    lp_look = "lph"; lp_ext_i = 0; last_lph = last_lpl = None
    for i in range(1, n):
        # small pivots
        if look == "sph":
            if h_[i] >= h_[anc]: anc, cnt = i, 0
            elif l_[i] < l_[anc] and c[i] < c[anc]:
                cnt += 1
                if cnt == 2:
                    last_sph = h_[anc]; look = "spl"
                    anc = min(range(anc, i + 1), key=lambda k: l_[k]); cnt = 0
        else:
            if l_[i] <= l_[anc]: anc, cnt = i, 0
            elif h_[i] > h_[anc] and c[i] > c[anc]:
                cnt += 1
                if cnt == 2:
                    last_spl = l_[anc]; look = "sph"
                    anc = max(range(anc, i + 1), key=lambda k: h_[k]); cnt = 0
        # large pivots
        if lp_look == "lph":
            if h_[i] >= h_[lp_ext_i]: lp_ext_i = i
            if last_spl is not None and l_[i] < last_spl:
                last_lph = h_[lp_ext_i]; lp_look = "lpl"; lp_ext_i = i
        else:
            if l_[i] <= l_[lp_ext_i]: lp_ext_i = i
            if last_sph is not None and h_[i] > last_sph:
                last_lpl = l_[lp_ext_i]; lp_look = "lph"; lp_ext_i = i
        LPH[i], LPL[i], SPH[i], SPL[i] = last_lph, last_lpl, last_sph, last_spl
    info = [(datetime.fromtimestamp(t, timezone.utc).date(),
             datetime.fromtimestamp(t, timezone.utc).hour) for t, *_ in b]
    return dict(c=c, h=[x[1] for x in b], l=[x[2] for x in b], s10=sma(c, 10), s20=sma(c, 20),
                atr=atr(b), piv=piv, info=info, t=[x[0] for x in b], LPH=LPH, LPL=LPL, SPH=SPH, SPL=SPL)


def run(P, zone, touch, k, rr, sess):
    c, h, l, s10, s20, at = P["c"], P["h"], P["l"], P["s10"], P["s20"], P["atr"]
    st = s10 if touch == 10 else s20
    out, pos = [], None
    for i in range(25, len(c)):
        d, hr = P["info"][i]
        if pos:
            dr, e, sl, tp, risk = pos; x = None
            if (dr == 1 and l[i] <= sl) or (dr == -1 and h[i] >= sl): x = sl
            elif (dr == 1 and h[i] >= tp) or (dr == -1 and l[i] <= tp): x = tp
            elif hr >= 20: x = c[i]
            if x is not None: out.append((P["t"][i], ((x - e) * dr - COST) / risk)); pos = None
            continue
        if not (sess[0] <= hr < sess[1]): continue
        lph, lpl = P["LPH"][i], P["LPL"][i]
        if zone != "none" and (lph is None or lpl is None): continue
        up_ok = zone == "none" or (zone == "outside" and c[i] > lph) or (zone == "inside" and lpl < c[i] < lph)
        dn_ok = zone == "none" or (zone == "outside" and c[i] < lpl) or (zone == "inside" and lpl < c[i] < lph)
        dr = 0
        if s10[i] > s20[i] and l[i] <= st[i] and c[i] > s10[i] and up_ok: dr = 1
        elif s10[i] < s20[i] and h[i] >= st[i] and c[i] < s10[i] and dn_ok: dr = -1
        if not dr: continue
        if k == "spl":
            ref = P["SPL"][i] if dr == 1 else P["SPH"][i]
            if ref is None: continue
            risk = (c[i] - ref) * dr + BUF
            if risk < 0.5 * at[i] or risk > 6 * at[i]: continue
        else:
            risk = k * at[i]
        if risk <= 0: continue
        pos = (dr, c[i], c[i] - dr * risk, c[i] + dr * risk * rr, risk)
    return out


def st(rs):
    if not rs: return dict(n=0, win=0, pf=0, net=0, dd=0)
    w = [r for r in rs if r > 0]; gl = -sum(r for r in rs if r <= 0)
    eq = pk = dd = 0.0
    for r in rs: eq += r; pk = max(pk, eq); dd = max(dd, pk - eq)
    return dict(n=len(rs), win=100 * len(w) / len(rs), pf=sum(w) / gl if gl > 0 else 99, net=sum(rs), dd=dd)


def fmt(s): return f"{s['n']:>4} tr | win {s['win']:4.1f}% | PF {s['pf']:4.2f} | {s['net']:+6.1f}R | DD {s['dd']:4.1f}R"


def main():
    b = None
    for sym in ((SYM + "=X",) if IS_FX else ("XAUUSD=X", "GC=F")):
        try:
            b = fetch(sym, "5m", "60d")
            if len(b) > 5000: break
        except Exception as e:
            print(sym, "failed:", e)
    print(f"Data {sym}: {len(b)} bars, {datetime.fromtimestamp(b[0][0], timezone.utc).date()} -> "
          f"{datetime.fromtimestamp(b[-1][0], timezone.utc).date()}, price ~{b[-1][3]:.0f}")
    P = prep(b)
    marks = sum(1 for i in range(1, len(b)) if P["LPH"][i] != P["LPH"][i-1]) + sum(1 for i in range(1, len(b)) if P["LPL"][i] != P["LPL"][i-1])
    sm = sum(1 for i in range(1, len(b)) if P["SPH"][i] != P["SPH"][i-1]) + sum(1 for i in range(1, len(b)) if P["SPL"][i] != P["SPL"][i-1])
    print(f"Structural pivots marked: {sm} small, {marks} large (about {marks/ (len(b)/276):.1f} large pivots per day)")
    split = b[int(len(b) * 0.75)][0]
    S = {"all day": (0, 24), "London+NY 07-16 UTC": (7, 16), "NY 12-17 UTC": (12, 17)}
    rows = []
    for zone, touch, k, rr, sn in product(("none", "outside", "inside"), (10, 20), (1.0, 1.5, 2.0, 3.0, "spl"), (1.0, 1.5, 2.0, 3.0), S):
        res = run(P, zone, touch, k, rr, S[sn])
        a = st([r for t, r in res if t < split]); z = st([r for t, r in res if t >= split])
        rows.append(((zone, touch, k, rr, sn), a, z))
    print(f"\n{len(rows)} combinations. LPH/LPL zone effect (touch SMA10, SL 2xATR, TP 2R, London+NY):")
    for zone in ("none", "outside", "inside"):
        r = next(x for x in rows if x[0] == (zone, 10, 2.0, 2.0, "London+NY 07-16 UTC"))
        print(f"  zone {zone:<5} FIT {fmt(r[1])} || NEW {fmt(r[2])}")
    ok = sorted([r for r in rows if r[1]["n"] >= 40], key=lambda r: -r[1]["pf"])
    print("\nTop 8 fits (zone, touch, SLxATR, RR, session):")
    for p, a, z in ok[:8]:
        print(f"  {str(p):<52} FIT {fmt(a)} || NEW {fmt(z)}")
    top = ok[:max(1, len(ok) // 10)]
    print(f"\nTop 10% of fits on new data: avg PF {sum(z['pf'] for _, _, z in top)/len(top):.2f}, "
          f"{100*sum(1 for _, _, z in top if z['net'] > 0)/len(top):.0f}% profitable")
    both = sorted([r for r in rows if r[1]["n"] >= 40 and r[2]["n"] >= 12 and r[1]["pf"] > 1.1 and r[2]["pf"] > 1.1],
                  key=lambda r: -min(r[1]["pf"], r[2]["pf"]))
    print(f"Profitable (PF > 1.1) in BOTH periods: {len(both)} of {len(rows)}")
    for p, a, z in both[:8]:
        print(f"  {str(p):<52} FIT {fmt(a)} || NEW {fmt(z)}")


if __name__ == "__main__":
    main()
