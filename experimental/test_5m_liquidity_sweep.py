"""
Experimental Confluence Test: 5M Liquidity Sweep (Turtle Soup) Filter
Tests whether requiring a 5M Liquidity Sweep during/preceding the pullback improves:
- Win Rate (%)
- Profit Factor
- Drawdown (%)
- Overall Expectancy

Definition of 5M Liquidity Sweep:
For a BUY:
- During the pullback leading up to the 5M EMA 9/20 flip, price must have swept
  below a recent 5M swing low (low[k] < prior_swing_low) and reclaimed back above it.
For a SELL:
- During the pullback leading up to the 5M EMA 9/20 flip, price must have swept
  above a recent 5M swing high (high[k] > prior_swing_high) and reclaimed back below it.
"""

import os
import sys
from datetime import datetime, timezone
from typing import Tuple, List, Dict, Optional
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mt5_data import MT5DataProvider
from dcc_engine import DCCEngine


def detect_liquidity_sweep(
    df_m5: pd.DataFrame,
    entry_idx: int,
    direction: int,  # 1 for BUY, -1 for SELL
    swing_lookback: int = 15,
    pullback_window: int = 6
) -> Tuple[bool, float, float]:
    """
    Checks if a 5M liquidity sweep occurred prior to entry_idx.
    Returns: (is_sweep, sweep_level, swept_by_pts)
    """
    if entry_idx < swing_lookback + 2:
        return False, 0.0, 0.0

    # Look back at bars prior to the pullback to find swing levels
    pullback_start_idx = max(0, entry_idx - pullback_window)
    swing_search_start = max(0, pullback_start_idx - swing_lookback)

    if pullback_start_idx <= swing_search_start:
        return False, 0.0, 0.0

    lows = df_m5['low'].values
    highs = df_m5['high'].values
    closes = df_m5['close'].values

    if direction == 1:  # BUY setup
        # Find swing lows in the search window (a bar lower than its neighbors)
        candidate_lows = []
        for j in range(swing_search_start + 1, pullback_start_idx):
            if lows[j] <= lows[j - 1] and lows[j] <= lows[j + 1]:
                candidate_lows.append(lows[j])

        if not candidate_lows:
            # Fallback to absolute minimum in the search window
            candidate_lows = [min(lows[swing_search_start:pullback_start_idx])]

        # Check if any bar during the pullback swept below ANY of these candidate lows
        # and current bar closed back above it (reclaim)
        curr_close = closes[entry_idx]
        for swing_low in candidate_lows:
            # Did any bar in [pullback_start_idx, entry_idx] breach below swing_low?
            min_pullback_low = min(lows[pullback_start_idx:entry_idx + 1])
            if min_pullback_low < swing_low and curr_close > swing_low:
                swept_pts = swing_low - min_pullback_low
                return True, swing_low, swept_pts

        return False, 0.0, 0.0

    else:  # SELL setup
        # Find swing highs in the search window
        candidate_highs = []
        for j in range(swing_search_start + 1, pullback_start_idx):
            if highs[j] >= highs[j - 1] and highs[j] >= highs[j + 1]:
                candidate_highs.append(highs[j])

        if not candidate_highs:
            candidate_highs = [max(highs[swing_search_start:pullback_start_idx])]

        curr_close = closes[entry_idx]
        for swing_high in candidate_highs:
            max_pullback_high = max(highs[pullback_start_idx:entry_idx + 1])
            if max_pullback_high > swing_high and curr_close < swing_high:
                swept_pts = max_pullback_high - swing_high
                return True, swing_high, swept_pts

        return False, 0.0, 0.0


def run_experiment():
    print("=" * 75)
    print("EXPERIMENTAL TEST: 5M LIQUIDITY SWEEP CONFLUENCE AUDIT")
    print("=" * 75)

    dp = MT5DataProvider()
    start_date = datetime(2026, 1, 2)
    end_date = datetime(2026, 6, 30, 23, 59, 59)

    # Load trades from our strict 6-month non-overlapping dataset
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(base_dir, "reports", "trades_log_with_3pct_circuit_breaker.csv")
    if not os.path.exists(csv_path):
        csv_path = os.path.join(base_dir, "reports", "trades_log_optimized_low_dd.csv")
    
    df_trades = pd.read_csv(csv_path)
    df_trades['entry_time'] = pd.to_datetime(df_trades['entry_time'], format='ISO8601')
    print(f"Loaded {len(df_trades)} historical trades from backtest.")

    # Fetch 5M rates for both symbols
    rates_5m = {}
    for s in ["XAUUSD", "NAS100"]:
        print(f"Fetching 5M rates for {s}...")
        df_m5, _, _ = dp.fetch_multi_timeframe_rates(s, start_date, end_date, warmup_days=20)
        rates_5m[s] = df_m5

    # Test multiple lookback variations
    # swing_lookback: how far back to look for the swing level (bars)
    # pullback_window: duration of the pullback (bars)
    param_variations = [
        {"name": "Tight Sweep (Lookback=10, Pullback=4)", "swing_lookback": 10, "pullback_window": 4},
        {"name": "Standard Sweep (Lookback=15, Pullback=6)", "swing_lookback": 15, "pullback_window": 6},
        {"name": "Deep Swing Sweep (Lookback=20, Pullback=8)", "swing_lookback": 20, "pullback_window": 8},
    ]

    for p in param_variations:
        print("\n" + "-" * 75)
        print(f"TESTING VARIATION: {p['name']}")
        print("-" * 75)

        sweep_results = []
        for _, trade in df_trades.iterrows():
            sym = trade['symbol']
            e_time = trade['entry_time']
            direction = 1 if trade['direction'] == 'BUY' else -1
            df_m = rates_5m[sym]

            # Find matching bar index in 5M dataframe
            # Entry is on the first tick of the candle right after the signal bar
            # So the setup bar is the bar immediately before or at entry_time
            matching_indices = df_m.index[df_m.index <= e_time]
            if len(matching_indices) < 20:
                continue

            entry_idx = len(matching_indices) - 1

            has_sweep, level, swept_pts = detect_liquidity_sweep(
                df_m, entry_idx, direction,
                swing_lookback=p['swing_lookback'],
                pullback_window=p['pullback_window']
            )

            record = trade.to_dict()
            record['has_sweep'] = has_sweep
            record['sweep_level'] = level
            record['swept_pts'] = swept_pts
            sweep_results.append(record)

        df_res = pd.DataFrame(sweep_results)
        
        # Breakdown: Trades WITH sweep vs WITHOUT sweep
        with_sweep = df_res[df_res['has_sweep'] == True]
        no_sweep = df_res[df_res['has_sweep'] == False]

        def get_stats(sub_df, label):
            tot = len(sub_df)
            if tot == 0:
                return f"{label}: 0 trades"
            wins = len(sub_df[sub_df['net_pnl'] > 0])
            wr = wins / tot * 100.0
            pnl = sub_df['compounded_net_pnl'].sum() if 'compounded_net_pnl' in sub_df else sub_df['net_pnl'].sum()
            gp = sub_df[sub_df['net_pnl'] > 0]['net_pnl'].sum()
            gl = abs(sub_df[sub_df['net_pnl'] <= 0]['net_pnl'].sum())
            pf = gp / gl if gl > 0 else 999.0
            return {
                "trades": tot, "wins": wins, "wr": wr, "pnl": pnl, "pf": pf
            }

        s_all = get_stats(df_res, "ALL")
        s_with = get_stats(with_sweep, "WITH SWEEP")
        s_without = get_stats(no_sweep, "WITHOUT SWEEP")

        print(f"  [BASELINE (ALL TRADES)]   Trades: {s_all['trades']:3d} | WR: {s_all['wr']:.1f}% | PF: {s_all['pf']:.2f} | Net: +${s_all['pnl']:,.2f}")
        print(f"  >>> [WITH 5M SWEEP] <<<   Trades: {s_with['trades']:3d} | WR: {s_with['wr']:.1f}% | PF: {s_with['pf']:.2f} | Net: +${s_with['pnl']:,.2f}")
        print(f"  [WITHOUT 5M SWEEP]        Trades: {s_without['trades']:3d} | WR: {s_without['wr']:.1f}% | PF: {s_without['pf']:.2f} | Net: +${s_without['pnl']:,.2f}")

        # Comparison summary
        delta_wr = s_with['wr'] - s_all['wr']
        print(f"  -> Win Rate Impact: {'+' if delta_wr >= 0 else ''}{delta_wr:.1f}%")
        print(f"  -> Profit Factor Impact: {s_with['pf']:.2f} vs {s_all['pf']:.2f}")


if __name__ == "__main__":
    run_experiment()
