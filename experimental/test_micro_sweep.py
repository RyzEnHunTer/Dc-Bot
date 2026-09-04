"""
Micro-Sweep Experiment: Sweeping the low/high of the previous 1-4 bars directly at the EMA 20 test
"""

import os
import sys
from datetime import datetime
from typing import Tuple
import pandas as pd
import numpy as np

sys.path.insert(0, r"d:\FOREX\DC")
from mt5_data import MT5DataProvider

def test_micro_sweeps():
    dp = MT5DataProvider()
    start_date = datetime(2026, 1, 2)
    end_date = datetime(2026, 6, 30, 23, 59, 59)

    csv_path = r"d:\FOREX\DC\reports\trades_log_with_3pct_circuit_breaker.csv"
    df_trades = pd.read_csv(csv_path)
    df_trades['entry_time'] = pd.to_datetime(df_trades['entry_time'], format='ISO8601')

    rates_5m = {}
    for s in ["XAUUSD", "NAS100"]:
        df_m5, _, _ = dp.fetch_multi_timeframe_rates(s, start_date, end_date, warmup_days=20)
        rates_5m[s] = df_m5

    # Test micro sweep lookbacks: 1 bar (previous bar), 2 bars, 3 bars
    for lookback in [1, 2, 3, 4, 5]:
        results = []
        for _, trade in df_trades.iterrows():
            sym = trade['symbol']
            e_time = trade['entry_time']
            direction = 1 if trade['direction'] == 'BUY' else -1
            df_m = rates_5m[sym]

            matching = df_m.index[df_m.index <= e_time]
            if len(matching) < 10:
                continue
            idx = len(matching) - 1

            lows = df_m['low'].values
            highs = df_m['high'].values
            closes = df_m['close'].values

            # Look at the trigger candle (idx) and the candle right before (idx-1)
            # Did either candle sweep the lowest low of the preceding `lookback` candles?
            has_micro_sweep = False
            if direction == 1:
                # Prior reference low: min of bars [idx - lookback - 1 : idx - 1]
                ref_low = min(lows[idx - lookback - 1 : idx])
                # Did bar idx or idx-1 wick below ref_low and close above?
                if (lows[idx] < ref_low and closes[idx] > ref_low) or (lows[idx - 1] < ref_low and closes[idx] > ref_low):
                    has_micro_sweep = True
            else:
                ref_high = max(highs[idx - lookback - 1 : idx])
                if (highs[idx] > ref_high and closes[idx] < ref_high) or (highs[idx - 1] > ref_high and closes[idx] < ref_high):
                    has_micro_sweep = True

            rec = trade.to_dict()
            rec['has_micro_sweep'] = has_micro_sweep
            results.append(rec)

        res_df = pd.DataFrame(results)
        with_sw = res_df[res_df['has_micro_sweep'] == True]
        no_sw = res_df[res_df['has_micro_sweep'] == False]

        def calc_pf(df):
            gp = df[df['net_pnl'] > 0]['net_pnl'].sum()
            gl = abs(df[df['net_pnl'] <= 0]['net_pnl'].sum())
            return gp / gl if gl > 0 else 999.0

        wr_with = len(with_sw[with_sw['net_pnl'] > 0]) / len(with_sw) * 100.0 if len(with_sw) > 0 else 0
        wr_no = len(no_sw[no_sw['net_pnl'] > 0]) / len(no_sw) * 100.0 if len(no_sw) > 0 else 0
        pf_with = calc_pf(with_sw)
        pf_no = calc_pf(no_sw)
        pnl_with = with_sw['compounded_net_pnl'].sum()
        pnl_no = no_sw['compounded_net_pnl'].sum()

        print(f"Micro-Sweep Lookback={lookback} bars:")
        print(f"  WITH SWEEP:    Trades={len(with_sw):3d} | WR={wr_with:.1f}% | PF={pf_with:.2f} | Net=+${pnl_with:,.2f}")
        print(f"  WITHOUT SWEEP: Trades={len(no_sw):3d} | WR={wr_no:.1f}% | PF={pf_no:.2f} | Net=+${pnl_no:,.2f}\n")

if __name__ == "__main__":
    test_micro_sweeps()
