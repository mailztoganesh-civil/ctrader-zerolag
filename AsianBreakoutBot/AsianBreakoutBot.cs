// AsianBreakoutBot - Asian range breakout at London open (cTrader cBot)
// Range: 00:00-07:00 UTC (04:00-11:00 Dubai). Entry: first M5 close beyond the
// range between 07:00 and 12:00 UTC. One trade per day. SL at range middle
// (or opposite side), TP = RR x risk. Everything closed at 20:00 UTC (00:00 Dubai).
// Position size from Risk % of equity.

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

        [Parameter("Reward:Risk (TP)", Group = "Risk", DefaultValue = 2.0, MinValue = 0.5)]
        public double RR { get; set; }

        [Parameter("Use Min Lot If Risk Too Small", Group = "Risk", DefaultValue = false)]
        public bool UseMinLot { get; set; }

        // ---------------- Session (UTC) ----------------
        [Parameter("Range Start Hour UTC", Group = "Session (UTC)", DefaultValue = 0, MinValue = 0, MaxValue = 23)]
        public int RangeStartHour { get; set; }

        [Parameter("Range End Hour UTC", Group = "Session (UTC)", DefaultValue = 7, MinValue = 1, MaxValue = 23)]
        public int RangeEndHour { get; set; }

        [Parameter("Last Entry Hour UTC", Group = "Session (UTC)", DefaultValue = 12, MinValue = 1, MaxValue = 23)]
        public int EntryEndHour { get; set; }

        [Parameter("Close All Hour UTC", Group = "Session (UTC)", DefaultValue = 20, MinValue = 1, MaxValue = 23)]
        public int ExitHour { get; set; }

        // ---------------- Filters ----------------
        [Parameter("Min Range Pips", Group = "Filters", DefaultValue = 5, MinValue = 0)]
        public double MinRangePips { get; set; }

        [Parameter("Max Range Pips (0 = off)", Group = "Filters", DefaultValue = 0, MinValue = 0)]
        public double MaxRangePips { get; set; }

        [Parameter("Max Spread Pips (0 = off)", Group = "Filters", DefaultValue = 3, MinValue = 0)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Label", Group = "Misc", DefaultValue = "AsiaBO")]
        public string Label { get; set; }

        // ---------------- State ----------------
        private DateTime _day = DateTime.MinValue;
        private bool _rangeReady;
        private bool _tradedToday;
        private bool _skipToday;
        private double _rangeHi, _rangeLo;

        protected override void OnStart()
        {
            if (TimeFrame != TimeFrame.Minute5)
                Print("WARNING: run this bot on a 5-minute (m5) chart. Current timeframe: {0}", TimeFrame);

            Print("AsianBreakoutBot started on {0} {1}. Risk {2}%, stop {3}, TP {4}R. Range {5:00}:00-{6:00}:00 UTC, entries until {7:00}:00, close all {8:00}:00 UTC.",
                SymbolName, TimeFrame, RiskPct, StopMode, RR, RangeStartHour, RangeEndHour, EntryEndHour, ExitHour);

            ResetDay(Server.Time.Date);
            // after a restart: don't trade twice on the same day
            if (TradedToday()) { _tradedToday = true; Print("Already traded today -> no new entry today."); }
        }

        private void ResetDay(DateTime day)
        {
            _day = day;
            _rangeReady = false;
            _tradedToday = false;
            _skipToday = false;
            _rangeHi = _rangeLo = 0;
        }

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
            DateTime barOpen = Bars.OpenTimes[last];
            DateTime barClose = barOpen.AddMinutes(5);
            DateTime now = Server.Time;

            if (now.Date != _day) ResetDay(now.Date);

            // 1) time exit
            if (now.Hour >= ExitHour)
            {
                foreach (var p in Positions.FindAll(Label, SymbolName))
                {
                    var r = ClosePosition(p);
                    if (r.IsSuccessful) Print("Time exit: closed {0}, net {1:F2}", p.TradeType, p.NetProfit);
                    else Print("Time exit failed: {0}", r.Error);
                }
                return;
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
