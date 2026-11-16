// SmaPullbackBot - SMA 9/18 crossover -> move -> pullback to MA -> bounce entry (cTrader cBot)
//
// Buy setup (sell = exact mirror):
//   1. CROSS    SMA 9 crosses above SMA 18 on a closed candle.
//               (optional) Trend filter: close must be above SMA 200, else the cross is ignored.
//   2. MOVE     price runs away: highest high since the cross is at least 'Move Size (x ATR)'
//               above SMA 9.
//   3. PULLBACK a candle's low touches the chosen MA (SMA 9 or SMA 18).
//               Cancelled if a candle closes below SMA 18, or no bounce within 'Max Pullback Candles'.
//   4. BOUNCE   a candle closes above the previous candle's high AND above SMA 9 -> BUY at market.
//   SL  below the lowest low of the pullback (- buffer). TP = RR x risk.
//   One trade per crossover, one position at a time. An opposite cross resets everything.
//
// Backtest (Dukascopy M5, Nov 2024 - Nov 2026, RR 2, move 1 ATR, touch SMA 18, 0.8 pip cost):
//   No filter : negative on every major pair on M5/M15 (PF 0.75-0.91).
//   SMA200 filter on : roughly break-even (EURUSD M5 PF 1.01, GBPUSD M15 1.03, EURJPY M15 1.06).
//   => NOT a proven edge. Run on DEMO only until it shows results.

using System;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    public enum TouchMa { Sma9, Sma18 }

    [Robot(AccessRights = AccessRights.None, TimeZone = TimeZones.UTC, AddIndicators = true)]
    public class SmaPullbackBot : Robot
    {
        // ---------------- Strategy ----------------
        [Parameter("Fast SMA", Group = "Strategy", DefaultValue = 9, MinValue = 2)]
        public int FastPeriod { get; set; }

        [Parameter("Slow SMA", Group = "Strategy", DefaultValue = 18, MinValue = 3)]
        public int SlowPeriod { get; set; }

        [Parameter("Move Size (x ATR)", Group = "Strategy", DefaultValue = 1.0, MinValue = 0)]
        public double MoveAtr { get; set; }

        [Parameter("Pullback Touches", Group = "Strategy", DefaultValue = TouchMa.Sma18)]
        public TouchMa Touch { get; set; }

        [Parameter("Max Pullback Candles", Group = "Strategy", DefaultValue = 20, MinValue = 1)]
        public int MaxPbBars { get; set; }

        [Parameter("Trend Filter SMA 200", Group = "Strategy", DefaultValue = true)]
        public bool UseTrend { get; set; }

        [Parameter("Trend SMA Period", Group = "Strategy", DefaultValue = 200, MinValue = 20)]
        public int TrendPeriod { get; set; }

        // ---------------- Risk ----------------
        [Parameter("Risk % per Trade", Group = "Risk", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 10)]
        public double RiskPct { get; set; }

        [Parameter("Reward:Risk (TP)", Group = "Risk", DefaultValue = 2.0, MinValue = 0.5)]
        public double RR { get; set; }

        [Parameter("SL Buffer Pips", Group = "Risk", DefaultValue = 1.0, MinValue = 0)]
        public double BufferPips { get; set; }

        [Parameter("Min SL Pips", Group = "Risk", DefaultValue = 5, MinValue = 0)]
        public double MinSlPips { get; set; }

        [Parameter("Max SL Pips", Group = "Risk", DefaultValue = 40, MinValue = 1)]
        public double MaxSlPips { get; set; }

        [Parameter("Use Min Lot If Risk Too Small", Group = "Risk", DefaultValue = true)]
        public bool UseMinLot { get; set; }

        [Parameter("Max Risk % At Min Lot (0 = off)", Group = "Risk", DefaultValue = 5, MinValue = 0)]
        public double MaxRiskAtMinLot { get; set; }

        // ---------------- Safety ----------------
        [Parameter("Start Hour UTC", Group = "Safety", DefaultValue = 7, MinValue = 0, MaxValue = 23)]
        public int StartHour { get; set; }

        [Parameter("End Hour UTC", Group = "Safety", DefaultValue = 20, MinValue = 1, MaxValue = 24)]
        public int EndHour { get; set; }

        [Parameter("Max Trades Per Day", Group = "Safety", DefaultValue = 3, MinValue = 1)]
        public int MaxTradesPerDay { get; set; }

        [Parameter("Max Spread Pips (0 = off)", Group = "Safety", DefaultValue = 2.0, MinValue = 0)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Stop Bot If Equity Below (0 = off)", Group = "Safety", DefaultValue = 0, MinValue = 0)]
        public double EquityStop { get; set; }

        [Parameter("Label", Group = "Misc", DefaultValue = "SmaPB")]
        public string Label { get; set; }

        private SimpleMovingAverage _fast, _slow, _trend;
        private AverageTrueRange _atr;

        // state: 0 idle, 1 waiting for move, 2 waiting for pullback, 3 waiting for bounce
        private int _dir, _state, _touchBar;
        private double _ext, _pbExt;
        private bool _used;
        private DateTime _day = DateTime.MinValue;
        private int _tradesToday;

        protected override void OnStart()
        {
            _fast = Indicators.SimpleMovingAverage(Bars.ClosePrices, FastPeriod);
            _slow = Indicators.SimpleMovingAverage(Bars.ClosePrices, SlowPeriod);
            _trend = Indicators.SimpleMovingAverage(Bars.ClosePrices, TrendPeriod);
            _atr = Indicators.AverageTrueRange(14, MovingAverageType.WilderSmoothing);

            Positions.Opened += a => { if (a.Position.Label == Label && a.Position.SymbolName == SymbolName) Print("{0} opened at {1}, SL {2}, TP {3}", a.Position.TradeType, a.Position.EntryPrice, a.Position.StopLoss, a.Position.TakeProfit); };
            Positions.Closed += a => { if (a.Position.Label == Label && a.Position.SymbolName == SymbolName) Print("{0} closed ({1}) net {2:F2}", a.Position.TradeType, a.Reason, a.Position.NetProfit); };

            // warm-up so the current setup state is known (no trading)
            int start = Math.Max(TrendPeriod, SlowPeriod) + 2;
            for (int i = start; i <= Bars.Count - 2; i++) Step(i, false);

            Print("SmaPullbackBot started on {0} {1}. SMA {2}/{3}, move {4} ATR, touch {5}, RR {6}, risk {7}%, trend filter {8}. State {9} dir {10}.",
                SymbolName, TimeFrame, FastPeriod, SlowPeriod, MoveAtr, Touch, RR, RiskPct, UseTrend, _state, _dir);
        }

        protected override void OnBar()
        {
            int i = Bars.Count - 2;                       // last CLOSED candle
            if (i < Math.Max(TrendPeriod, SlowPeriod) + 2) return;

            if (EquityStop > 0 && Account.Equity < EquityStop)
            {
                foreach (var p in Positions.FindAll(Label, SymbolName)) ClosePosition(p);
                Print("Equity {0:F2} below {1} -> bot stopped.", Account.Equity, EquityStop);
                Stop();
                return;
            }

            if (Bars.OpenTimes[i].Date != _day) { _day = Bars.OpenTimes[i].Date; _tradesToday = 0; }
            Step(i, true);
        }

        private void Step(int i, bool live)
        {
            double f = _fast.Result[i], s = _slow.Result[i];
            double fp = _fast.Result[i - 1], sp = _slow.Result[i - 1];
            double h = Bars.HighPrices[i], l = Bars.LowPrices[i], c = Bars.ClosePrices[i];

            bool up = f > s && fp <= sp, dn = f < s && fp >= sp;
            if (up || dn)
            {
                _dir = up ? 1 : -1;
                _used = false;
                _ext = up ? h : l;
                bool trendOk = !UseTrend || _dir * (c - _trend.Result[i]) > 0;
                _state = trendOk ? 1 : 0;
                if (live) Print("SMA cross {0}{1}", up ? "UP" : "DOWN", trendOk ? " -> waiting for move" : " (against SMA200, ignored)");
                return;
            }
            if (_state == 0 || _used) return;

            int d = _dir;
            double line = Touch == TouchMa.Sma9 ? f : s;
            switch (_state)
            {
                case 1:   // move away from the MA
                    _ext = d == 1 ? Math.Max(_ext, h) : Math.Min(_ext, l);
                    if (d * (_ext - f) >= MoveAtr * _atr.Result[i])
                    {
                        _state = 2;
                        if (live) Print("Move done -> waiting for pullback to {0}", Touch);
                    }
                    break;

                case 2:   // pullback touches the MA
                    if ((d == 1 && l <= line) || (d == -1 && h >= line))
                    {
                        if (d * (c - s) < 0) { _state = 0; if (live) Print("Pullback closed through SMA {0} -> setup cancelled", SlowPeriod); break; }
                        _state = 3; _pbExt = d == 1 ? l : h; _touchBar = i;
                        if (live) Print("Pullback touched {0} -> waiting for bounce", Touch);
                    }
                    break;

                case 3:   // bounce
                    _pbExt = d == 1 ? Math.Min(_pbExt, l) : Math.Max(_pbExt, h);
                    if (d * (c - s) < 0 || i - _touchBar > MaxPbBars)
                    {
                        _state = 0;
                        if (live) Print("Setup cancelled (closed through SMA {0} or no bounce in {1} candles)", SlowPeriod, MaxPbBars);
                        break;
                    }
                    bool bounce = d == 1 ? (c > Bars.HighPrices[i - 1] && c > f) : (c < Bars.LowPrices[i - 1] && c < f);
                    if (!bounce) break;
                    if (live) TryEnter(d, i);
                    else _state = 2;
                    break;
            }
        }

        private void TryEnter(int d, int i)
        {
            var tt = d == 1 ? TradeType.Buy : TradeType.Sell;
            int hr = Bars.OpenTimes[i + 1].Hour;
            if (hr < StartHour || hr >= EndHour) { Print("{0} bounce outside trading hours, skipped.", tt); _state = 2; return; }
            if (Positions.FindAll(Label, SymbolName).Any()) { Print("{0} bounce skipped: position already open.", tt); _state = 2; return; }
            if (_tradesToday >= MaxTradesPerDay) { Print("{0} skipped: max trades today.", tt); _state = 2; return; }
            if (MaxSpreadPips > 0 && Symbol.Spread / Symbol.PipSize > MaxSpreadPips) { Print("{0} skipped: spread {1:F1} pips.", tt, Symbol.Spread / Symbol.PipSize); _state = 2; return; }

            double px = d == 1 ? Symbol.Ask : Symbol.Bid;
            double sl = _pbExt - d * BufferPips * Symbol.PipSize;
            double slPips = d * (px - sl) / Symbol.PipSize;
            if (slPips < MinSlPips || slPips > MaxSlPips) { Print("{0} skipped: SL {1:F1} pips outside {2}-{3}.", tt, slPips, MinSlPips, MaxSlPips); _state = 2; return; }

            double units = Size(slPips, tt);
            if (units <= 0) { _state = 2; return; }

            var r = ExecuteMarketOrder(tt, SymbolName, units, Label, Math.Round(slPips, 1), Math.Round(slPips * RR, 1), "SMA pullback");
            Print("{0} {1} lots | SL {2:F1} pips | TP {3:F1} pips | {4}", tt, Symbol.VolumeInUnitsToQuantity(units), slPips, slPips * RR,
                r.IsSuccessful ? "ok" : r.Error.ToString());
            if (r.IsSuccessful) { _used = true; _state = 0; _tradesToday++; }
            else _state = 2;
        }

        private double Size(double slPips, TradeType tt)
        {
            double riskMoney = Account.Equity * RiskPct / 100.0;
            double units = Symbol.NormalizeVolumeInUnits(riskMoney / (slPips * Symbol.PipValue), RoundingMode.Down);
            if (units < Symbol.VolumeInUnitsMin)
            {
                if (!UseMinLot) { Print("{0} skipped: risk too small for min lot.", tt); return 0; }
                units = Symbol.VolumeInUnitsMin;
                double realPct = slPips * Symbol.PipValue * units / Account.Equity * 100.0;
                if (MaxRiskAtMinLot > 0 && realPct > MaxRiskAtMinLot)
                {
                    Print("{0} skipped: min lot would risk {1:F1}% (> {2}%).", tt, realPct, MaxRiskAtMinLot);
                    return 0;
                }
            }
            return Math.Min(units, Symbol.VolumeInUnitsMax);
        }
    }
}
