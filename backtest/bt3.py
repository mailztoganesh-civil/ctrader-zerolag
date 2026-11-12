#!/usr/bin/env python3
"""
Stop-and-reverse on Zero Lag trend flips. No SL, no TP.
Bullish flip -> close sell (if any), open buy at candle close.
Bearish flip -> close buy, open sell. Results in pips, cost 0.9 pip/trade.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "alerts"))
import zerolag_alert as za  # noqa: E402
from bt2 import fetch  # noqa: E402

COST = 0.9


def sar(bars, pip):
    z = za.zero_lag(bars)
    cl, t, lo, hi = z["cl"], z["trd"], [b[2] for b in bars], [b[1] for b in bars]
    pos = None  # (dir, entry, entry_index)
    res, worst_open = [], 0.0
    for i in range(z["start"] + 1, len(bars)):
        if pos:
            d, e, _ = pos
            adverse = ((e - lo[i]) if d == 1 else (hi[i] - e)) / pip
            worst_open = max(worst_open, adverse)
        flip = t[i] != t[i - 1] and t[i] != 0
        if flip:
            if pos:
                d, e, k = pos
                res.append(((cl[i] - e) / pip * d - COST, i - k))
            pos = (t[i], cl[i], i)
    return res, worst_open


def report(name, res, worst_open, bar_min, pip_usd_001):
    if not res:
        print(f"{name:<22} no trades")
        return
    p = [r for r, _ in res]
    w = [x for x in p if x > 0]
    l = [x for x in p if x <= 0]
    pf = sum(w) / -sum(l) if l and sum(l) < 0 else 99
    eq = pk = dd = 0.0
    for x in p:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    hold = sum(h for _, h in res) / len(res) * bar_min
    print(f"{name:<22} trades {len(p):>4} | win {100*len(w)/len(p):5.1f}% | PF {pf:4.2f} | "
          f"net {sum(p):+8.1f} pips | avg win {sum(w)/max(1,len(w)):5.1f} | avg loss {sum(l)/max(1,len(l)):6.1f} | "
          f"worst trade {min(p):6.1f} | max DD {dd:6.1f} pips | avg hold {hold/60:4.1f}h | "
          f"0.01 lot: ${sum(p)*pip_usd_001:+.0f}")


def main():
    for sym in ["EURUSD=X", "EURJPY=X", "GBPUSD=X"]:
        pip = 0.01 if "JPY" in sym else 0.0001
        usd = 0.07 if "JPY" in sym else 0.10   # approx $ per pip at 0.01 lot
        print(f"\n=== {sym.replace('=X','')} stop-and-reverse on flips ===")
        for label, iv, rng, mins in (("M5  (60 days)", "5m", "60d", 5),
                                     ("M15 (60 days)", "15m", "60d", 15),
                                     ("H1  (~2 years)", "1h", "730d", 60)):
            bars = fetch(sym, iv, rng)
            res, wo = sar(bars, pip)
            report(label, res, wo, mins, usd)


if __name__ == "__main__":
    main()
