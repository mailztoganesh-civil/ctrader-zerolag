// ZeroLagTrendBot - cTrader cBot port of
// "Zero Lag Trend Signals (MTF) [AlgoAlpha]"
// Original Pine Script(TM) (c) AlgoAlpha.
// This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
// https://mozilla.org/MPL/2.0/

using System;
using cAlgo.API;
using cAlgo.API.Indicators;
using cAlgo.API.Internals;

namespace cAlgo.Robots
{
    public enum EntryMode { TrendFlip, Pullback, Both }
    public enum StopMode { Band, ATR, FixedPips }
    public enum SizeMode { FixedLots, RiskPercent }

    [Robot(AccessRights = AccessRights.None, TimeZone = TimeZones.UTC, AddIndicators = false)]
    public class ZeroLagTrendBot : Robot
    {
        // ---------------- Main (same defaults as the Pine script) ----------------
        [Parameter("Length", Group = "Main", DefaultValue = 70, MinValue = 2)]
        public int Length { get; set; }

        [Parameter("Band Multiplier", Group = "Main", DefaultValue = 1.2, MinValue = 0.1)]
        public double Mult { get; set; }

        [Parameter("History Bars", Group = "Main", DefaultValue = 1500, MinValue = 400)]
        public int CalcBars { get; set; }

        // ---------------- Entries ----------------
        [Parameter("Entry Mode", Group = "Entries", DefaultValue = EntryMode.TrendFlip)]
        public EntryMode Mode { get; set; }

        [Parameter("Allow Buys", Group = "Entries", DefaultValue = true)]
        public bool AllowLong { get; set; }

        [Parameter("Allow Sells", Group = "Entries", DefaultValue = true)]
        public bool AllowShort { get; set; }

        [Parameter("Close On Opposite Flip", Group = "Entries", DefaultValue = true)]
        public bool CloseOnOpposite { get; set; }

        [Parameter("Max Positions", Group = "Entries", DefaultValue = 1, MinValue = 1)]
        public int MaxPositions { get; set; }

        // ---------------- Multi-timeframe filter ----------------
        [Parameter("Use TF1", Group = "MTF Filter", DefaultValue = false)]
        public bool UseTF1 { get; set; }
        [Parameter("TF1", Group = "MTF Filter", DefaultValue = "Minute5")]
        public TimeFrame TF1 { get; set; }

        [Parameter("Use TF2", Group = "MTF Filter", DefaultValue = false)]
        public bool UseTF2 { get; set; }
        [Parameter("TF2", Group = "MTF Filter", DefaultValue = "Minute15")]
        public TimeFrame TF2 { get; set; }

        [Parameter("Use TF3", Group = "MTF Filter", DefaultValue = false)]
        public bool UseTF3 { get; set; }
        [Parameter("TF3", Group = "MTF Filter", DefaultValue = "Hour")]
        public TimeFrame TF3 { get; set; }

        [Parameter("Use TF4", Group = "MTF Filter", DefaultValue = false)]
        public bool UseTF4 { get; set; }
        [Parameter("TF4", Group = "MTF Filter", DefaultValue = "Hour4")]
        public TimeFrame TF4 { get; set; }

        [Parameter("Use TF5", Group = "MTF Filter", DefaultValue = false)]
        public bool UseTF5 { get; set; }
        [Parameter("TF5", Group = "MTF Filter", DefaultValue = "Daily")]
        public TimeFrame TF5 { get; set; }

        // ---------------- Risk ----------------
        [Parameter("Size Mode", Group = "Risk", DefaultValue = SizeMode.RiskPercent)]
        public SizeMode Sizing { get; set; }

        [Parameter("Fixed Lots", Group = "Risk", DefaultValue = 0.01, MinValue = 0.01)]
        public double FixedLotSize { get; set; }

        [Parameter("Risk % per Trade", Group = "Risk", DefaultValue = 1.0, MinValue = 0.1)]
        public double RiskPct { get; set; }

        [Parameter("Stop Mode", Group = "Risk", DefaultValue = StopMode.Band)]
        public StopMode SLMode { get; set; }

        [Parameter("ATR Multiplier (ATR mode)", Group = "Risk", DefaultValue = 1.5, MinValue = 0.1)]
        public double AtrMult { get; set; }

        [Parameter("Fixed SL Pips (Fixed mode)", Group = "Risk", DefaultValue = 20, MinValue = 1)]
        public double SLPips { get; set; }

        [Parameter("Min SL Pips", Group = "Risk", DefaultValue = 5, MinValue = 0)]
        public double MinSLPips { get; set; }

        [Parameter("Max SL Pips (0 = off)", Group = "Risk", DefaultValue = 50, MinValue = 0)]
        public double MaxSLPips { get; set; }

        [Parameter("Reward:Risk (0 = no TP)", Group = "Risk", DefaultValue = 2.0, MinValue = 0)]
        public double RR { get; set; }

        // ---------------- Filters ----------------
        [Parameter("Max Spread Pips (0 = off)", Group = "Filters", DefaultValue = 2.0, MinValue = 0)]
        public double MaxSpreadPips { get; set; }

        [Parameter("Use Trading Hours (UTC)", Group = "Filters", DefaultValue = false)]
        public bool UseSession { get; set; }

        [Parameter("Start Hour UTC", Group = "Filters", DefaultValue = 6, MinValue = 0, MaxValue = 23)]
        public int StartHour { get; set; }

        [Parameter("End Hour UTC", Group = "Filters", DefaultValue = 18, MinValue = 0, MaxValue = 23)]
        public int EndHour { get; set; }

        [Parameter("Label", Group = "Misc", DefaultValue = "ZeroLag")]
        public string Label { get; set; }

        // ---------------- State ----------------
        private bool[] _use;
        private TimeFrame[] _tfs;
        private Bars[] _mtfBars;
        private AverageTrueRange _atr;

        protected override void OnStart()
        {
            _use = new[] { UseTF1, UseTF2, UseTF3, UseTF4, UseTF5 };
            _tfs = new[] { TF1, TF2, TF3, TF4, TF5 };
            _mtfBars = new Bars[5];

            EnsureHistory(Bars);
            for (int k = 0; k < 5; k++)
            {
                if (!_use[k]) continue;
                _mtfBars[k] = MarketData.GetBars(_tfs[k]);
                EnsureHistory(_mtfBars[k]);
            }

            _atr = Indicators.AverageTrueRange(14, MovingAverageType.Simple);
            Print("ZeroLag bot started on {0} {1}", SymbolName, TimeFrame);
        }

        private void EnsureHistory(Bars b)
        {
            int tries = 0;
            while (b.Count < CalcBars + 2 && tries < 10)
            {
                int loaded = b.LoadMoreHistory();
                if (loaded <= 0) break;
                tries++;
            }
        }

        // Builds the Zero-Lag trend state on CLOSED bars. Arrays oldest-first;
        // last element = last closed bar. Returns bar count or 0 if not enough data.
        private int Calc(Bars b, out double[] cl, out double[] zl, out double[] vol, out int[] trd)
        {
            cl = null; zl = null; vol = null; trd = null;

            int closed = b.Count - 1;                 // exclude the forming bar
            int n = Math.Min(CalcBars, closed);
            int L = Length;
            int lag = (L - 1) / 2;
            int w = L * 3;
            int emaStart = lag + L - 1;
            int volStart = (L - 1) + (w - 1);
            int start = Math.Max(emaStart, volStart) + 1;
            if (n < start + 50) return 0;

            int off = closed - n;
            cl = new double[n];
            zl = new double[n];
            vol = new double[n];
            trd = new int[n];
            var hi = new double[n];
            var lo = new double[n];
            var zs = new double[n];
            var tr = new double[n];
            var atr = new double[n];

            for (int i = 0; i < n; i++)
            {
                cl[i] = b.ClosePrices[off + i];
                hi[i] = b.HighPrices[off + i];
                lo[i] = b.LowPrices[off + i];
            }

            // zero-lag source: src + (src - src[lag])
            for (int i = lag; i < n; i++)
                zs[i] = cl[i] + (cl[i] - cl[i - lag]);

            // EMA with SMA seed (Pine ta.ema)
            double alpha = 2.0 / (L + 1.0);
            double sum = 0;
            for (int i = lag; i <= emaStart; i++) sum += zs[i];
            zl[emaStart] = sum / L;
            for (int i = emaStart + 1; i < n; i++)
                zl[i] = alpha * zs[i] + (1.0 - alpha) * zl[i - 1];

            // ATR = RMA of true range (Pine ta.atr)
            tr[0] = hi[0] - lo[0];
            for (int i = 1; i < n; i++)
                tr[i] = Math.Max(hi[i] - lo[i], Math.Max(Math.Abs(hi[i] - cl[i - 1]), Math.Abs(lo[i] - cl[i - 1])));
            sum = 0;
            for (int i = 0; i < L; i++) sum += tr[i];
            atr[L - 1] = sum / L;
            for (int i = L; i < n; i++)
                atr[i] = (atr[i - 1] * (L - 1) + tr[i]) / L;

            // volatility = highest(ATR, L*3) * mult
            for (int i = volStart; i < n; i++)
            {
                double h = 0;
                for (int k = i - w + 1; k <= i; k++)
                    if (atr[k] > h) h = atr[k];
                vol[i] = h * Mult;
            }

            // trend state
            int t = 0;
            for (int i = start; i < n; i++)
            {
                double up0 = zl[i] + vol[i], up1 = zl[i - 1] + vol[i - 1];
                double dn0 = zl[i] - vol[i], dn1 = zl[i - 1] - vol[i - 1];
                if (cl[i] > up0 && cl[i - 1] <= up1) t = 1;
                if (cl[i] < dn0 && cl[i - 1] >= dn1) t = -1;
                trd[i] = t;
            }
            return n;
        }

        protected override void OnBar()
        {
            if (Calc(Bars, out var c, out var z, out var v, out var t) == 0)
            {
                Print("Not enough history yet");
                return;
            }

            int i = c.Length - 1, j = c.Length - 2;
            bool flipUp = t[i] == 1 && t[j] <= 0;
            bool flipDn = t[i] == -1 && t[j] >= 0;
            bool pbUp = t[i] == 1 && t[j] == 1 && c[i] > z[i] && c[j] <= z[j];
            bool pbDn = t[i] == -1 && t[j] == -1 && c[i] < z[i] && c[j] >= z[j];

            // MTF filter values
            var mtf = new int[5];
            var ok = new bool[5];
            string status = string.Format("{0} {1}: {2}", SymbolName, TimeFrame, t[i] == 1 ? "Bullish" : "Bearish");
            for (int k = 0; k < 5; k++)
            {
                if (!_use[k]) continue;
                if (Calc(_mtfBars[k], out _, out _, out _, out var t2) > 0)
                {
                    ok[k] = true;
                    mtf[k] = t2[t2.Length - 1];
                }
                status += string.Format(" | {0}: {1}", _tfs[k], !ok[k] ? "no data" : (mtf[k] == 1 ? "Bullish" : "Bearish"));
            }
            Print(status);

            if (flipUp || flipDn)
                Print("Trend flipped {0}", flipUp ? "BULLISH" : "BEARISH");

            // exits on opposite flip
            if (CloseOnOpposite)
            {
                if (flipDn) CloseAll(TradeType.Buy, "bearish flip");
                if (flipUp) CloseAll(TradeType.Sell, "bullish flip");
            }

            bool longSig = (Mode != EntryMode.Pullback && flipUp) || (Mode != EntryMode.TrendFlip && pbUp);
            bool shortSig = (Mode != EntryMode.Pullback && flipDn) || (Mode != EntryMode.TrendFlip && pbDn);

            if (longSig && AllowLong)
            {
                if (MtfAllows(1, mtf, ok)) Open(TradeType.Buy, z[i], v[i], flipUp ? "bull flip" : "bull pullback");
                else Print("Long signal blocked by MTF filter");
            }
            if (shortSig && AllowShort)
            {
                if (MtfAllows(-1, mtf, ok)) Open(TradeType.Sell, z[i], v[i], flipDn ? "bear flip" : "bear pullback");
                else Print("Short signal blocked by MTF filter");
            }
        }

        private bool MtfAllows(int dir, int[] mtf, bool[] ok)
        {
            for (int k = 0; k < 5; k++)
            {
                if (!_use[k]) continue;
                if (!ok[k] || mtf[k] != dir) return false;
            }
            return true;
        }

        private void CloseAll(TradeType type, string why)
        {
            foreach (var p in Positions.FindAll(Label, SymbolName, type))
            {
                var r = ClosePosition(p);
                if (r.IsSuccessful) Print("Closed {0} {1} ({2}), net P/L {3}", type, SymbolName, why, p.NetProfit);
                else Print("Close failed: {0}", r.Error);
            }
        }

        private bool SessionOk()
        {
            if (!UseSession) return true;
            int h = Server.Time.Hour;
            if (StartHour <= EndHour) return h >= StartHour && h < EndHour;
            return h >= StartHour || h < EndHour;   // overnight window
        }

        private double CalcUnits(double slPips)
        {
            double units;
            if (Sizing == SizeMode.FixedLots)
                units = Symbol.QuantityToVolumeInUnits(FixedLotSize);
            else
            {
                double riskMoney = Account.Equity * RiskPct / 100.0;
                double perUnit = slPips * Symbol.PipValue;
                if (perUnit <= 0) return 0;
                units = riskMoney / perUnit;
            }

            units = Symbol.NormalizeVolumeInUnits(units, RoundingMode.Down);
            if (units < Symbol.VolumeInUnitsMin)
                return Sizing == SizeMode.RiskPercent ? 0 : Symbol.VolumeInUnitsMin;   // never exceed chosen risk
            if (units > Symbol.VolumeInUnitsMax) units = Symbol.VolumeInUnitsMax;
            return units;
        }

        private void Open(TradeType type, double zlv, double volv, string why)
        {
            if (Positions.FindAll(Label, SymbolName).Length >= MaxPositions) return;
            if (!SessionOk()) { Print("Outside trading hours, skipped: {0}", why); return; }

            double spreadPips = Symbol.Spread / Symbol.PipSize;
            if (MaxSpreadPips > 0 && spreadPips > MaxSpreadPips)
            {
                Print("Spread {0:F1} pips > max, skipped: {1}", spreadPips, why);
                return;
            }

            bool isBuy = type == TradeType.Buy;
            double entry = isBuy ? Symbol.Ask : Symbol.Bid;
            double sl;
            switch (SLMode)
            {
                case StopMode.Band:
                    sl = isBuy ? zlv - volv : zlv + volv;
                    break;
                case StopMode.ATR:
                    double a = _atr.Result.Last(1);
                    sl = isBuy ? entry - a * AtrMult : entry + a * AtrMult;
                    break;
                default:
                    sl = isBuy ? entry - SLPips * Symbol.PipSize : entry + SLPips * Symbol.PipSize;
                    break;
            }

            if ((isBuy && sl >= entry) || (!isBuy && sl <= entry))
            {
                Print("SL on wrong side of price, skipped: {0}", why);
                return;
            }

            double slPips = Math.Abs(entry - sl) / Symbol.PipSize;
            if (MinSLPips > 0 && slPips < MinSLPips) slPips = MinSLPips;
            if (MaxSLPips > 0 && slPips > MaxSLPips)
            {
                Print("SL {0:F1} pips > max {1}, skipped: {2}", slPips, MaxSLPips, why);
                return;
            }

            double? tpPips = RR > 0 ? Math.Round(slPips * RR, 1) : (double?)null;
            double units = CalcUnits(slPips);
            if (units <= 0)
            {
                Print("Volume below broker minimum for this risk %, skipped: {0}", why);
                return;
            }

            var res = ExecuteMarketOrder(type, SymbolName, units, Label, Math.Round(slPips, 1), tpPips, "ZL " + why);
            if (res.IsSuccessful)
                Print("{0} {1} {2} units | SL {3:F1} pips | TP {4} | {5}", type, SymbolName, units, slPips,
                      tpPips.HasValue ? tpPips.Value.ToString("F1") + " pips" : "none", why);
            else
                Print("Order failed: {0}", res.Error);
        }
    }
}
