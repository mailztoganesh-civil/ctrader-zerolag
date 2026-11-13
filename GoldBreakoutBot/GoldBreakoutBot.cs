// GoldBreakoutBot - Asian range breakout for XAUUSD only (cTrader cBot)
// Range: 01:00-07:00 UTC (05:00-11:00 Dubai). Entry: first M5 close beyond the
// range between 07:00 and 16:00 UTC (11:00-20:00 Dubai). One trade per day.
// SL at range middle, TP = 2 x risk. Everything closed at 20:00 UTC (00:00 Dubai).
// 2-year test (Dukascopy, Nov 2024 - Nov 2026): +51.8R, 7 of 9 quarters positive.
// Gold stops are large (often $15-25 per oz), so 0.01 lot can be far more than
// your Risk %. "Max Risk % At Min Lot" protects small accounts.
// V2 filters (default ON): breakout candle must close beyond range +/- 0.10 x ATR(14),
// body >= 60% of candle, close in outer 25%, and last completed H1 close on the
// trade side of H1 EMA50. A candle that fails the filters is ignored and the bot
// keeps watching for the next qualified breakout. 2-year test: PF 1.34, +61.2R.

using System;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    public enum GoldStopPlacement { RangeMiddle, OppositeSide }

    [Robot(AccessRights = AccessRights.None, TimeZone = TimeZones.UTC, AddIndicators = false)]
    public class GoldBreakoutBot : Robot
    {
        // ---------------- Risk ----------------
        [Parameter("Risk % per Trade", Group = "Risk", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 10)]
        public double RiskPct { get; set; }

        [Parameter("Stop Placement", Group = "Risk", DefaultValue = GoldStopPlacement.RangeMiddle)]
        public GoldStopPlacement StopMode { get; set; }

        [Parameter("Reward:Risk (TP)", Group = "Risk", DefaultValue = 2.0, MinValue = 0.5)]
        public double RR { get; set; }

        [Parameter("Use Min Lot If Risk Too Small", Group = "Risk", DefaultValue = true)]
        public bool UseMinLot { get; set; }

        // ---------------- Session (UTC) ----------------
        [Parameter("Range Start Hour UTC", Group = "Session (UTC)", DefaultValue = 1, MinValue = 0, MaxValue = 23)]
        public int RangeStartHour { get; set; }

        [Parameter("Range End Hour UTC", Group = "Session (UTC)", DefaultValue = 7, MinValue = 1, MaxValue = 23)]
        public int RangeEndHour { get; set; }

        [Parameter("Last Entry Hour UTC", Group = "Session (UTC)", DefaultValue = 16, MinValue = 1, MaxValue = 23)]
        public int EntryEndHour { get; set; }

        [Parameter("Close All Hour UTC", Group = "Session (UTC)", DefaultValue = 20, MinValue = 1, MaxValue = 23)]
        public int ExitHour { get; set; }

        // ---------------- Filters ----------------
        [Parameter("Min Range in $", Group = "Filters", DefaultValue = 0, MinValue = 0)]
        public double MinRangePips { get; set; }

        [Parameter("Max Range in $ (0 = off)", Group = "Filters", DefaultValue = 0, MinValue = 0)]
        public double MaxRangePips { get; set; }

        [Parameter("Max Spread in $ (0 = off)", Group = "Filters", DefaultValue = 0.6, MinValue = 0)]
        public double MaxSpreadPips { get; set; }

        // ---------------- Selective Reverse ----------------
        [Parameter("Enable Selective Reverse", Group = "Selective Reverse", DefaultValue = false)]
        public bool RevEnabled { get; set; }

        [Parameter("Max Minutes Entry -> Stop", Group = "Selective Reverse", DefaultValue = 45, MinValue = 5)]
        public int RevMaxMinutes { get; set; }

        [Parameter("Original Entry Before (UTC hour)", Group = "Selective Reverse", DefaultValue = 9.0, MinValue = 0, MaxValue = 24)]
        public double RevEntryBefore { get; set; }

        [Parameter("No Reverse If Stop After (UTC hour)", Group = "Selective Reverse", DefaultValue = 10.5, MinValue = 0, MaxValue = 24)]
        public double RevStopCutoff { get; set; }

        [Parameter("Need Clean Failure (close past middle)", Group = "Selective Reverse", DefaultValue = true)]
        public bool RevClean { get; set; }

        [Parameter("Reverse Stop Beyond Entry (pips)", Group = "Selective Reverse", DefaultValue = 2.5, MinValue = 0)]
        public double RevStopExtra { get; set; }

        [Parameter("Reverse Target (R)", Group = "Selective Reverse", DefaultValue = 1.5, MinValue = 0.5)]
        public double RevRR { get; set; }

        [Parameter("Reverse Risk Factor", Group = "Selective Reverse", DefaultValue = 0.7, MinValue = 0.1, MaxValue = 1.0)]
        public double RevRiskFactor { get; set; }

        [Parameter("Max Risk % At Min Lot (0 = off)", Group = "Risk", DefaultValue = 5, MinValue = 0)]
        public double MaxRiskAtMinLot { get; set; }

        [Parameter("Stop Bot If Equity Below (0 = off)", Group = "Risk", DefaultValue = 0, MinValue = 0)]
        public double EquityStop { get; set; }

        // ---------------- V2 filters ----------------
        [Parameter("Use V2 Filters", Group = "V2 Filters", DefaultValue = true)]
        public bool UseV2 { get; set; }

        [Parameter("ATR Buffer (x ATR14)", Group = "V2 Filters", DefaultValue = 0.10, MinValue = 0)]
        public double AtrBuffer { get; set; }

        [Parameter("Min Body % of Candle", Group = "V2 Filters", DefaultValue = 60, MinValue = 0, MaxValue = 100)]
        public double MinBodyPct { get; set; }

        [Parameter("Close In Outer % of Candle", Group = "V2 Filters", DefaultValue = 25, MinValue = 1, MaxValue = 100)]
        public double CloseZonePct { get; set; }

        [Parameter("H1 EMA Period", Group = "V2 Filters", DefaultValue = 50, MinValue = 2)]
        public int H1EmaPeriod { get; set; }

        [Parameter("Label", Group = "Misc", DefaultValue = "GoldBO")]
        public string Label { get; set; }

        // ---------------- State ----------------
        private DateTime _day = DateTime.MinValue;
        private AverageTrueRange _atr;
        private Bars _h1;
        private ExponentialMovingAverage _h1Ema;
        private bool _rangeReady;
        private bool _tradedToday;
        private bool _skipToday;
        private double _rangeHi, _rangeLo;
        private bool _revDone;
        private TradeType? _revPending;     // direction waiting for the stop-out candle to close
        private double _revOrigEntry;
        private DateTime _revStopTime;

        protected override void OnStart()
        {
            if (!SymbolName.ToUpper().Contains("XAU"))
            {
                Print("This bot is for gold (XAUUSD) only. Current symbol: {0}. Stopping.", SymbolName);
                Stop();
                return;
            }
            _atr = Indicators.AverageTrueRange(Bars, 14, MovingAverageType.WilderSmoothing);
            _h1 = MarketData.GetBars(TimeFrame.Hour);
            _h1Ema = Indicators.ExponentialMovingAverage(_h1.ClosePrices, H1EmaPeriod);
            Print("V2 filters: {0}", UseV2 ? string.Format("ON (buffer {0} ATR, body {1}%, close zone {2}%, H1 EMA{3})", AtrBuffer, MinBodyPct, CloseZonePct, H1EmaPeriod) : "OFF");
            Print("Gold pip size {0}, pip value per unit {1}", Symbol.PipSize, Symbol.PipValue);
            if (TimeFrame != TimeFrame.Minute5)
                Print("WARNING: run this bot on a 5-minute (m5) chart. Current timeframe: {0}", TimeFrame);

            Print("Selective reverse: {0}", RevEnabled ? string.Format("ON (<= {0} min, entry before {1} UTC, {2}R, {3}x risk)", RevMaxMinutes, RevEntryBefore, RevRR, RevRiskFactor) : "OFF");
            Print("GoldBreakoutBot started on {0} {1}. Risk {2}%, stop {3}, TP {4}R. Range {5:00}:00-{6:00}:00 UTC, entries until {7:00}:00, close all {8:00}:00 UTC.",
                SymbolName, TimeFrame, RiskPct, StopMode, RR, RangeStartHour, RangeEndHour, EntryEndHour, ExitHour);

            Positions.Closed += OnPositionClosed;
            ResetDay(Server.Time.Date);
            // after a restart: don't trade twice on the same day
            if (TradedToday()) { _tradedToday = true; Print("Already traded today -> no new entry today."); }
            foreach (var t in History)
                if (t.Label == Label + "_R" && t.SymbolName == SymbolName && t.EntryTime.Date == Server.Time.Date) _revDone = true;
            if (Positions.FindAll(Label + "_R", SymbolName).Any()) _revDone = true;
        }

        private void ResetDay(DateTime day)
        {
            _day = day;
            _rangeReady = false;
            _tradedToday = false;
            _skipToday = false;
            _rangeHi = _rangeLo = 0;
            _revDone = false;
            _revPending = null;
        }

        // ---------- selective reverse ----------
        private void OnPositionClosed(PositionClosedEventArgs args)
        {
            var p = args.Position;
            if (!RevEnabled || _revDone || p.Label != Label || p.SymbolName != SymbolName) return;
            if (args.Reason != PositionCloseReason.StopLoss) return;
            DateTime now = Server.Time;
            if (p.EntryTime.Date != now.Date || !_rangeReady) return;
            double mins = (now - p.EntryTime).TotalMinutes;
            double entryH = p.EntryTime.TimeOfDay.TotalHours, stopH = now.TimeOfDay.TotalHours;
            if (mins > RevMaxMinutes) { Print("No reverse: stop hit after {0:F0} min (> {1}).", mins, RevMaxMinutes); return; }
            if (entryH >= RevEntryBefore) { Print("No reverse: original entry too late."); return; }
            if (stopH >= RevStopCutoff) { Print("No reverse: stop hit after cutoff."); return; }
            _revDone = true;   // only one chance per day
            var dir = p.TradeType == TradeType.Buy ? TradeType.Sell : TradeType.Buy;
            _revOrigEntry = p.EntryPrice;
            _revStopTime = now;
            if (RevClean) { _revPending = dir; Print("Fast failure -> waiting for this candle to close past the middle."); }
            else EnterReverse(dir);
        }

        private void EnterReverse(TradeType dir)
        {
            if (now_hour() >= ExitHour) return;
            bool buy = dir == TradeType.Buy;
            double entry = buy ? Symbol.Ask : Symbol.Bid;
            double stop = buy ? _revOrigEntry - RevStopExtra * Symbol.PipSize : _revOrigEntry + RevStopExtra * Symbol.PipSize;
            if ((buy && stop >= entry) || (!buy && stop <= entry)) { Print("Reverse skipped: price already past the reverse stop."); return; }
            double slPips = Math.Abs(entry - stop) / Symbol.PipSize;
            if (slPips < 3) { Print("Reverse skipped: stop only {0:F1} pips.", slPips); return; }
            double riskMoney = Account.Equity * RiskPct * RevRiskFactor / 100.0;
            double units = Symbol.NormalizeVolumeInUnits(riskMoney / (slPips * Symbol.PipValue), RoundingMode.Down);
            if (units < Symbol.VolumeInUnitsMin)
            {
                if (!UseMinLot) { Print("Reverse skipped: risk below minimum lot."); return; }
                units = Symbol.VolumeInUnitsMin;
            }
            var res = ExecuteMarketOrder(dir, SymbolName, units, Label + "_R", Math.Round(slPips, 1), Math.Round(slPips * RevRR, 1), "Selective reverse");
            if (res.IsSuccessful)
                Print("REVERSE {0} {1} lots | SL {2:F1} pips | TP {3:F1} pips", dir, Symbol.VolumeInUnitsToQuantity(units), slPips, slPips * RevRR);
            else Print("Reverse order failed: {0}", res.Error);
        }

        private int now_hour() { return Server.Time.Hour; }

        private bool TradedToday()
        {
            DateTime d = Server.Time.Date;
            if (Positions.FindAll(Label, SymbolName).Any(p => p.EntryTime.Date == d)) return true;
            foreach (var t in History)
                if (t.Label == Label && t.SymbolName == SymbolName && t.EntryTime.Date == d) return true;
            return false;
        }

        protected override void OnBar()
        {
            int last = Bars.Count - 2;                 // last CLOSED bar
            if (last < 1) return;
            if (EquityStop > 0 && Account.Equity < EquityStop)
            {
                foreach (var q in Positions.FindAll(Label, SymbolName).Concat(Positions.FindAll(Label + "_R", SymbolName)).ToArray())
                    ClosePosition(q);
                Print("Equity {0:F2} below {1} -> bot stopped.", Account.Equity, EquityStop);
                Stop();
                return;
            }
            DateTime barOpen = Bars.OpenTimes[last];
            DateTime barClose = barOpen.AddMinutes(5);
            DateTime now = Server.Time;

            if (now.Date != _day) ResetDay(now.Date);

            // 1) time exit
            if (now.Hour >= ExitHour)
            {
                foreach (var p in Positions.FindAll(Label, SymbolName).Concat(Positions.FindAll(Label + "_R", SymbolName)).ToArray())
                {
                    var r = ClosePosition(p);
                    if (r.IsSuccessful) Print("Time exit: closed {0}, net {1:F2}", p.TradeType, p.NetProfit);
                    else Print("Time exit failed: {0}", r.Error);
                }
                return;
            }

            // pending reverse: check the candle that hit the original stop
            if (_revPending != null && barClose > _revStopTime)
            {
                var dir = _revPending.Value; _revPending = null;
                double mid = (_rangeHi + _rangeLo) / 2.0, c = Bars.ClosePrices[last];
                bool clean = dir == TradeType.Sell ? c < mid : c > mid;
                if (clean) EnterReverse(dir);
                else Print("No reverse: stop-out candle closed back on the original side (not a clean failure).");
            }

            if (_tradedToday || _skipToday) return;

            DateTime rangeStart = _day.AddHours(RangeStartHour);
            DateTime rangeEnd = _day.AddHours(RangeEndHour);
            DateTime entryEnd = _day.AddHours(EntryEndHour);

            // 2) build today's range once the range window is complete
            if (!_rangeReady)
            {
                if (barClose < rangeEnd) return;
                double hi = double.MinValue, lo = double.MaxValue;
                int count = 0;
                for (int i = last; i >= 0; i--)
                {
                    DateTime t = Bars.OpenTimes[i];
                    if (t < rangeStart) break;
                    if (t >= rangeEnd) continue;
                    hi = Math.Max(hi, Bars.HighPrices[i]);
                    lo = Math.Min(lo, Bars.LowPrices[i]);
                    count++;
                }
                int expected = (RangeEndHour - RangeStartHour) * 12;
                if (count < expected * 0.8)
                {
                    Print("Range skipped: only {0}/{1} bars in range window.", count, expected);
                    _skipToday = true;
                    return;
                }
                double widthPips = hi - lo;
                if (widthPips < MinRangePips || (MaxRangePips > 0 && widthPips > MaxRangePips))
                {
                    Print("Range {0:F1} pips outside limits -> no trade today.", widthPips);
                    _skipToday = true;
                    return;
                }
                _rangeHi = hi; _rangeLo = lo; _rangeReady = true;
                Print("Asian range {0}: high {1} low {2} ({3:F1} pips)", _day.ToString("dd MMM"),
                      hi.ToString("F" + Symbol.Digits), lo.ToString("F" + Symbol.Digits), widthPips);
            }

            // 3) entry window
            if (barOpen < rangeEnd || barClose > entryEnd) return;

            double close = Bars.ClosePrices[last];
            double buf = UseV2 ? AtrBuffer * _atr.Result[last - 1] : 0;
            TradeType? side = null;
            if (close > _rangeHi + buf) side = TradeType.Buy;
            else if (close < _rangeLo - buf) side = TradeType.Sell;
            if (side == null) return;

            if (UseV2 && !V2Ok(side.Value, last, barClose)) return;   // keep watching

            Enter(side.Value);
        }

        private bool V2Ok(TradeType side, int i, DateTime barClose)
        {
            double o = Bars.OpenPrices[i], h = Bars.HighPrices[i], l = Bars.LowPrices[i], c = Bars.ClosePrices[i];
            double span = h - l;
            if (span <= 0) return false;
            bool buy = side == TradeType.Buy;
            if (Math.Abs(c - o) < MinBodyPct / 100.0 * span) { Print("V2: {0} breakout ignored, weak body.", side); return false; }
            if (buy && c < h - CloseZonePct / 100.0 * span) { Print("V2: Buy breakout ignored, close not near high."); return false; }
            if (!buy && c > l + CloseZonePct / 100.0 * span) { Print("V2: Sell breakout ignored, close not near low."); return false; }
            int j = _h1.Count - 1;
            while (j > 0 && _h1.OpenTimes[j].AddHours(1) > barClose) j--;   // last COMPLETED H1 candle
            if (j < H1EmaPeriod) { Print("V2: not enough H1 history."); return false; }
            double hc = _h1.ClosePrices[j], ema = _h1Ema.Result[j];
            if (buy ? hc <= ema : hc >= ema)
            {
                Print("V2: {0} breakout ignored, H1 close {1:F2} vs EMA{2} {3:F2} against trade.", side, hc, H1EmaPeriod, ema);
                return false;
            }
            return true;
        }

        private void Enter(TradeType side)
        {
            _tradedToday = true;   // one attempt per day, even if filtered below

            double spreadUsd = Symbol.Spread;
            if (MaxSpreadPips > 0 && spreadUsd > MaxSpreadPips)
            {
                Print("Breakout {0} skipped: spread ${1:F2} > ${2}", side, spreadUsd, MaxSpreadPips);
                return;
            }

            bool buy = side == TradeType.Buy;
            double entry = buy ? Symbol.Ask : Symbol.Bid;
            double stop = StopMode == GoldStopPlacement.RangeMiddle
                ? (_rangeHi + _rangeLo) / 2.0
                : (buy ? _rangeLo : _rangeHi);

            if ((buy && stop >= entry) || (!buy && stop <= entry))
            {
                Print("Breakout {0} skipped: price already back beyond stop level.", side);
                return;
            }

            double slPips = Math.Abs(entry - stop) / Symbol.PipSize;
            if (Math.Abs(entry - stop) < 1.0)
            {
                Print("Breakout {0} skipped: stop only ${1:F2}.", side, Math.Abs(entry - stop));
                return;
            }
            double tpPips = slPips * RR;

            double riskMoney = Account.Equity * RiskPct / 100.0;
            double units = riskMoney / (slPips * Symbol.PipValue);
            units = Symbol.NormalizeVolumeInUnits(units, RoundingMode.Down);
            if (units < Symbol.VolumeInUnitsMin)
            {
                if (!UseMinLot)
                {
                    Print("Breakout {0} skipped: {1}% risk with {2:F1}-pip stop is below the minimum lot.", side, RiskPct, slPips);
                    return;
                }
                units = Symbol.VolumeInUnitsMin;
                double realPct = slPips * Symbol.PipValue * units / Account.Equity * 100.0;
                if (MaxRiskAtMinLot > 0 && realPct > MaxRiskAtMinLot)
                {
                    Print("Breakout {0} skipped: 0.01 lot would risk {1:F1}% of the account (> {2}%). Stop is ${3:F2}.", side, realPct, MaxRiskAtMinLot, Math.Abs(entry - stop));
                    return;
                }
            }
            if (units > Symbol.VolumeInUnitsMax) units = Symbol.VolumeInUnitsMax;

            var res = ExecuteMarketOrder(side, SymbolName, units, Label, Math.Round(slPips, 1), Math.Round(tpPips, 1), "Gold breakout");
            if (res.IsSuccessful)
                Print("{0} {1} {2} lots | SL {3:F1} pips | TP {4:F1} pips | risk about {5:F2}",
                      side, SymbolName, Symbol.VolumeInUnitsToQuantity(units), slPips, tpPips,
                      slPips * Symbol.PipValue * units);
            else
                Print("Order failed: {0}", res.Error);
        }
    }
}
