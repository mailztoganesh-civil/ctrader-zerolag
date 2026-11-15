// BtcTrendBot - Zero Lag trend bot for BTCUSD on M30 (cTrader cBot)
//
// Rules (from the BTC research in mailztoganesh-civil/forex-data, results/btc/research2):
//   Entry : M30 Zero Lag trend flips (close crosses the upper / lower band), entry at the next
//           candle's open, ONLY if the H1 Zero Lag trend points the same way.
//           No new entries on Saturday / Sunday (UTC). One position at a time.
//   Stop  : starts at the opposite band, then trails: best price since entry -/+ 5 x ATR(14),
//           moved on every M30 close, only ever tightened. No take-profit.
//   Exit  : trailing / initial stop, or the opposite M30 trend flip.
//   Loss filter: 2 losses in a row on one side block that side until an opposite trade opens.
// Backtest 2018-2026 (Binance BTCUSDT M5 -> M30, cost 0.06% per trade):
//   673 trades (~1.5 a week), 44% winners, +132R, profit factor 1.55, Sharpe 1.27,
//   ~15% a year at 1% risk, max drawdown -13%, every calendar year positive.
//   Worst losing streak 15 trades. Most profit comes from a few big trend trades: never skip.
// Small accounts: 0.01 lot BTC with a ~2% stop risks ~$12-16. "Max Risk % At Min Lot" decides
// whether the bot may still trade the minimum lot when Risk % alone would be too small.

using System;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    public enum BtcSizeMode { RiskPercent, FixedLots }

    [Robot(AccessRights = AccessRights.None, TimeZone = TimeZones.UTC, AddIndicators = false)]
    public class BtcTrendBot : Robot
    {
        // ---------------- Risk ----------------
        [Parameter("Size Mode", Group = "Risk", DefaultValue = BtcSizeMode.RiskPercent)]
        public BtcSizeMode Sizing { get; set; }

        [Parameter("Risk % per Trade", Group = "Risk", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 10)]
        public double RiskPct { get; set; }

        [Parameter("Fixed Lots", Group = "Risk", DefaultValue = 0.01, MinValue = 0.01)]
        public double FixedLots { get; set; }

        [Parameter("Max Risk % At Min Lot (0 = never use min lot)", Group = "Risk", DefaultValue = 0, MinValue = 0, MaxValue = 50)]
        public double MaxRiskAtMinLot { get; set; }

        [Parameter("Stop Bot If Equity Below (0 = off)", Group = "Risk", DefaultValue = 0, MinValue = 0)]
        public double EquityStop { get; set; }

        // ---------------- Strategy (tested values) ----------------
        [Parameter("Zero Lag Length", Group = "Strategy", DefaultValue = 70, MinValue = 10)]
        public int Length { get; set; }

        [Parameter("Band Multiplier", Group = "Strategy", DefaultValue = 1.2, MinValue = 0.1)]
        public double Mult { get; set; }

        [Parameter("Use H1 Trend Filter", Group = "Strategy", DefaultValue = true)]
        public bool UseH1 { get; set; }

        [Parameter("Trail Distance (x ATR14)", Group = "Strategy", DefaultValue = 5.0, MinValue = 0.5)]
        public double TrailMult { get; set; }

        [Parameter("Skip Weekend Entries", Group = "Strategy", DefaultValue = true)]
        public bool SkipWeekend { get; set; }

        [Parameter("Losses In A Row To Block Side (0 = off)", Group = "Strategy", DefaultValue = 2, MinValue = 0)]
        public int LossesToBlock { get; set; }

        [Parameter("History Bars", Group = "Strategy", DefaultValue = 1500, MinValue = 600)]
        public int CalcBars { get; set; }

        [Parameter("Label", Group = "Misc", DefaultValue = "BtcTrend")]
        public string Label { get; set; }

        private Bars _h1;
        private AverageTrueRange _atr;
        private TradeType? _streakSide, _blocked;
        private int _streak;
        private bool _halted;

        protected override void OnStart()
        {
            if (TimeFrame != TimeFrame.Minute30)
                Print("WARNING: BtcTrendBot was tested on M30 charts; this chart is {0}", TimeFrame);
            if (!SymbolName.ToUpper().Contains("BTC"))
                Print("WARNING: BtcTrendBot was tested on BTCUSD; this symbol is {0}", SymbolName);

            Load(Bars);
            if (UseH1)
            {
                _h1 = MarketData.GetBars(TimeFrame.Hour);
                Load(_h1);
            }
            _atr = Indicators.AverageTrueRange(14, MovingAverageType.Simple);

            RebuildLossState();
            Positions.Closed += OnClosed;

            Print("BtcTrendBot started on {0} {1}: {2}, H1 filter {3}, trail {4}x ATR, weekends {5}, equity {6:F2}. {7}",
                  SymbolName, TimeFrame,
                  Sizing == BtcSizeMode.RiskPercent ? string.Format("risk {0}%", RiskPct) : string.Format("{0} lots", FixedLots),
                  UseH1 ? "on" : "off", TrailMult, SkipWeekend ? "skipped" : "traded", Account.Equity, LossText());
        }

        private void Load(Bars b)
        {
            for (int k = 0; k < 3 && b.Count < CalcBars + 2; k++)
                if (b.LoadMoreHistory() <= 0) break;
        }

        // ---------------- Zero Lag trend on CLOSED bars (same maths as the Pine script) ----------------
        // Returns arrays oldest-first, last element = last closed bar; null if not enough data.
        private bool Calc(Bars b, out double[] zl, out double[] vol, out int[] trd)
        {
            zl = vol = null; trd = null;
            int closed = b.Count - 1, n = Math.Min(CalcBars, closed), L = Length;
            int lag = (L - 1) / 2, w = L * 3, emaStart = lag + L - 1, volStart = (L - 1) + (w - 1);
            int start = Math.Max(emaStart, volStart) + 1;
            if (n < start + 50) return false;
            int off = closed - n;
            var c = new double[n]; var h = new double[n]; var l = new double[n];
            for (int i = 0; i < n; i++) { c[i] = b.ClosePrices[off + i]; h[i] = b.HighPrices[off + i]; l[i] = b.LowPrices[off + i]; }

            zl = new double[n]; vol = new double[n]; trd = new int[n];
            var zs = new double[n]; var atr = new double[n];
            for (int i = lag; i < n; i++) zs[i] = c[i] + (c[i] - c[i - lag]);
            double a = 2.0 / (L + 1.0), sum = 0;
            for (int i = lag; i <= emaStart; i++) sum += zs[i];
            zl[emaStart] = sum / L;
            for (int i = emaStart + 1; i < n; i++) zl[i] = a * zs[i] + (1 - a) * zl[i - 1];

            sum = 0;
            for (int i = 0; i < L; i++)
                sum += i == 0 ? h[0] - l[0] : Math.Max(h[i] - l[i], Math.Max(Math.Abs(h[i] - c[i - 1]), Math.Abs(l[i] - c[i - 1])));
            atr[L - 1] = sum / L;
            for (int i = L; i < n; i++)
            {
                double tr = Math.Max(h[i] - l[i], Math.Max(Math.Abs(h[i] - c[i - 1]), Math.Abs(l[i] - c[i - 1])));
                atr[i] = (atr[i - 1] * (L - 1) + tr) / L;
            }
            for (int i = volStart; i < n; i++)
            {
                double mx = 0;
                for (int k = i - w + 1; k <= i; k++) if (atr[k] > mx) mx = atr[k];
                vol[i] = mx * Mult;
            }
            int t = 0;
            for (int i = start; i < n; i++)
            {
                if (c[i] > zl[i] + vol[i] && c[i - 1] <= zl[i - 1] + vol[i - 1]) t = 1;
                if (c[i] < zl[i] - vol[i] && c[i - 1] >= zl[i - 1] - vol[i - 1]) t = -1;
                trd[i] = t;
            }
            return true;
        }

        protected override void OnBar()
        {
            if (_halted) return;
            if (EquityStop > 0 && Account.Equity < EquityStop)
            {
                foreach (var p in Positions.FindAll(Label, SymbolName)) ClosePosition(p);
                Print("Equity {0:F2} below {1:F2}: all closed, bot stopped.", Account.Equity, EquityStop);
                _halted = true;
                Stop();
                return;
            }
            if (!Calc(Bars, out var z, out var v, out var t)) { Print("Not enough history yet"); return; }

            int i = t.Length - 1, j = i - 1;
            bool flipUp = t[i] == 1 && t[j] <= 0;
            bool flipDn = t[i] == -1 && t[j] >= 0;

            // 1) trail the stop of any open trade (uses the candle that just closed)
            TrailStops();

            // 2) close on the opposite flip
            if (flipDn) CloseSide(TradeType.Buy, "bearish M30 flip");
            if (flipUp) CloseSide(TradeType.Sell, "bullish M30 flip");
            if (!flipUp && !flipDn) return;

            int dir = flipUp ? 1 : -1;
            var side = flipUp ? TradeType.Buy : TradeType.Sell;
            int h1 = 0;
            if (UseH1)
            {
                if (!Calc(_h1, out _, out _, out var th)) { Print("H1 history not ready, signal skipped"); return; }
                h1 = th[th.Length - 1];
            }
            Print("M30 trend flipped {0} | H1 trend {1}", flipUp ? "BULLISH" : "BEARISH",
                  !UseH1 ? "off" : h1 == 1 ? "bullish" : h1 == -1 ? "bearish" : "none");

            // 3) entry filters
            if (Positions.FindAll(Label, SymbolName).Length > 0) { Print("Position already open, no new entry"); return; }
            if (UseH1 && h1 != dir) { Print("Skipped: H1 trend does not agree"); return; }
            if (SkipWeekend && (Server.Time.DayOfWeek == DayOfWeek.Saturday || Server.Time.DayOfWeek == DayOfWeek.Sunday))
            { Print("Skipped: weekend"); return; }
            if (_blocked.HasValue && _blocked.Value == side) { Print("Skipped: {0}", LossText()); return; }

            // 4) stop at the opposite band, size, enter
            double entry = flipUp ? Symbol.Ask : Symbol.Bid;
            double sl = flipUp ? z[i] - v[i] : z[i] + v[i];
            if (flipUp ? sl >= entry : sl <= entry) { Print("Skipped: band stop on the wrong side of price"); return; }
            double slPips = Math.Abs(entry - sl) / Symbol.PipSize;
            double units = Units(slPips, out string sizeNote);
            if (units <= 0) { Print("Skipped: {0}", sizeNote); return; }

            var r = ExecuteMarketOrder(side, SymbolName, units, Label, Math.Round(slPips, 1), null, "BTC trend " + side);
            if (!r.IsSuccessful) { Print("Order failed: {0}", r.Error); return; }
            Print("{0} {1} lots at {2}, stop {3} ({4:F2}% away), risk {5:F2} {6}. {7}",
                  side, Symbol.VolumeInUnitsToQuantity(units), r.Position.EntryPrice, r.Position.StopLoss,
                  Math.Abs(entry - sl) / entry * 100, slPips * Symbol.PipValue * units, Account.Asset.Name, sizeNote);
            if (_blocked.HasValue && _blocked.Value != side)
            {
                Print("Opposite trade opened -> {0} side unblocked", _blocked.Value);
                _blocked = null; _streak = 0; _streakSide = null;
            }
        }

        private double Units(double slPips, out string note)
        {
            double perUnit = slPips * Symbol.PipValue;
            note = "";
            if (perUnit <= 0) { note = "bad stop distance"; return 0; }
            double units = Sizing == BtcSizeMode.FixedLots
                ? Symbol.QuantityToVolumeInUnits(FixedLots)
                : Account.Equity * RiskPct / 100.0 / perUnit;
            units = Symbol.NormalizeVolumeInUnits(units, RoundingMode.Down);
            if (units < Symbol.VolumeInUnitsMin)
            {
                double minPct = perUnit * Symbol.VolumeInUnitsMin / Account.Equity * 100;
                if (Sizing == BtcSizeMode.RiskPercent && (MaxRiskAtMinLot <= 0 || minPct > MaxRiskAtMinLot))
                {
                    note = string.Format("min lot would risk {0:F1}% of equity (Risk % {1}, Max Risk % At Min Lot {2})",
                                         minPct, RiskPct, MaxRiskAtMinLot);
                    return 0;
                }
                units = Symbol.VolumeInUnitsMin;
                note = string.Format("using min lot, real risk {0:F1}%", minPct);
            }
            return Math.Min(units, Symbol.VolumeInUnitsMax);
        }

        // ---------------- ATR trailing stop ----------------
        private void TrailStops()
        {
            double a = _atr.Result.Last(1);
            if (double.IsNaN(a) || a <= 0) return;
            int last = Bars.Count - 2;
            foreach (var p in Positions.FindAll(Label, SymbolName))
            {
                int first = Bars.OpenTimes.GetIndexByTime(p.EntryTime);
                if (first < 0 || first > last) continue;
                bool buy = p.TradeType == TradeType.Buy;
                double best = buy ? double.MinValue : double.MaxValue;
                for (int k = first; k <= last; k++)
                    best = buy ? Math.Max(best, Bars.HighPrices[k]) : Math.Min(best, Bars.LowPrices[k]);
                double sl = Math.Round(buy ? best - TrailMult * a : best + TrailMult * a, Symbol.Digits);
                if (p.StopLoss.HasValue && (buy ? sl <= p.StopLoss.Value : sl >= p.StopLoss.Value)) continue;
                if (buy ? sl >= Symbol.Bid : sl <= Symbol.Ask)
                {
                    var c = ClosePosition(p);
                    Print("Trailing stop {0} already passed, closed {1}: {2}", sl, p.TradeType, c.IsSuccessful ? "ok" : c.Error.ToString());
                    continue;
                }
                var m = p.ModifyStopLossPrice(sl);
                if (m.IsSuccessful) Print("Trail {0}: stop -> {1} (best {2}, ATR {3:F1})", p.TradeType, sl, best, a);
                else Print("Trail modify failed: {0}", m.Error);
            }
        }

        private void CloseSide(TradeType type, string why)
        {
            foreach (var p in Positions.FindAll(Label, SymbolName, type))
            {
                var r = ClosePosition(p);
                Print(r.IsSuccessful ? "Closed {0} ({1}), net {2:F2}" : "Close failed {0} ({1})", type, why, p.NetProfit);
            }
        }

        // ---------------- loss filter ----------------
        private void Register(TradeType side, double net)
        {
            if (LossesToBlock <= 0) return;
            if (net < 0)
            {
                if (_streakSide == side) _streak++; else { _streakSide = side; _streak = 1; }
                if (_streak >= LossesToBlock) _blocked = side;
            }
            else { _streak = 0; _streakSide = null; }
        }

        private void RebuildLossState()
        {
            _streakSide = null; _streak = 0; _blocked = null;
            foreach (var t in History.Where(x => x.Label == Label && x.SymbolName == SymbolName).OrderBy(x => x.ClosingTime))
            {
                if (_blocked.HasValue && t.TradeType != _blocked.Value) _blocked = null;
                Register(t.TradeType, t.NetProfit);
            }
        }

        private void OnClosed(PositionClosedEventArgs e)
        {
            var p = e.Position;
            if (p.Label != Label || p.SymbolName != SymbolName) return;
            Register(p.TradeType, p.NetProfit);
            Print("Trade closed {0} ({1}), net {2:F2}. {3}", p.TradeType, e.Reason, p.NetProfit, LossText());
        }

        private string LossText()
        {
            if (_blocked.HasValue)
                return string.Format("{0} side blocked after {1} losses in a row, waiting for a {2} trade",
                                     _blocked.Value, LossesToBlock, _blocked.Value == TradeType.Buy ? "SELL" : "BUY");
            return _streak > 0 ? string.Format("{0} losing streak {1}/{2}", _streakSide, _streak, LossesToBlock) : "no losing streak";
        }
    }
}
