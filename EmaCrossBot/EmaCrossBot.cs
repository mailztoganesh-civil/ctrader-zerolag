// EmaCrossBot - EMA 34/50 crossover on M5 (fitted on EURUSD, also positive on USDCAD in tests)
// Entry: EMA fast crosses EMA slow on a CLOSED M5 candle, only between 07:00 and 16:00 UTC
//        (11:00-20:00 Dubai). Stop: 3 x ATR(14). Target: 3R. All positions closed at 20:00 UTC.
// Size: Risk % of equity. Safety: max trades per day, equity stop (bot halts below a floor).

using System;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    [Robot(AccessRights = AccessRights.None, TimeZone = TimeZones.UTC, AddIndicators = false)]
    public class EmaCrossBot : Robot
    {
        // ---------------- Risk ----------------
        [Parameter("Risk % per Trade", Group = "Risk", DefaultValue = 2.0, MinValue = 0.1, MaxValue = 10)]
        public double RiskPct { get; set; }

        [Parameter("ATR Stop Multiplier", Group = "Risk", DefaultValue = 3.0, MinValue = 0.5)]
        public double AtrMult { get; set; }

        [Parameter("Reward:Risk (TP)", Group = "Risk", DefaultValue = 3.0, MinValue = 0.5)]
        public double RR { get; set; }

        [Parameter("Use Min Lot If Risk Too Small", Group = "Risk", DefaultValue = false)]
        public bool UseMinLot { get; set; }

        // ---------------- Safety ----------------
        [Parameter("Max Trades Per Day", Group = "Safety", DefaultValue = 3, MinValue = 1)]
        public int MaxTradesPerDay { get; set; }

        [Parameter("Equity Stop (0 = off)", Group = "Safety", DefaultValue = 105, MinValue = 0)]
        public double EquityFloor { get; set; }

        [Parameter("Max Spread Pips (0 = off)", Group = "Safety", DefaultValue = 2.0, MinValue = 0)]
        public double MaxSpreadPips { get; set; }

        // ---------------- Strategy ----------------
        [Parameter("Fast EMA", Group = "Strategy", DefaultValue = 34, MinValue = 2)]
        public int FastPeriod { get; set; }

        [Parameter("Slow EMA", Group = "Strategy", DefaultValue = 50, MinValue = 3)]
        public int SlowPeriod { get; set; }

        [Parameter("ATR Period", Group = "Strategy", DefaultValue = 14, MinValue = 2)]
        public int AtrPeriod { get; set; }

        // ---------------- Session (UTC) ----------------
        [Parameter("First Entry Hour UTC", Group = "Session (UTC)", DefaultValue = 7, MinValue = 0, MaxValue = 23)]
        public int StartHour { get; set; }

        [Parameter("Last Entry Hour UTC", Group = "Session (UTC)", DefaultValue = 16, MinValue = 1, MaxValue = 23)]
        public int EndHour { get; set; }

        [Parameter("Close All Hour UTC", Group = "Session (UTC)", DefaultValue = 20, MinValue = 1, MaxValue = 23)]
        public int ExitHour { get; set; }

        [Parameter("Label", Group = "Misc", DefaultValue = "EMA3450")]
        public string Label { get; set; }

        private ExponentialMovingAverage _fast, _slow;
        private DateTime _day = DateTime.MinValue;
        private int _tradesToday;
        private bool _halted;

        protected override void OnStart()
        {
            if (TimeFrame != TimeFrame.Minute5)
                Print("WARNING: designed for a 5-minute (m5) chart. Current timeframe: {0}", TimeFrame);
            if (FastPeriod >= SlowPeriod)
                Print("WARNING: Fast EMA should be smaller than Slow EMA.");

            _fast = Indicators.ExponentialMovingAverage(Bars.ClosePrices, FastPeriod);
            _slow = Indicators.ExponentialMovingAverage(Bars.ClosePrices, SlowPeriod);

            _day = Server.Time.Date;
            _tradesToday = CountTradesToday();
            Print("EmaCrossBot started on {0} {1}. Risk {2}%, stop {3}xATR, TP {4}R, entries {5:00}-{6:00} UTC, close {7:00} UTC. Equity {8:F2}, floor {9}. Trades today so far: {10}",
                SymbolName, TimeFrame, RiskPct, AtrMult, RR, StartHour, EndHour, ExitHour, Account.Equity, EquityFloor, _tradesToday);
        }

        private int CountTradesToday()
        {
            DateTime d = Server.Time.Date;
            int n = Positions.FindAll(Label, SymbolName).Count(p => p.EntryTime.Date == d);
            foreach (var t in History)
                if (t.Label == Label && t.SymbolName == SymbolName && t.EntryTime.Date == d) n++;
            return n;
        }

        // ATR with Wilder smoothing over recent history (same as the backtest)
        private double Atr(int last)
        {
            int start = Math.Max(1, last - AtrPeriod * 20);
            double atr = 0; int k = 0;
            for (int i = start; i <= last; i++)
            {
                double tr = Math.Max(Bars.HighPrices[i] - Bars.LowPrices[i],
                            Math.Max(Math.Abs(Bars.HighPrices[i] - Bars.ClosePrices[i - 1]),
                                     Math.Abs(Bars.LowPrices[i] - Bars.ClosePrices[i - 1])));
                k++;
                atr = k <= AtrPeriod ? atr + (tr - atr) / k : (atr * (AtrPeriod - 1) + tr) / AtrPeriod;
            }
            return atr;
        }

        protected override void OnTick()
        {
            if (_halted || EquityFloor <= 0 || Account.Equity > EquityFloor) return;
            _halted = true;
            foreach (var p in Positions.FindAll(Label, SymbolName)) ClosePosition(p);
            Print("EQUITY STOP: equity {0:F2} fell to the floor {1}. Positions closed, bot stopped.", Account.Equity, EquityFloor);
            Stop();
        }

        protected override void OnBar()
        {
            if (_halted) return;
            int last = Bars.Count - 2;            // last CLOSED candle
            if (last < SlowPeriod + 2) return;

            DateTime now = Server.Time;
            if (now.Date != _day) { _day = now.Date; _tradesToday = 0; }

            // time exit
            if (now.Hour >= ExitHour)
            {
                foreach (var p in Positions.FindAll(Label, SymbolName))
                {
                    var r = ClosePosition(p);
                    if (r.IsSuccessful) Print("Time exit: closed {0}, net {1:F2}", p.TradeType, p.NetProfit);
                }
                return;
            }

            if (Positions.FindAll(Label, SymbolName).Length > 0) return;   // one trade at a time

            DateTime barOpen = Bars.OpenTimes[last];
            int m = barOpen.Hour * 60 + barOpen.Minute;
            if (m < StartHour * 60 || m >= EndHour * 60) return;

            double f1 = _fast.Result[last], f0 = _fast.Result[last - 1];
            double s1 = _slow.Result[last], s0 = _slow.Result[last - 1];
            TradeType? side = null;
            if (f1 > s1 && f0 <= s0) side = TradeType.Buy;
            else if (f1 < s1 && f0 >= s0) side = TradeType.Sell;
            if (side == null) return;

            if (_tradesToday >= MaxTradesPerDay)
            {
                Print("{0} signal skipped: max {1} trades today reached.", side, MaxTradesPerDay);
                return;
            }

            double spreadPips = Symbol.Spread / Symbol.PipSize;
            if (MaxSpreadPips > 0 && spreadPips > MaxSpreadPips)
            {
                Print("{0} signal skipped: spread {1:F1} pips.", side, spreadPips);
                return;
            }

            double slPips = AtrMult * Atr(last) / Symbol.PipSize;
            if (slPips < 3) { Print("{0} signal skipped: stop only {1:F1} pips.", side, slPips); return; }
            double tpPips = slPips * RR;

            double units = Account.Equity * RiskPct / 100.0 / (slPips * Symbol.PipValue);
            units = Symbol.NormalizeVolumeInUnits(units, RoundingMode.Down);
            if (units < Symbol.VolumeInUnitsMin)
            {
                if (!UseMinLot)
                {
                    Print("{0} signal skipped: {1}% risk with a {2:F1}-pip stop is below the minimum lot.", side, RiskPct, slPips);
                    return;
                }
                units = Symbol.VolumeInUnitsMin;
            }
            if (units > Symbol.VolumeInUnitsMax) units = Symbol.VolumeInUnitsMax;

            var res = ExecuteMarketOrder(side.Value, SymbolName, units, Label, Math.Round(slPips, 1), Math.Round(tpPips, 1), "EMA 34/50 cross");
            if (res.IsSuccessful)
            {
                _tradesToday++;
                Print("{0} {1} {2} lots | SL {3:F1} pips | TP {4:F1} pips | risk about {5:F2}",
                      side, SymbolName, Symbol.VolumeInUnitsToQuantity(units), slPips, tpPips, slPips * Symbol.PipValue * units);
            }
            else Print("Order failed: {0}", res.Error);
        }
    }
}
