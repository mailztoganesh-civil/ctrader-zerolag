#!/usr/bin/env python3
"""
Zero Lag on M3 (built from Yahoo 1-minute, ~28 days), $150 account, 1% risk,
daily profit target: once the day's closed P/L reaches +$5, no new trades that day.
Rules as in the cBot: flips + pullbacks (impulse filter), band SL (min 5 / max 50 pips),
TP 2R, close on opposite flip, one position at a time, cost 0.9 pip, lots in 0.01 steps.
Each 3-hour entry window (UTC start hour 0..21) is tested, with and without the daily target.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "alerts"))
import zerolag_alert as za  # noqa: E402
from bt15 import fetch_1m  # noqa: E402

START_BAL, RISK, TARGET, COST, RR = 150.0, 0.01, 5.0, 0.9, 2.0


def to_m3(b1):
    out = {}
    for t, h, l, c in b1:
        k = t - t % 180
        if k not in out: out[k] = [k, h, l, c]
        else:
            o = out[k]; o[1] = max(o[1], h); o[2] = min(o[2], l); o[3] = c
    return [tuple(out[k]) for k in sorted(out)]


def sim(b, z, pip, usd_per_pip_unit, w0, w1, target):
    h = [x[1] for x in b]; l = [x[2] for x in b]
    cl, zl, vol, t = z["cl"], z["zl"], z["vol"], z["trd"]
    bal, pos, day, day_pnl = START_BAL, None, None, 0.0
    trades, daily = [], {}
    for i in range(z["start"] + 2, len(b)):
        dt = datetime.fromtimestamp(b[i][0], timezone.utc); d = dt.date()
        if d != day: day, day_pnl = d, 0.0
        flip_up = t[i] == 1 and t[i-1] <= 0; flip_dn = t[i] == -1 and t[i-1] >= 0
        if pos:
            dr, e, sl, tp, units = pos; x = None
            if (dr == 1 and l[i] <= sl) or (dr == -1 and h[i] >= sl): x = sl
            elif (dr == 1 and h[i] >= tp) or (dr == -1 and l[i] <= tp): x = tp
            elif (dr == 1 and flip_dn) or (dr == -1 and flip_up): x = cl[i]
            if x is not None:
                pnl = ((x - e) * dr / pip - COST) * pip * units * usd_per_pip_unit(x)
                bal += pnl; day_pnl += pnl; trades.append((b[i][0], pnl))
                daily[d] = daily.get(d, 0) + pnl; pos = None
            else:
                continue
        if target and day_pnl >= TARGET: continue
        if not (w0 <= dt.hour < w1): continue
        pb_up = t[i] == 1 and t[i-1] == 1 and cl[i] > zl[i] and cl[i-1] <= zl[i-1] and z["ok_up"][i]
        pb_dn = t[i] == -1 and t[i-1] == -1 and cl[i] < zl[i] and cl[i-1] >= zl[i-1] and z["ok_dn"][i]
        dr = 1 if (flip_up or pb_up) else (-1 if (flip_dn or pb_dn) else 0)
        if not dr: continue
        band = zl[i] - vol[i] if dr == 1 else zl[i] + vol[i]
        e = cl[i]
        if (dr == 1 and band >= e) or (dr == -1 and band <= e): continue
        slp = max(abs(e - band) / pip, 5.0)
        if slp > 50: continue
        units = int(bal * RISK / (slp * pip * usd_per_pip_unit(e)) // 1000) * 1000
        if units < 1000: continue
        pos = (dr, e, e - dr * slp * pip, e + dr * slp * pip * RR, units)
    return trades, daily, bal


def line(label, trades, daily, bal, mid):
    if not trades: return f"{label:<26} no trades"
    p = [x for _, x in trades]; w = sum(x for x in p if x > 0); ls = -sum(x for x in p if x <= 0)
    eq = pk = dd = 0.0
    for x in p: eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    a = sum(x for t, x in trades if t < mid); b = sum(x for t, x in trades if t >= mid)
    hit = sum(1 for v in daily.values() if v >= TARGET)
    return (f"{label:<26} tr {len(p):>3} | PF {w/ls if ls else 99:4.2f} | net ${sum(p):+6.1f} | maxDD ${dd:5.1f} | "
            f"days {len(daily):>2} (+$5 hit {hit:>2}, losing {sum(1 for v in daily.values() if v < 0):>2}) | 1st ${a:+5.1f} 2nd ${b:+5.1f}")


def main():
    for sym, pip, upp in (("EURUSD=X", 0.0001, lambda px: 1.0), ("USDCAD=X", 0.0001, lambda px: 1.0 / px)):
        b = to_m3(fetch_1m(sym))
        z = za.zero_lag(b)
        mid = b[len(b) // 2][0]
        d0 = datetime.fromtimestamp(b[0][0], timezone.utc).date(); d1 = datetime.fromtimestamp(b[-1][0], timezone.utc).date()
        print(f"\n=== {sym[:6]} M3 {d0} -> {d1} ({len(b)} bars). Windows in Dubai time (UTC+4) ===")
        print(line("All day, no daily target", *sim(b, z, pip, upp, 0, 24, False), mid))
        print(line("All day, stop at +$5/day", *sim(b, z, pip, upp, 0, 24, True), mid))
        for w0 in range(0, 22):
            lab = f"{(w0+4)%24:02d}:00-{(w0+7)%24:02d}:00 Dubai +$5 stop"
            print(line(lab, *sim(b, z, pip, upp, w0, w0 + 3, True), mid))


if __name__ == "__main__":
    main()
