#!/usr/bin/env python3
"""
Filter / timeframe study for the Zero Lag bot.
Same trade simulation as bt.py, plus:
  - any timeframe (5m / 15m / 1h)
  - higher-timeframe trend filter (trade only with HTF Zero Lag trend)
  - flat-market filter (skip when zero-lag line slope < k x band over N bars)
Results in R. Cost 0.9 pip/trade, SL = band (min 5 pips), TP 2R, max 2 positions.
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "alerts"))
import zerolag_alert as za  # noqa: E402

COST_PIPS = 0.9
MIN_SL_PIPS = 5.0
RR = 2.0
MAX_POS = 2


def fetch(symbol, interval, rng):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={rng}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (zerolag-backtest)"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)
            res = d["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            bars = [(int(t), float(h), float(l), float(c))
                    for t, h, l, c in zip(res["timestamp"], q["high"], q["low"], q["close"])
                    if None not in (h, l, c)]
            return bars[:-1]
        except Exception as e:  # retry
            err = e
            time.sleep(3)
    raise RuntimeError(f"fetch failed {symbol} {interval}: {err}")


def htf_map(bars, htf_bars, htf_sec):
    """For each base bar, trend of the last CLOSED higher-timeframe bar."""
    z = za.zero_lag(htf_bars)
    out = [0] * len(bars)
    j = -1
    for i, b in enumerate(bars):
        close_t = b[0] + (bars[1][0] - bars[0][0] if len(bars) > 1 else 300)
        while j + 1 < len(htf_bars) and htf_bars[j + 1][0] + htf_sec <= close_t:
            j += 1
        out[i] = z["trd"][j] if j >= 0 else 0
    return out


def run(bars, z, pip, impulse=True, use_pullbacks=True, htf=None, slope_k=0.0, slope_n=12):
    n = len(bars)
    hi = [b[1] for b in bars]
    lo = [b[2] for b in bars]
    cl, zl, vol, t = z["cl"], z["zl"], z["vol"], z["trd"]
    open_pos, results = [], []

    def close(p, px):
        pips = (px - p["entry"]) / pip * p["dir"]
        results.append((pips - COST_PIPS) / p["sl_pips"])

    for i in range(max(z["start"] + 2, slope_n + 1), n):
        still = []
        for p in open_pos:
            if p["dir"] == 1:
                if lo[i] <= p["sl"]: close(p, p["sl"])
                elif hi[i] >= p["tp"]: close(p, p["tp"])
                else: still.append(p)
            else:
                if hi[i] >= p["sl"]: close(p, p["sl"])
                elif lo[i] <= p["tp"]: close(p, p["tp"])
                else: still.append(p)
        open_pos = still

        flip_up = t[i] == 1 and t[i - 1] <= 0
        flip_dn = t[i] == -1 and t[i - 1] >= 0
        pb_up = t[i] == 1 and t[i - 1] == 1 and cl[i] > zl[i] and cl[i - 1] <= zl[i - 1]
        pb_dn = t[i] == -1 and t[i - 1] == -1 and cl[i] < zl[i] and cl[i - 1] >= zl[i - 1]
        if impulse:
            pb_up = pb_up and z["ok_up"][i]
            pb_dn = pb_dn and z["ok_dn"][i]
        if not use_pullbacks:
            pb_up = pb_dn = False

        if flip_up or flip_dn:
            keep = []
            for p in open_pos:
                if (flip_dn and p["dir"] == 1) or (flip_up and p["dir"] == -1):
                    close(p, cl[i])
                else:
                    keep.append(p)
            open_pos = keep

        for d, sig in ((1, flip_up or pb_up), (-1, flip_dn or pb_dn)):
            if not sig or len(open_pos) >= MAX_POS:
                continue
            if htf is not None and htf[i] != d:
                continue
            if slope_k > 0 and (zl[i] - zl[i - slope_n]) * d < slope_k * vol[i]:
                continue
            entry = cl[i]
            band = zl[i] - vol[i] if d == 1 else zl[i] + vol[i]
            if (d == 1 and band >= entry) or (d == -1 and band <= entry):
                continue
            sl_pips = max(abs(entry - band) / pip, MIN_SL_PIPS)
            open_pos.append({"dir": d, "entry": entry, "sl_pips": sl_pips,
                             "sl": entry - d * sl_pips * pip, "tp": entry + d * sl_pips * pip * RR})
    return results


def stats(rs):
    if not rs:
        return "trades    0"
    w = [r for r in rs if r > 0]
    gl = -sum(r for r in rs if r <= 0)
    pf = sum(w) / gl if gl > 0 else 99
    eq = pk = dd = 0.0
    for r in rs:
        eq += r; pk = max(pk, eq); dd = max(dd, pk - eq)
    return f"trades {len(rs):>4} | win {100*len(w)/len(rs):5.1f}% | PF {pf:4.2f} | net {sum(rs):+7.1f}R | maxDD {dd:5.1f}R"


def main():
    for sym in ["EURUSD=X", "EURJPY=X", "GBPUSD=X"]:
        pip = 0.01 if "JPY" in sym else 0.0001
        name = sym.replace("=X", "")
        m5 = fetch(sym, "5m", "60d")
        m15 = fetch(sym, "15m", "60d")
        h1 = fetch(sym, "1h", "730d")
        h1_recent = [b for b in h1 if b[0] >= m5[0][0] - 400 * 3600]
        z5, z15, z1 = za.zero_lag(m5), za.zero_lag(m15), za.zero_lag(h1)
        f15 = htf_map(m5, m15, 900)
        f60 = htf_map(m5, h1_recent, 3600)
        print(f"\n=== {name} ===")
        rows = [
            ("M5  current rules (impulse)", run(m5, z5, pip)),
            ("M5  + M15 trend filter", run(m5, z5, pip, htf=f15)),
            ("M5  + H1 trend filter", run(m5, z5, pip, htf=f60)),
            ("M5  + flat filter 0.5", run(m5, z5, pip, slope_k=0.5)),
            ("M5  + flat filter 1.0", run(m5, z5, pip, slope_k=1.0)),
            ("M5  + H1 trend + flat 0.5", run(m5, z5, pip, htf=f60, slope_k=0.5)),
            ("M15 current rules (60d)", run(m15, z15, pip)),
            ("M15 flips only (60d)", run(m15, z15, pip, use_pullbacks=False)),
            ("H1  current rules (~2y)", run(h1, z1, pip)),
            ("H1  flips only (~2y)", run(h1, z1, pip, use_pullbacks=False)),
            ("H1  + flat filter 0.5 (~2y)", run(h1, z1, pip, slope_k=0.5)),
        ]
        for label, rs in rows:
            print(f"{label:<30} {stats(rs)}")


if __name__ == "__main__":
    main()
