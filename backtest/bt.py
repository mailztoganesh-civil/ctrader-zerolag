#!/usr/bin/env python3
"""
Backtest of the Zero Lag cBot rules on Yahoo 5m data (last ~60 days).
Compares rule variants. Results in R (1R = the trade's stop-loss risk).

Assumptions (conservative):
- signal on candle close, entry at that close (bot enters on next bar open)
- SL = opposite band, min 5 pips, no max; TP = 2R
- if SL and TP are both touched in one candle -> counted as SL
- close on opposite trend flip at candle close
- max 2 open positions
- cost per trade: 0.9 pip (raw spread ~0.2 + commission ~0.7)
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "alerts"))
import zerolag_alert as za  # noqa: E402

SYMBOL = os.environ.get("BT_SYMBOL", "EURUSD=X")
PIP = 0.01 if "JPY" in SYMBOL else 0.0001
COST_PIPS = 0.9
MIN_SL_PIPS = 5.0
RR = 2.0
MAX_POS = 2


def fetch():
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}?interval=5m&range=60d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (zerolag-backtest)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    res = d["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    bars = []
    for t, h, l, c in zip(res["timestamp"], q["high"], q["low"], q["close"]):
        if None in (h, l, c):
            continue
        bars.append((int(t), float(h), float(l), float(c)))
    return bars[:-1]  # drop forming bar


def run(bars, z, use_pullbacks=True, impulse=False, loss_filter=False, flip_only_after_block=True):
    n = len(bars)
    hi = [b[1] for b in bars]
    lo = [b[2] for b in bars]
    cl, zl, vol, t = z["cl"], z["zl"], z["vol"], z["trd"]
    open_pos = []
    results = []          # R per closed trade
    streak_side, streak, blocked = None, 0, None

    def register(side, r):
        nonlocal streak_side, streak, blocked
        if r < 0:
            if streak_side == side:
                streak += 1
            else:
                streak_side, streak = side, 1
            if streak >= 2:
                blocked = side
        else:
            streak_side, streak = None, 0

    def close(p, price_exit):
        pips = (price_exit - p["entry"]) / PIP * p["dir"]
        r = (pips - COST_PIPS) / p["sl_pips"]
        results.append(r)
        if loss_filter:
            register(p["dir"], r)

    for i in range(z["start"] + 2, n):
        # 1) exits inside candle i
        still = []
        for p in open_pos:
            if p["dir"] == 1:
                if lo[i] <= p["sl"]:
                    close(p, p["sl"])
                elif hi[i] >= p["tp"]:
                    close(p, p["tp"])
                else:
                    still.append(p)
            else:
                if hi[i] >= p["sl"]:
                    close(p, p["sl"])
                elif lo[i] <= p["tp"]:
                    close(p, p["tp"])
                else:
                    still.append(p)
        open_pos = still

        # 2) signals at close of candle i
        flip_up = t[i] == 1 and t[i - 1] <= 0
        flip_dn = t[i] == -1 and t[i - 1] >= 0
        pb_up = t[i] == 1 and t[i - 1] == 1 and cl[i] > zl[i] and cl[i - 1] <= zl[i - 1]
        pb_dn = t[i] == -1 and t[i - 1] == -1 and cl[i] < zl[i] and cl[i - 1] >= zl[i - 1]
        if impulse:
            pb_up = pb_up and z["ok_up"][i]
            pb_dn = pb_dn and z["ok_dn"][i]
        if not use_pullbacks:
            pb_up = pb_dn = False

        # close on opposite flip
        if flip_dn or flip_up:
            keep = []
            for p in open_pos:
                if (flip_dn and p["dir"] == 1) or (flip_up and p["dir"] == -1):
                    close(p, cl[i])
                else:
                    keep.append(p)
            open_pos = keep

        long_sig = flip_up or pb_up
        short_sig = flip_dn or pb_dn
        if loss_filter and blocked is not None:
            if blocked == 1:
                long_sig = False
                if flip_only_after_block and short_sig and not flip_dn:
                    short_sig = False
            else:
                short_sig = False
                if flip_only_after_block and long_sig and not flip_up:
                    long_sig = False

        for d, sig in ((1, long_sig), (-1, short_sig)):
            if not sig or len(open_pos) >= MAX_POS:
                continue
            entry = cl[i]
            band = zl[i] - vol[i] if d == 1 else zl[i] + vol[i]
            if (d == 1 and band >= entry) or (d == -1 and band <= entry):
                continue
            sl_pips = max(abs(entry - band) / PIP, MIN_SL_PIPS)
            sl = entry - d * sl_pips * PIP
            tp = entry + d * sl_pips * PIP * RR
            open_pos.append({"dir": d, "entry": entry, "sl": sl, "tp": tp, "sl_pips": sl_pips})
            if loss_filter and blocked is not None and d != blocked:
                blocked, streak_side, streak = None, None, 0

    return results


def stats(name, rs):
    if not rs:
        return f"{name:<34} no trades"
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gp, gl = sum(wins), -sum(losses)
    pf = gp / gl if gl > 0 else float("inf")
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return (f"{name:<34} trades {len(rs):>4} | win {100*len(wins)/len(rs):5.1f}% | "
            f"PF {pf:4.2f} | net {sum(rs):+7.1f}R | maxDD {dd:5.1f}R")


def main():
    bars = fetch()
    z = za.zero_lag(bars)
    from datetime import datetime, timezone
    d0 = datetime.fromtimestamp(bars[0][0], timezone.utc).strftime("%d %b")
    d1 = datetime.fromtimestamp(bars[-1][0], timezone.utc).strftime("%d %b %Y")
    print(f"=== {SYMBOL} M5  {d0} - {d1}  ({len(bars)} candles) ===")
    variants = [
        ("A  Old rules (flip + pullback)", dict()),
        ("B  + impulse-before-pullback", dict(impulse=True)),
        ("C  + 2-loss side block", dict(loss_filter=True)),
        ("D  NEW: impulse + 2-loss block", dict(impulse=True, loss_filter=True)),
        ("E  Trend flips only", dict(use_pullbacks=False)),
        ("F  Flips only + 2-loss block", dict(use_pullbacks=False, loss_filter=True)),
    ]
    for name, kw in variants:
        print(stats(name, run(bars, z, **kw)))


if __name__ == "__main__":
    main()
