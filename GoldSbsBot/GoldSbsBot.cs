// GoldSbsBot - Swing Breakout Sequence (SBS) for XAUUSD, confirmation entry (cTrader cBot)
// Same rules as the 2-year backtest (Dukascopy M5, Nov 2024 - Nov 2026):
//   swing 3, target 3R -> 330 trades, PF 1.48, +102R; first 16 months PF 1.44, last 8 months PF 1.57.
//
// Bullish sequence (bearish = exact mirror):
//   Swing high/low = candle with 'Swing Strength' lower highs/higher lows on each side
//                    (confirmed 'Swing Strength' candles later).
//   P1  a candle CLOSES above the last confirmed swing high. Start of move = lowest low
//       from that swing high to the break.
//   P2  first confirmed swing low after P1.
//   P3  price makes a new high >= the highest high between P1 and P2 confirmation
//       (if price trades below P2 first -> sequence cancelled).
//   P4  price trades below P2 (sweep). Lowest low after that = sweep low.
//   P5  a confirmed swing high forms after the sweep low -> BUY-STOP at that swing high.
//   SL  at the sweep low, TP = RR x risk. A new lower low cancels the pending order and
//       waits for a new P5. Sequence cancelled if price breaks the start of the move or
//       after 'Max Sequence Bars'. One trade at a time. Trade closed after 2 x Max Sequence Bars.

using System;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    [Robot(AccessRights = AccessRights.None, TimeZone = TimeZones.UTC, AddIndicators = false)]
    public class GoldSbsBot : Robot
    {
        [Parameter("Risk % per Trade", Group = "Risk", DefaultValue = 1.0, MinValue = 0.1, MaxValue = 10)]
        public double RiskPct { get; set; }

        [Parameter("Reward:Risk (TP)", Group = "Risk", DefaultValue = 3.0, MinValue = 0.5)]
        public double RR { get; set; }

        [Parameter("Use Min Lot If Risk Too Small", Group = "Risk", DefaultValue = true)]
        public bool UseMinLot { get; set; }

        [Parameter("Max Risk % At Min Lot (0 = off)", Group = "Risk", DefaultValue = 5, MinValue = 0)]
        public double MaxRiskAtMinLot { get; set; }

        [Parameter("Min Stop in $", Group = "Risk", DefaultValue = 1.0, MinValue = 0)]
        public double MinStopUsd { get; set; }

        [Parameter("Stop Bot If Equity Below (0 = off)", Group = "Risk", DefaultValue = 0, MinValue = 0)]
        public double EquityStop { get; set; }

        [Parameter("Swing Strength", Group = "SBS", DefaultValue = 3, MinValue = 1, MaxValue = 10)]
        public int N { get; set; }

        [Parameter("Max Sequence Bars", Group = "SBS", DefaultValue = 150, MinValue = 20)]
        public int MaxBars { get; set; }

        [Parameter("Max Spread in $ (0 = off)", Group = "SBS", DefaultValue = 0.8, MinValue = 0)]
        public double MaxSpreadUsd { get; set; }

        [Parameter("Label", Group = "Misc", DefaultValue = "GoldSBS")]
        public string Label { get; set; }

        private class Seq
        {
            public int S;
            public int P1, P2i, P3i, Sw, L4i, Oi;
            public double Origin, P2, Hpre, P3, L4, Trig = double.NaN;
        }

        private readonly Seq[] _seq = { new Seq(), new Seq() };          // [0] = bull, [1] = bear
        private readonly int[] _shIdx = { -1, -1 };
        private readonly double[] _shLvl = { 0, 0 };
        private bool _live;
        private DateTime _entryBarTime = DateTime.MinValue;

        private static int K(int d) { return d == 1 ? 0 : 1; }
        private double Hd(int i, int d) { return d == 1 ? Bars.HighPrices[i] : -Bars.LowPrices[i]; }
        private double Ld(int i, int d) { return d == 1 ? Bars.LowPrices[i] : -Bars.HighPrices[i]; }
        private double Cd(int i, int d) { return d * Bars.ClosePrices[i]; }

        private bool PivH(int j, int d)
        {
            if (j - N < 0) return false;
            double v = Hd(j, d);
            for (int k = j - N; k < j; k++) if (!(Hd(k, d) < v)) return false;
            for (int k = j + 1; k <= j + N; k++) if (!(Hd(k, d) <= v)) return false;
            return true;
        }

        private bool PivL(int j, int d)
        {
            if (j - N < 0) return false;
            double v = Ld(j, d);
            for (int k = j - N; k < j; k++) if (!(Ld(k, d) > v)) return false;
            for (int k = j + 1; k <= j + N; k++) if (!(Ld(k, d) >= v)) return false;
            return true;
        }

        protected override void OnStart()
        {
            if (!SymbolName.ToUpper().Contains("XAU"))
                Print("WARNING: tested on gold (XAUUSD) only. Current symbol: {0}", SymbolName);
            if (TimeFrame != TimeFrame.Minute5)
                Print("WARNING: tested on the 5-minute chart. Current timeframe: {0}", TimeFrame);

            Positions.Opened += OnOpened;
            Positions.Closed += OnClosed;

            // warm-up: replay history so the sequence state is current (no trading)
            _live = false;
            for (int i = N + 1; i <= Bars.Count - 2; i++) Step(i);
            _live = true;
            CancelPending(0);
            ManagePending(Bars.Count - 2);

            Print("GoldSbsBot started on {0} {1}. Risk {2}%, swing {3}, TP {4}R. Bull state {5}, bear state {6}.",
                SymbolName, TimeFrame, RiskPct, N, RR, _seq[0].S, _seq[1].S);
        }

        protected override void OnBar()
        {
            int i = Bars.Count - 2;                      // last CLOSED candle
            if (i < 2 * N + 2) return;

            if (EquityStop > 0 && Account.Equity < EquityStop)
            {
                foreach (var p in Positions.FindAll(Label, SymbolName)) ClosePosition(p);
                CancelPending(0);
                Print("Equity {0:F2} below {1} -> bot stopped.", Account.Equity, EquityStop);
                Stop();
                return;
            }

            // time exit
            var pos = Positions.FindAll(Label, SymbolName).FirstOrDefault();
            if (pos != null)
            {
                int since = Bars.OpenTimes.GetIndexByTime(Bars.OpenTimes[i]) - Bars.OpenTimes.GetIndexByTime(pos.EntryTime);
                if (since > 2 * MaxBars) { ClosePosition(pos); Print("Time exit after {0} candles.", since); }
            }

            Step(i);
            ManagePending(i);
        }

        private void Reset(int d)
        {
            _seq[K(d)] = new Seq();
            if (_live) CancelPending(d);
        }

        private void Step(int i)
        {
            foreach (int d in new[] { 1, -1 })
            {
                var s = _seq[K(d)];
                int j = i - N;
                if (j >= N && PivH(j, d) && s.S == 0) { _shIdx[K(d)] = j; _shLvl[K(d)] = Hd(j, d); }

                if (s.S > 0 && (Ld(i, d) < s.Origin || i - s.P1 > MaxBars)) { Reset(d); s = _seq[K(d)]; }

                switch (s.S)
                {
                    case 0:
                        if (_shIdx[K(d)] >= 0 && Cd(i, d) > _shLvl[K(d)])
                        {
                            int j0 = _shIdx[K(d)];
                            double o = double.MaxValue;
                            for (int k = j0; k <= i; k++) o = Math.Min(o, Ld(k, d));
                            s.S = 1; s.P1 = i; s.Origin = o;
                            _shIdx[K(d)] = -1;
                        }
                        break;
                    case 1:
                        if (j > s.P1 && PivL(j, d))
                        {
                            double hp = double.MinValue;
                            for (int k = s.P1; k <= i; k++) hp = Math.Max(hp, Hd(k, d));
                            s.S = 2; s.P2 = Ld(j, d); s.P2i = j; s.Hpre = hp;
                        }
                        break;
                    case 2:
                        if (Ld(i, d) < s.P2) { Reset(d); break; }
                        if (Hd(i, d) >= s.Hpre) { s.S = 3; s.P3 = Hd(i, d); s.P3i = i; }
                        break;
                    case 3:
                        if (Hd(i, d) > s.P3) { s.P3 = Hd(i, d); s.P3i = i; }
                        if (Ld(i, d) < s.P2) { s.S = 4; s.Sw = i; s.L4 = Ld(i, d); s.L4i = i; s.Trig = double.NaN; }
                        break;
                    case 4:
                        if (Ld(i, d) < s.L4)
                        {
                            s.L4 = Ld(i, d); s.L4i = i;
                            if (!double.IsNaN(s.Trig) && _live) CancelPending(d);
                            s.Trig = double.NaN;
                        }
                        if (j > s.L4i && PivH(j, d)) s.Trig = Hd(j, d);
                        if (i - s.Sw > MaxBars) Reset(d);
                        break;
                }
            }
        }

        // keep a stop order at P5 for every armed sequence while no trade is open
        private void ManagePending(int i)
        {
            if (Positions.FindAll(Label, SymbolName).Any()) { CancelPending(0); return; }
            foreach (int d in new[] { 1, -1 })
            {
                var s = _seq[K(d)];
                if (s.S != 4 || double.IsNaN(s.Trig)) continue;
                double entry = d * s.Trig, sl = d * s.L4;
                double dist = Math.Abs(entry - sl);
                var tt = d == 1 ? TradeType.Buy : TradeType.Sell;
                var existing = PendingOrders.FirstOrDefault(o => o.Label == Label && o.SymbolName == SymbolName && o.TradeType == tt);
                if (existing != null && Math.Abs(existing.TargetPrice - entry) < Symbol.TickSize / 2) continue;
                if (existing != null) CancelPendingOrder(existing);
                if (dist < MinStopUsd) { Print("{0} setup skipped: stop only ${1:F2}.", tt, dist); _seq[K(d)] = new Seq(); continue; }

                double slPips = dist / Symbol.PipSize, tpPips = slPips * RR;
                double units = Size(slPips, tt);
                if (units <= 0) { _seq[K(d)] = new Seq(); continue; }

                double px = d == 1 ? Symbol.Ask : Symbol.Bid;
                bool already = d == 1 ? px >= entry : px <= entry;
                if (already)
                {
                    if (MaxSpreadUsd > 0 && Symbol.Spread > MaxSpreadUsd) { Print("{0} skipped: spread ${1:F2}.", tt, Symbol.Spread); continue; }
                    double slP = Math.Abs(px - sl) / Symbol.PipSize;
                    var r = ExecuteMarketOrder(tt, SymbolName, units, Label, Math.Round(slP, 1), Math.Round(slP * RR, 1), "SBS");
                    Print("SBS {0} at market (price already past P5): {1}", tt, r.IsSuccessful ? "ok" : r.Error.ToString());
                }
                else
                {
                    var r = PlaceStopOrder(tt, SymbolName, units, entry, Label, Math.Round(slPips, 1), Math.Round(tpPips, 1), null, "SBS");
                    Print("SBS {0} STOP at {1:F2} | SL {2:F2} (${3:F2}) | TP {4:F2} | {5} lots | {6}", tt, entry, sl, dist,
                        entry + d * dist * RR, Symbol.VolumeInUnitsToQuantity(units), r.IsSuccessful ? "placed" : r.Error.ToString());
                }
            }
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

        private void CancelPending(int d)
        {
            foreach (var o in PendingOrders.Where(o => o.Label == Label && o.SymbolName == SymbolName).ToArray())
                if (d == 0 || (d == 1 && o.TradeType == TradeType.Buy) || (d == -1 && o.TradeType == TradeType.Sell))
                    CancelPendingOrder(o);
        }

        private void OnOpened(PositionOpenedEventArgs a)
        {
            var p = a.Position;
            if (p.Label != Label || p.SymbolName != SymbolName) return;
            int d = p.TradeType == TradeType.Buy ? 1 : -1;
            _seq[K(d)] = new Seq();                      // sequence used
            CancelPending(0);                            // one trade at a time
            Print("SBS {0} opened at {1:F2}, SL {2}, TP {3}", p.TradeType, p.EntryPrice, p.StopLoss, p.TakeProfit);
        }

        private void OnClosed(PositionClosedEventArgs a)
        {
            var p = a.Position;
            if (p.Label != Label || p.SymbolName != SymbolName) return;
            Print("SBS {0} closed ({1}) net {2:F2}", p.TradeType, a.Reason, p.NetProfit);
        }
    }
}
