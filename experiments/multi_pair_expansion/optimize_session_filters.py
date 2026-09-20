"""
Session Timing & Confluence Optimization for Top Multi-Pair Contenders
Tests London (07:00-16:00 UTC), NY (12:30-20:00 UTC), and Combined Liquid Hours (07:00-20:00 UTC).
"""

import os
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_fetcher import MT5DataFetcher
from strategy_engine import DCCStrategyEngine
from tick_execution_engine import TickExecutionSimulator

def main():
    print("="*75)
    print("SESSION TIMING & CONFLUENCE OPTIMIZATION (GBPJPY, XAUUSD, NAS100)")
    print("="*75)
    
    symbols = ['GBPJPY', 'XAUUSD', 'NAS100', 'US30']
    
    session_configs = {
        '24/5 All Day': (0, 24),
        'London Only (07-16 UTC)': (7, 16),
        'NY Only (12:30-20 UTC)': (12.5, 20),
        'London + NY Liquid (07-20 UTC)': (7, 20)
    }
    
    fetcher = MT5DataFetcher()
    engine = DCCStrategyEngine(adx_threshold=20.0, atr_multiplier=0.9, rr_ratio=2.0)
    simulator = TickExecutionSimulator(fetcher)
    
    results = []
    
    for symbol in symbols:
        print(f"\n--- Optimizing {symbol} ---")
        df_m5, df_h1 = fetcher.get_candle_data(symbol, n_m5_bars=15000)
        df_prepared = engine.prepare_data(df_m5, df_h1)
        all_signals = engine.scan_signals(df_prepared)
        
        for sess_name, (start_h, end_h) in session_configs.items():
            filtered_signals = []
            for s in all_signals:
                s_time = s['time']
                hour_float = s_time.hour + (s_time.minute / 60.0)
                if start_h <= hour_float < end_h:
                    filtered_signals.append(s)
                    
            if len(filtered_signals) == 0:
                continue
                
            trades = [simulator.simulate_trade_with_ticks(symbol, s) for s in filtered_signals]
            df_t = pd.DataFrame(trades)
            
            total = len(df_t)
            wins = len(df_t[df_t['outcome'] == 'WIN'])
            losses = len(df_t[df_t['outcome'] == 'LOSS'])
            wr = (wins / total * 100) if total > 0 else 0.0
            
            pos_r = df_t[df_t['r_multiple'] > 0]['r_multiple'].sum()
            neg_r = abs(df_t[df_t['r_multiple'] < 0]['r_multiple'].sum())
            pf = (pos_r / neg_r) if neg_r > 0 else 99.9
            net_r = df_t['r_multiple'].sum()
            
            equity = df_t['r_multiple'].cumsum()
            max_dd = (equity.cummax() - equity).max() if len(equity) > 0 else 0.0
            
            print(f"  [{sess_name:<30}] Trades: {total:>3} | WR: {wr:>5.1f}% | PF: {pf:>4.2f} | Net: {net_r:>+5.1f}R | DD: -{max_dd:>4.1f}R")
            
            results.append({
                'Symbol': symbol,
                'Session Filter': sess_name,
                'Trades': total,
                'Win Rate (%)': round(wr, 1),
                'Profit Factor': round(pf, 2),
                'Net R': round(net_r, 1),
                'Max DD (R)': round(max_dd, 1)
            })
            
    fetcher.close()
    
    df_res = pd.DataFrame(results)
    df_res.sort_values(by=['Symbol', 'Profit Factor'], ascending=[True, False], inplace=True)
    
    out_path = 'experiments/multi_pair_expansion/results/session_optimization_results.csv'
    df_res.to_csv(out_path, index=False)
    print("\n" + "="*75)
    print("SESSION OPTIMIZATION SUMMARY")
    print("="*75)
    print(df_res.to_string(index=False))
    print("="*75)

if __name__ == '__main__':
    main()
