// AsianBreakoutBot - Asian range breakout at London open (cTrader cBot)
// Range: 03:00-07:00 UTC (07:00-11:00 Dubai). Entry: first M5 close beyond the
// range between 07:00 and 14:00 UTC (11:00-18:00 Dubai). One trade per day.
// SL at range middle, TP = 3 x risk. Everything closed at 20:00 UTC (00:00 Dubai).
// Optional Selective Reverse: if the original trade is stopped within 45 min and
// was entered before 09:00 UTC (13:00 Dubai), and the stop-out candle closes past
// the range middle, open ONE opposite trade: stop = original entry +/- 2.5 pips,
// target 1.5R, risk 0.7 x normal. No reverse if the stop is hit after 10:30 UTC.

using System;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    public enum StopPlacement { RangeMiddle, OppositeSide }

    [Robot(AccessRights = AccessRights.None, TimeZone = TimeZones.UTC, AddIndicators = false)]
    public class AsianBreakoutBot : Robot
    {
        // ---------------- Risk ----------------
        [Parameter("Risk % per Trade", Group = "Risk", DefaultValue = 2.0, MinValue = 0.1, MaxValue = 10)]
        public double RiskPct { get; set; }

        [Parameter("Stop Placement", Group = "Risk", DefaultValue = StopPlacement.RangeMiddle)]
        public StopPlacement StopMode { get; set; }

        [Parameter("Reward:Risk (TP)", Group = "Risk", DefaultValue = 3.0, MinValue = 0.5)]
        public double RR { get; set; }

        [Parameter("Use Min Lot If Risk Too Small", Group = "Risk", DefaultValue = true)]
        public bool UseMinLot { get; set; }

        // ---------------- Session (UTC) ----------------
        [Parameter("Range Start Hour UTC", Group = "Session (UTC)", DefaultValue = 3, MinValue = 0, MaxValue = 23)]
        public int RangeStartHour { get; set; }

        [Parameter("Range End Hour UTC", Group = "Session (UTC)", DefaultValue = 7, MinValue = 1, MaxValue = 23)]
        public int RangeEndHour { get; set; }

        [Parameter("Last Entry Hour UTC", Group = "Session (UTC)", DefaultValue = 14, MinValue = 1, MaxValue = 23)]
        public int EntryEndHour { get; set; }

        [Parameter("Close All Hour UTC", Group = "Session (UTC)", DefaultValue = 20, MinValue = 1, MaxValue = 23)]
        public int ExitHour { get; set; }

        // ---------------- Filters ----------------
        [Parameter("Min Range Pips", Group = "Filters", DefaultValue = 0, MinValue = 0)]
        public double MinRangePips { get; set; }

        [Parameter("Max Range Pips (0 = off)", Group = "Filters", DefaultValue = 0, MinValue = 0)]
        public double MaxRangePips { get; set; }

        [Parameter("Max Spread Pips (0 = off)", Group = "Filters", DefaultValue = 3, MinValue = 0)]
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

        [Parameter("Stop Bot If Equity Below (0 = off)", Group = "Risk", DefaultValue = 85, MinValue = 0)]
        public double EquityStop { get; set; }

        [Parameter("Label", Group = "Misc", DefaultValue = "AsiaBO")]
        public string Label { get; set; }

        // ---------------- State ----------------
        private DateTime _day = DateTime.MinValue;
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
            if (TimeFrame != TimeFrame.Minute5)
                Print("WARNING: run this bot on a 5-minute (m5) chart. Current timeframe: {0}", TimeFrame);

            Print("Selective reverse: {0}", RevEnabled ? string.Format("ON (<= {0} min, entry before {1} UTC, {2}R, {3}x risk)", RevMaxMinutes, RevEntryBefore, RevRR, RevRiskFactor) : "OFF");
            Print("AsianBreakoutBot started on {0} {1}. Risk {2}%, stop {3}, TP {4}R. Range {5:00}:00-{6:00}:00 UTC, entries until {7:00}:00, close all {8:00}:00 UTC.",
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
                double widthPips = (hi - lo) / Symbol.PipSize;
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
            TradeType? side = null;
            if (close > _rangeHi) side = TradeType.Buy;
            else if (close < _rangeLo) side = TradeType.Sell;
            if (side == null) return;

            Enter(side.Value);
        }

        private void Enter(TradeType side)
        {
            _tradedToday = true;   // one attempt per day, even if filtered below

            double spreadPips = Symbol.Spread / Symbol.PipSize;
            if (MaxSpreadPips > 0 && spreadPips > MaxSpreadPips)
            {
                Print("Breakout {0} skipped: spread {1:F1} pips > {2}", side, spreadPips, MaxSpreadPips);
                return;
            }

            bool buy = side == TradeType.Buy;
            double entry = buy ? Symbol.Ask : Symbol.Bid;
            double stop = StopMode == StopPlacement.RangeMiddle
                ? (_rangeHi + _rangeLo) / 2.0
                : (buy ? _rangeLo : _rangeHi);

            if ((buy && stop >= entry) || (!buy && stop <= entry))
            {
                Print("Breakout {0} skipped: price already back beyond stop level.", side);
                return;
            }

            double slPips = Math.Abs(entry - stop) / Symbol.PipSize;
            if (slPips < 3)
            {
                Print("Breakout {0} skipped: stop only {1:F1} pips.", side, slPips);
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
            }
            if (units > Symbol.VolumeInUnitsMax) units = Symbol.VolumeInUnitsMax;

            var res = ExecuteMarketOrder(side, SymbolName, units, Label, Math.Round(slPips, 1), Math.Round(tpPips, 1), "Asia breakout");
            if (res.IsSuccessful)
                Print("{0} {1} {2} lots | SL {3:F1} pips | TP {4:F1} pips | risk about {5:F2}",
                      side, SymbolName, Symbol.VolumeInUnitsToQuantity(units), slPips, tpPips,
                      slPips * Symbol.PipValue * units);
            else
                Print("Order failed: {0}", res.Error);
        }
    }
}
