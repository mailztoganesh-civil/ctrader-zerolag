#!/usr/bin/env python3
"""
Zero Lag Trend Signals -> WhatsApp alerts (via CallMeBot).

Same math as ZeroLagTrendBot (port of "Zero Lag Trend Signals (MTF) [AlgoAlpha]",
Mozilla Public License 2.0). Runs from GitHub Actions every 5 minutes, checks
every CLOSED bar since the last run, and sends one WhatsApp message with any
new signals.

Env:
  CALLMEBOT_PHONE   phone with country code, e.g. 9715XXXXXXXX (no +)
  CALLMEBOT_APIKEY  key received from CallMeBot
  STATE_FILE        path of JSON state (default alerts/state.json)
  DRY_RUN=1         print instead of sending
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ---------------- settings ----------------
PAIRS = {            # display name -> Yahoo symbol
    "EURUSD": "EURUSD=X",
    "EURJPY": "EURJPY=X",
}
INTERVAL = "5m"      # Yahoo interval
BAR_SECONDS = 300
LENGTH = 70          # Zero-Lag EMA look-back (Pine default)
MULT = 1.2           # band multiplier (Pine default)
ALERT_FLIPS = True       # big arrows: trend flip
ALERT_PULLBACKS = True   # small arrows: pullback entry in trend
DUBAI = timezone(timedelta(hours=4))

STATE_FILE = os.environ.get("STATE_FILE", "alerts/state.json")
DRY_RUN = os.environ.get("DRY_RUN") == "1"


# ---------------- data ----------------
def fetch_bars(symbol):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol)
           + "?interval=" + INTERVAL + "&range=5d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (zerolag-alerts)"})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            res = data["chart"]["result"][0]
            ts = res["timestamp"]
            q = res["indicators"]["quote"][0]
            bars = []
            for t, h, l, c in zip(ts, q["high"], q["low"], q["close"]):
                if None in (h, l, c):
                    continue
                bars.append((int(t), float(h), float(l), float(c)))
            # keep closed bars only
            now = time.time()
            bars = [b for b in bars if b[0] + BAR_SECONDS <= now]
            return bars
        except Exception as e:  # network hiccup -> retry
            last_err = e
            time.sleep(3)
    raise RuntimeError(f"Yahoo fetch failed for {symbol}: {last_err}")


# ---------------- Zero Lag math (same as cBot / EA) ----------------
def zero_lag(bars):
    n = len(bars)
    L = LENGTH
    lag = (L - 1) // 2
    w = L * 3
    ema_start = lag + L - 1
    vol_start = (L - 1) + (w - 1)
    start = max(ema_start, vol_start) + 1
    if n < start + 50:
        return None

    hi = [b[1] for b in bars]
    lo = [b[2] for b in bars]
    cl = [b[3] for b in bars]
    zs = [0.0] * n
    zl = [0.0] * n
    tr = [0.0] * n
    atr = [0.0] * n
    vol = [0.0] * n
    trd = [0] * n

    for i in range(lag, n):
        zs[i] = cl[i] + (cl[i] - cl[i - lag])

    alpha = 2.0 / (L + 1.0)
    zl[ema_start] = sum(zs[lag:ema_start + 1]) / L
    for i in range(ema_start + 1, n):
        zl[i] = alpha * zs[i] + (1 - alpha) * zl[i - 1]

    tr[0] = hi[0] - lo[0]
    for i in range(1, n):
        tr[i] = max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
    atr[L - 1] = sum(tr[:L]) / L
    for i in range(L, n):
        atr[i] = (atr[i - 1] * (L - 1) + tr[i]) / L

    for i in range(vol_start, n):
        vol[i] = max(atr[i - w + 1:i + 1]) * MULT

    t = 0
    for i in range(start, n):
        if cl[i] > zl[i] + vol[i] and cl[i - 1] <= zl[i - 1] + vol[i - 1]:
            t = 1
        if cl[i] < zl[i] - vol[i] and cl[i - 1] >= zl[i - 1] - vol[i - 1]:
            t = -1
        trd[i] = t

    return {"cl": cl, "zl": zl, "vol": vol, "trd": trd, "start": start}


def signals_at(z, i):
    """Signals on closed bar i (needs i-1)."""
    t, cl, zl = z["trd"], z["cl"], z["zl"]
    out = []
    if i - 1 < z["start"]:
        return out
    if ALERT_FLIPS:
        if t[i] == 1 and t[i - 1] <= 0:
            out.append(("BUY", "Bullish trend flip"))
        if t[i] == -1 and t[i - 1] >= 0:
            out.append(("SELL", "Bearish trend flip"))
    if ALERT_PULLBACKS:
        if t[i] == 1 and t[i - 1] == 1 and cl[i] > zl[i] and cl[i - 1] <= zl[i - 1]:
            out.append(("BUY", "Bullish pullback entry"))
        if t[i] == -1 and t[i - 1] == -1 and cl[i] < zl[i] and cl[i - 1] >= zl[i - 1]:
            out.append(("SELL", "Bearish pullback entry"))
    return out


# ---------------- messaging ----------------
def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return None
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request("https://api.telegram.org/bot" + token + "/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("Telegram:", r.read().decode("utf-8", "replace")[:120])
        return True
    except Exception as e:
        print("Telegram send failed:", e)
        return False


def send_whatsapp(text):
    if DRY_RUN:
        print("---- Message (dry run) ----\n" + text)
        return True
    tg = send_telegram(text)
    if tg is not None:
        return tg
    phone = os.environ.get("CALLMEBOT_PHONE", "").strip().lstrip("+")
    key = os.environ.get("CALLMEBOT_APIKEY", "").strip()
    if not phone or not key:
        print("CALLMEBOT_PHONE / CALLMEBOT_APIKEY not set; message not sent:\n" + text)
        return False
    url = ("https://api.callmebot.com/whatsapp.php?phone=" + urllib.parse.quote(phone)
           + "&text=" + urllib.parse.quote(text) + "&apikey=" + urllib.parse.quote(key))
    req = urllib.request.Request(url, headers={"User-Agent": "zerolag-alerts"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", "replace")
    print("CallMeBot response:", body[:200].replace("\n", " "))
    return True


def fmt_price(name, p):
    return f"{p:.3f}" if name.endswith("JPY") else f"{p:.5f}"


def fmt_time(ts):
    return datetime.fromtimestamp(ts + BAR_SECONDS, DUBAI).strftime("%a %H:%M")


# ---------------- main ----------------
def main():
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
        except Exception:
            state = {}

    lines = []
    first_run_lines = []
    for name, sym in PAIRS.items():
        try:
            bars = fetch_bars(sym)
        except Exception as e:
            print(e)
            continue
        z = zero_lag(bars)
        if z is None:
            print(f"{name}: not enough bars ({len(bars)})")
            continue

        last_i = len(bars) - 1
        last_ts = bars[last_i][0]
        prev_ts = state.get(name)

        if not state.get("_started"):
            trend = "Bullish" if z["trd"][last_i] == 1 else "Bearish"
            first_run_lines.append(f"{name} M5: {trend} @ {fmt_price(name, z['cl'][last_i])}")
        if prev_ts is not None:
            for i in range(len(bars)):
                if bars[i][0] <= prev_ts:
                    continue
                for side, why in signals_at(z, i):
                    emoji = "🟢" if side == "BUY" else "🔴"
                    band = z["zl"][i] - z["vol"][i] if side == "BUY" else z["zl"][i] + z["vol"][i]
                    lines.append(
                        f"{emoji} {name} M5 {side} - {why}\n"
                        f"Close {fmt_price(name, z['cl'][i])} | Band SL {fmt_price(name, band)}\n"
                        f"Candle closed {fmt_time(bars[i][0])} (Dubai)"
                    )
        state[name] = last_ts
        print(f"{name}: {len(bars)} bars, last closed {fmt_time(last_ts)} Dubai, trend {z['trd'][last_i]}")

    if first_run_lines:
        if send_whatsapp("✅ Zero Lag alerts started\n" + "\n".join(first_run_lines)):
            state["_started"] = True
    if lines:
        send_whatsapp("⚡ Zero Lag signals\n\n" + "\n\n".join(lines))
    if not lines and not first_run_lines:
        print("No new signals.")

    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


if __name__ == "__main__":
    sys.exit(main())
