"""
Gold (XAUUSD) ROI Maximization Engine
Tests systematic levers to drastically increase ROI WITHOUT increasing risk:
1. Dynamic Runner Target: 2.0R, 2.5R, 3.0R, 3.5R, 4.0R, or Trailing 5M 20 EMA
2. Asymmetric Partial Ratio: 50/50, 40/60, 30/70
3. Target 1 (Lock-in) level: 1.2R, 1.3R, 1.5R
4. Stop Loss multiplier: 0.75x, 0.85x, 0.90x, 1.00x ATR
5. Pyramiding / Re-entry when first trade is 100% Risk-Free at Breakeven
"""

from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from dcc_engine import DCCEngine, SignalType
from mt5_data import MT5DataProvider


class GoldTuningLab:
    def __init__(self, data_provider: Optional[MT5DataProvider] = None):
        self.dp = data_provider if data_provider is not None else MT5DataProvider()
        self.df_eval = None
        self.contract_size = 100.0  # 100 oz per lot

    def load_data(self, start_date: datetime, end_date: datetime):
        print(f"[GoldTuningLab] Loading XAUUSD M5, H1, H2 data from MT5...")
        df_m5, df_1h, df_2h = self.dp.fetch_multi_timeframe_rates("XAUUSD", start_date, end_date, warmup_days=20)
        base_engine = DCCEngine(
            london_session_start=time(6, 0),
            london_session_end=time(21, 0),
            ny_session_start=time(6, 0),
            ny_session_end=time(21, 0),
            eod_exit_time=time(21, 0)
        )
        df_prep = base_engine.prepare_data(df_m5, df_1h, df_2h)
        eval_mask = (df_prep.index >= pd.Timestamp(start_date, tz=timezone.utc)) & \
                    (df_prep.index <= pd.Timestamp(end_date, tz=timezone.utc))
        self.df_eval = df_prep.loc[eval_mask]
        print(f"[GoldTuningLab] Precomputed {len(self.df_eval)} 5M bars for Gold")

    def simulate(
        self,
        atr_multiplier: float = 0.9,
        tp1_rr: float = 1.5,
        tp2_rr: float = 2.0,
        partial_ratio: float = 0.5,  # Portion closed at TP1
        adx_min: float = 15.0,
        allow_risk_free_pyramid: bool = False,  # Allow 2nd trade ONLY when 1st is safe at BE
        use_trail_runner: bool = False,  # Trail runner behind 5M 20 EMA
        risk_per_trade: float = 0.01,
        initial_balance: float = 100_000.0
    ) -> Dict:
        df = self.df_eval
        spread_price = 0.30  # Actual average spread on XAUUSD in OctaFX
        n_bars = len(df)

        times = df.index
        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values

        m5_e9 = df['ema9_5m'].values
        m5_e20 = df['ema20_5m'].values
        vwap = df['vwap_5m'].values

        h1_bias = df['bias_1h'].values
        h1_e20 = df['ema20_1h'].values
        h1_adx = df['adx_1h'].values
        h1_atr = df['atr_1h'].values

        # 1. Extract signals
        signals = []
        for i in range(1, n_bars):
            t = times[i].time()
            if not (time(6, 0) <= t < time(20, 0)):
                continue

            bias = h1_bias[i]
            if bias == 0 or h1_adx[i] < adx_min:
                continue

            prev_diff = m5_e9[i - 1] - m5_e20[i - 1]
            curr_diff = m5_e9[i] - m5_e20[i]
            c = closes[i]

            sig_type = 0
            if bias == 1 and prev_diff <= 0 and curr_diff > 0:
                if c > vwap[i] and c > h1_e20[i]:
                    sig_type = 1
            elif bias == -1 and prev_diff >= 0 and curr_diff < 0:
                if c < vwap[i] and c < h1_e20[i]:
                    sig_type = -1

            if sig_type != 0:
                sl_dist = h1_atr[i] * atr_multiplier
                signals.append((i, times[i], sig_type, sl_dist))

        # 2. Simulate trades
        trades = []
        balance = initial_balance
        last_exit_idx = -1
        # When allow_risk_free_pyramid is True: can enter 2nd trade if 1st is risk-free (TP1 hit)
        active_trade_safe_at = -1  # Bar index where active trade became safe

        for sig_idx, sig_time, sig_type, sl_dist in signals:
            # Check overlap restriction
            if not allow_risk_free_pyramid:
                if sig_idx <= last_exit_idx:
                    continue
            else:
                # If active trade has NOT yet reached TP1 (still carrying risk), cannot open new trade
                if sig_idx <= last_exit_idx and sig_idx <= active_trade_safe_at:
                    continue

            if sig_idx + 1 >= n_bars:
                break

            entry_p = opens[sig_idx + 1] + (spread_price if sig_type == 1 else -spread_price)
            risk_amt = balance * risk_per_trade
            lots = max(0.02, round(risk_amt / (sl_dist * self.contract_size), 2))
            p_lots = round(lots * partial_ratio, 2)
            r_lots = round(lots - p_lots, 2)

            if sig_type == 1:
                sl_p = entry_p - sl_dist
                tp1_p = entry_p + (tp1_rr * sl_dist)
                tp2_p = entry_p + (tp2_rr * sl_dist)
            else:
                sl_p = entry_p + sl_dist
                tp1_p = entry_p - (tp1_rr * sl_dist)
                tp2_p = entry_p - (tp2_rr * sl_dist)

            tp1_hit = False
            curr_sl = sl_p
            p_exit = entry_p
            r_exit = entry_p
            trade_exit = sig_idx + 1

            for j in range(sig_idx + 1, min(n_bars, sig_idx + 120)):
                h = highs[j]
                l = lows[j]
                cl = closes[j]
                trade_exit = j
                bt = times[j].time()

                if sig_type == 1:
                    if not tp1_hit:
                        if h >= tp1_p:
                            tp1_hit = True
                            p_exit = tp1_p
                            curr_sl = entry_p + spread_price  # Breakeven!
                            active_trade_safe_at = j
                        if l <= curr_sl:
                            p_exit = curr_sl if not tp1_hit else p_exit
                            r_exit = curr_sl
                            break
                    else:
                        # Runner management
                        if use_trail_runner:
                            # Trail behind 5M 20 EMA once in profit
                            trail_level = m5_e20[j] - spread_price
                            curr_sl = max(curr_sl, trail_level)
                            if l <= curr_sl:
                                r_exit = curr_sl
                                break
                            elif h >= tp2_p:  # Maximum cap target
                                r_exit = tp2_p
                                break
                        else:
                            if h >= tp2_p:
                                r_exit = tp2_p
                                break
                            elif l <= curr_sl:
                                r_exit = curr_sl
                                break

                    if bt >= time(21, 0):
                        if not tp1_hit:
                            p_exit = cl
                        r_exit = cl
                        break

                else:  # Short
                    if not tp1_hit:
                        if l <= tp1_p:
                            tp1_hit = True
                            p_exit = tp1_p
                            curr_sl = entry_p - spread_price  # Breakeven!
                            active_trade_safe_at = j
                        if h >= curr_sl:
                            p_exit = curr_sl if not tp1_hit else p_exit
                            r_exit = curr_sl
                            break
                    else:
                        if use_trail_runner:
                            trail_level = m5_e20[j] + spread_price
                            curr_sl = min(curr_sl, trail_level)
                            if h >= curr_sl:
                                r_exit = curr_sl
                                break
                            elif l <= tp2_p:
                                r_exit = tp2_p
                                break
                        else:
                            if l <= tp2_p:
                                r_exit = tp2_p
                                break
                            elif h >= curr_sl:
                                r_exit = curr_sl
                                break

                    if bt >= time(21, 0):
                        if not tp1_hit:
                            p_exit = cl
                        r_exit = cl
                        break

            last_exit_idx = trade_exit

            if sig_type == 1:
                p_gross = (p_exit - entry_p) * p_lots * self.contract_size
                r_gross = (r_exit - entry_p) * r_lots * self.contract_size
            else:
                p_gross = (entry_p - p_exit) * p_lots * self.contract_size
                r_gross = (entry_p - r_exit) * r_lots * self.contract_size

            comm = lots * 5.0
            net_pnl = p_gross + r_gross - comm
            balance += net_pnl
            trades.append(net_pnl)

        if not trades:
            return {"total_trades": 0, "net_pnl": 0.0, "roi": 0.0, "win_rate": 0.0, "pf": 0.0, "max_dd": 0.0}

        t_arr = np.array(trades)
        wins = t_arr[t_arr > 0]
        losses = t_arr[t_arr <= 0]
        wr = len(wins) / len(t_arr) * 100.0

        gp = wins.sum() if len(wins) > 0 else 0.0
        gl = abs(losses.sum()) if len(losses) > 0 else 0.0
        pf = gp / gl if gl > 0 else 999.0

        cum_eq = initial_balance + np.cumsum(t_arr)
        cum_max = np.maximum.accumulate(cum_eq)
        max_dd = np.max((cum_max - cum_eq) / cum_max * 100.0)
        net_pnl = balance - initial_balance
        roi = (net_pnl / initial_balance) * 100.0

        return {
            "total_trades": len(t_arr),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": wr,
            "profit_factor": pf,
            "net_pnl": net_pnl,
            "roi": roi,
            "max_dd": max_dd,
            "atr_mult": atr_multiplier,
            "tp1_rr": tp1_rr,
            "tp2_rr": tp2_rr,
            "partial_ratio": partial_ratio,
            "pyramid": allow_risk_free_pyramid,
            "trail": use_trail_runner
        }


def run_gold_optimization():
    start_dt = datetime(2026, 7, 1)
    end_dt = datetime(2026, 8, 31, 23, 59, 59)

    lab = GoldTuningLab()
    lab.load_data(start_dt, end_dt)

    print("\n" + "=" * 90)
    print("EXPERIMENT 1: RUNNER TARGET EXPANSION (LETTING RUNNERS RUN TO 2.5R, 3.0R, 3.5R)")
    print("=" * 90)
    print(f"{'Config':30s} | {'Trades':6s} | {'Win Rate':8s} | {'PF':4s} | {'Net Profit':12s} | {'ROI':7s} | {'Max DD':6s}")
    print("-" * 90)

    # Test baseline (TP1=1.5R, TP2=2.0R, 50/50, strict single position)
    base = lab.simulate(atr_multiplier=0.9, tp1_rr=1.5, tp2_rr=2.0, partial_ratio=0.5)
    print(f"{'Baseline (1.5R TP1 / 2.0R TP2)':30s} | {base['total_trades']:2d}     | {base['win_rate']:5.1f}%   | {base['profit_factor']:4.2f} | ${base['net_pnl']:+10,.2f} | {base['roi']:+6.2f}% | {base['max_dd']:4.2f}%")

    # Test extending TP2
    for tp2 in [2.2, 2.5, 2.8, 3.0, 3.5]:
        res = lab.simulate(atr_multiplier=0.9, tp1_rr=1.5, tp2_rr=tp2, partial_ratio=0.5)
        print(f"{f'Extended Runner (1.5R / {tp2}R)':30s} | {res['total_trades']:2d}     | {res['win_rate']:5.1f}%   | {res['profit_factor']:4.2f} | ${res['net_pnl']:+10,.2f} | {res['roi']:+6.2f}% | {res['max_dd']:4.2f}%")

    print("\n" + "=" * 90)
    print("EXPERIMENT 2: ASYMMETRIC PARTIAL RATIO (BANK 40% AT 1.5R, LET 60% RUN)")
    print("=" * 90)
    for p_ratio in [0.4, 0.35, 0.3]:
        for tp2 in [2.5, 3.0]:
            res = lab.simulate(atr_multiplier=0.9, tp1_rr=1.5, tp2_rr=tp2, partial_ratio=p_ratio)
            p_desc = f"{int(p_ratio*100)}% TP1(1.5R) / {int((1-p_ratio)*100)}% TP2({tp2}R)"
            print(f"{p_desc:30s} | {res['total_trades']:2d}     | {res['win_rate']:5.1f}%   | {res['profit_factor']:4.2f} | ${res['net_pnl']:+10,.2f} | {res['roi']:+6.2f}% | {res['max_dd']:4.2f}%")

    print("\n" + "=" * 90)
    print("EXPERIMENT 3: 5M 20-EMA TRAILING RUNNER (SURF THE ENTIRE TREND)")
    print("=" * 90)
    for tp1 in [1.2, 1.5]:
        for cap in [3.0, 4.0, 5.0]:
            res = lab.simulate(atr_multiplier=0.9, tp1_rr=tp1, tp2_rr=cap, partial_ratio=0.5, use_trail_runner=True)
            desc = f"Trail 20EMA (TP1={tp1}R / Cap={cap}R)"
            print(f"{desc:30s} | {res['total_trades']:2d}     | {res['win_rate']:5.1f}%   | {res['profit_factor']:4.2f} | ${res['net_pnl']:+10,.2f} | {res['roi']:+6.2f}% | {res['max_dd']:4.2f}%")

    print("\n" + "=" * 90)
    print("EXPERIMENT 4: RISK-FREE PYRAMIDING (Add-on ONLY when Trade 1 is 100% Safe at Breakeven)")
    print("=" * 90)
    for tp2 in [2.0, 2.5, 3.0]:
        res = lab.simulate(atr_multiplier=0.9, tp1_rr=1.5, tp2_rr=tp2, partial_ratio=0.5, allow_risk_free_pyramid=True)
        desc = f"Risk-Free Addon (1.5R / {tp2}R)"
        print(f"{desc:30s} | {res['total_trades']:2d}     | {res['win_rate']:5.1f}%   | {res['profit_factor']:4.2f} | ${res['net_pnl']:+10,.2f} | {res['roi']:+6.2f}% | {res['max_dd']:4.2f}%")

    print("\n" + "=" * 90)
    print("EXPERIMENT 5: TUNED MASTER COMBO (THE ULTIMATE GOLD SETUP)")
    print("=" * 90)
    # Master combo: 40% partial at 1.5R + 60% runner at 2.8R + Risk-Free Addon + 0.85 ATR
    master = lab.simulate(
        atr_multiplier=0.85,
        tp1_rr=1.5,
        tp2_rr=2.8,
        partial_ratio=0.4,
        allow_risk_free_pyramid=True
    )
    print(f"{'ULTIMATE GOLD MASTER COMBO':30s} | {master['total_trades']:2d}     | {master['win_rate']:5.1f}%   | {master['profit_factor']:4.2f} | ${master['net_pnl']:+10,.2f} | {master['roi']:+6.2f}% | {master['max_dd']:4.2f}%")
    print("=" * 90)


if __name__ == "__main__":
    run_gold_optimization()
