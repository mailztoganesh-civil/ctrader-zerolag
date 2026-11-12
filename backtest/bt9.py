#!/usr/bin/env python3
"""
New strategy families on ~10 years of DAILY data (Yahoo).
 A  Time-series momentum: each pair long/short by sign of L-day return (L=63/126/252),
    and EMA50/200 trend. Equal weight across 10 pairs, daily.
 B  Cross-sectional currency momentum: 9 currencies vs USD, every Friday long top-3 /
    short bottom-3 by 63-day return, hold one week.
 C  Month-end rebalancing: last trading day of month, if SPY beat EFA month-to-date ->
    sell USD (buy EURUSD) for that day, else buy USD. Close-to-close.
 D  Volatility breakout (Larry Williams): buy if price reaches open + k x yesterday range,
    sell if open - k x range; exit at close. k = 0.5 / 0.7.
Costs: 1 bp per unit of turnover (each side) for A-C; 2 bp round trip for D.
Swaps / rollover NOT included (matters for A/B which hold positions overnight).
Reports annual return (sum of daily %), Sharpe, max drawdown and yearly returns.
"""
import json
import math
import time
import urllib.request
from datetime import datetime, timezone

PAIRS = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X",
         "USDCHF=X", "NZDUSD=X", "EURJPY=X", "GBPJPY=X", "EURGBP=X"]
CCY = {"EUR": ("EURUSD=X", 1), "GBP": ("GBPUSD=X", 1), "AUD": ("AUDUSD=X", 1), "NZD": ("NZDUSD=X", 1),
       "JPY": ("USDJPY=X", -1), "CAD": ("USDCAD=X", -1), "CHF": ("USDCHF=X", -1),
       "NOK": ("USDNOK=X", -1), "SEK": ("USDSEK=X", -1)}
COST = 0.0001


def daily(sym, rng="10y"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range={rng}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (backtest)"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)["chart"]["result"][0]
            q = d["indicators"]["quote"][0]
            out = {}
            for t, o, h, l, c in zip(d["timestamp"], q["open"], q["high"], q["low"], q["close"]):
                if None in (o, h, l, c) or c <= 0:
                    continue
                out[datetime.fromtimestamp(t, timezone.utc).date()] = (o, h, l, c)
            return out
        except Exception as e:
            err = e; time.sleep(3)
    raise RuntimeError(f"{sym}: {err}")


def summary(name, daily_pnl):
    """daily_pnl: list of (date, return). Returns line with ann. return, Sharpe, maxDD, by year."""
    if not daily_pnl:
        return f"{name:<38} no data"
    r = [x for _, x in daily_pnl]
    n = len(r); mu = sum(r) / n; sd = (sum((x - mu) ** 2 for x in r) / n) ** 0.5
    ann = mu * 252; sh = (mu / sd) * math.sqrt(252) if sd > 0 else 0
    eq = pk = dd = 0.0
    for x in r:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    yrs = {}
    for d, x in daily_pnl:
        yrs[d.year] = yrs.get(d.year, 0) + x
    ys = " ".join(f"{y % 100:02d}:{100*v:+.0f}" for y, v in sorted(yrs.items()))
    pos_years = sum(1 for v in yrs.values() if v > 0)
    return (f"{name:<38} ann {100*ann:+5.1f}% | Sharpe {sh:+.2f} | maxDD {100*dd:5.1f}% | "
            f"+yrs {pos_years}/{len(yrs)} | {ys}")


def strat_A(data, lookback=None, ema_mode=False):
    per_day = {}
    for sym in PAIRS:
        px = data[sym]; days = sorted(px); c = [px[d][3] for d in days]
        if ema_mode:
            def ema(v, n):
                a = 2 / (n + 1); o = [v[0]]
                for x in v[1:]: o.append(a * x + (1 - a) * o[-1])
                return o
            e50, e200 = ema(c, 50), ema(c, 200)
        pos_prev = 0
        for i in range(260, len(days)):
            if ema_mode:
                pos = 1 if e50[i - 1] > e200[i - 1] else -1
            else:
                pos = 1 if c[i - 1] > c[i - 1 - lookback] else -1
            ret = pos * (c[i] / c[i - 1] - 1) - COST * abs(pos - pos_prev)
            pos_prev = pos
            per_day.setdefault(days[i], []).append(ret)
    return [(d, sum(v) / len(v)) for d, v in sorted(per_day.items()) if len(v) >= 5]


def strat_B(data, lookback=63, k=3):
    # currency index level vs USD
    lv = {}
    for ccy, (sym, sgn) in CCY.items():
        px = data.get(sym)
        if not px: continue
        lv[ccy] = {d: (px[d][3] if sgn == 1 else 1 / px[d][3]) for d in px}
    days = sorted(set.intersection(*[set(v) for v in lv.values()]))
    out, hold, prev = [], {}, {}
    for i in range(lookback + 1, len(days)):
        d, dp = days[i], days[i - 1]
        r = sum(hold.get(c, 0) * (lv[c][d] / lv[c][dp] - 1) for c in lv)
        if dp.weekday() == 4 or not hold:   # rebalance after Friday close
            mom = {c: lv[c][dp] / lv[c][days[i - 1 - lookback]] - 1 for c in lv}
            rank = sorted(mom, key=mom.get)
            new = {c: 0.0 for c in lv}
            for c in rank[-k:]: new[c] = 1 / (2 * k)
            for c in rank[:k]: new[c] = -1 / (2 * k)
            turn = sum(abs(new[c] - hold.get(c, 0)) for c in lv)
            r -= COST * turn
            hold = new
        out.append((d, r))
    return out


def strat_C(data, spy, efa):
    px = data["EURUSD=X"]; days = sorted(set(px) & set(spy) & set(efa))
    out = []
    for i in range(1, len(days) - 1):
        d, nxt = days[i], days[i + 1]
        if nxt.month == d.month:
            continue                       # d is last trading day of the month
        prev_idx = [k for k in range(i) if days[k].month != d.month]
        if not prev_idx:
            continue
        start = max(prev_idx)  # last day of prev month
        s0, s1 = days[start], days[i - 1]
        rel = (spy[s1][3] / spy[s0][3]) - (efa[s1][3] / efa[s0][3])
        pos = 1 if rel > 0 else -1          # US outperformed -> sell USD -> buy EURUSD
        r = pos * (px[d][3] / px[days[i - 1]][3] - 1) - 2 * COST
        out.append((d, r))
    return out


def strat_D(data, k):
    per_day = {}
    for sym in PAIRS:
        px = data[sym]; days = sorted(px)
        for i in range(1, len(days)):
            o, h, l, c = px[days[i]]
            ph, pl = px[days[i - 1]][1], px[days[i - 1]][2]
            rg = ph - pl
            if rg <= 0: continue
            up, dn = o + k * rg, o - k * rg
            r = 0.0
            if h >= up and l <= dn:
                r = -k * rg / o - 2 * COST     # both touched: assume worst (loss)
            elif h >= up:
                r = (c - up) / up - 2 * COST
            elif l <= dn:
                r = (dn - c) / dn - 2 * COST
            else:
                continue
            per_day.setdefault(days[i], []).append(r)
    return [(d, sum(v) / len(PAIRS)) for d, v in sorted(per_day.items())]


def main():
    data = {s: daily(s) for s in PAIRS + ["USDNOK=X", "USDSEK=X"]}
    spy, efa = daily("SPY"), daily("EFA")
    first = min(min(v) for v in data.values()); last = max(max(v) for v in data.values())
    print(f"Daily data {first} -> {last}, {len(PAIRS)} pairs (+NOK, SEK for B)")
    print("\n--- A. Time-series momentum (10 pairs, equal weight) ---")
    for L in (63, 126, 252):
        print(summary(f"A  {L}-day trend", strat_A(data, L)))
    print(summary("A  EMA 50/200 trend", strat_A(data, ema_mode=True)))
    print("\n--- B. Cross-sectional currency momentum (weekly) ---")
    for L in (21, 63, 126):
        print(summary(f"B  top3/bottom3 by {L}-day return", strat_B(data, L)))
    print("\n--- C. Month-end rebalancing (EURUSD, last day of month) ---")
    c = strat_C(data, spy, efa)
    wins = sum(1 for _, r in c if r > 0)
    print(summary(f"C  month-end flow ({len(c)} months, {wins} wins)", c))
    print("\n--- D. Volatility breakout (10 pairs, exit at close) ---")
    for k in (0.5, 0.7):
        print(summary(f"D  k={k}", strat_D(data, k)))


if __name__ == "__main__":
    main()
