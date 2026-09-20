"""
Multi-Pair DCC Strategy Benchmark Runner
Evaluates DCC Strategy across all community-traded pairs using MT5 candle & tick data.
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime

# Local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_fetcher import MT5DataFetcher
from strategy_engine import DCCStrategyEngine
from tick_execution_engine import TickExecutionSimulator

def main():
    print("="*70)
    print("DCC GOLDEN STRATEGY - MULTI-PAIR TICK-PRECISION BENCHMARK")
    print("="*70)
    
    # Exclude BTC per user request. Focus on Forex, Metals, Indices.
    symbols = [
        'XAUUSD',   # Gold
        'USDJPY',   # Forex Major
        'EURUSD',   # Forex Major
        'GBPNZD',   # Forex Cross
        'GBPCAD',   # Forex Cross
        'GBPJPY',   # Forex Cross
        'US30',     # US Index
        'SPX500',   # US Index
        'NAS100'    # Baseline Reference
    ]
    
    fetcher = MT5DataFetcher()
    engine = DCCStrategyEngine(adx_threshold=20.0, atr_multiplier=0.9, rr_ratio=2.0)
    simulator = TickExecutionSimulator(fetcher)
    
    os.makedirs('experiments/multi_pair_expansion/results', exist_ok=True)
    all_trades = []
    summary_stats = []
    
    for symbol in symbols:
        print(f"\n>>> Processing {symbol}...")
        try:
            specs = fetcher.get_symbol_specs(symbol)
            print(f"    Broker Spread: {specs['spread']} pts, Digits: {specs['digits']}")
            
            # Fetch 15,000 M5 bars (~3.5 months of continuous trading)
            df_m5, df_h1 = fetcher.get_candle_data(symbol, n_m5_bars=15000)
            print(f"    Fetched {len(df_m5):,} M5 bars ({df_m5.index[0].strftime('%Y-%m-%d')} to {df_m5.index[-1].strftime('%Y-%m-%d')})")
            
            # Prepare Indicators & Filters
            df_prepared = engine.prepare_data(df_m5, df_h1)
            
            # Scan Signals
            signals = engine.scan_signals(df_prepared)
            for s in signals:
                s['symbol'] = symbol
            print(f"    Detected {len(signals)} valid DCC trade setups")
            
            if len(signals) == 0:
                print(f"    No signals found for {symbol}")
                continue
                
            # Simulate each trade with tick/intrabar precision
            sym_trades = []
            for s in signals:
                trade_res = simulator.simulate_trade_with_ticks(symbol, s)
                trade_res['symbol'] = symbol
                sym_trades.append(trade_res)
                all_trades.append(trade_res)
                
            df_sym_trades = pd.DataFrame(sym_trades)
            
            # Metrics
            total = len(df_sym_trades)
            wins = len(df_sym_trades[df_sym_trades['outcome'] == 'WIN'])
            losses = len(df_sym_trades[df_sym_trades['outcome'] == 'LOSS'])
            win_rate = (wins / total * 100) if total > 0 else 0.0
            
            total_r = df_sym_trades['r_multiple'].sum()
            gross_win = df_sym_trades[df_sym_trades['r_multiple'] > 0]['r_multiple'].sum()
            gross_loss = abs(df_sym_trades[df_sym_trades['r_multiple'] < 0]['r_multiple'].sum())
            profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 99.99
            
            # Equity Curve & Drawdown in R
            equity_curve = df_sym_trades['r_multiple'].cumsum()
            running_max = equity_curve.cummax()
            drawdown = running_max - equity_curve
            max_dd_r = drawdown.max() if len(drawdown) > 0 else 0.0
            
            avg_duration = df_sym_trades['duration_min'].mean()
            
            print(f"    [RESULTS] {symbol}: {total} Trades | Win Rate: {win_rate:.1f}% | PF: {profit_factor:.2f} | Net R: {total_r:+.1f}R | Max DD: -{max_dd_r:.1f}R")
            
            summary_stats.append({
                'Symbol': symbol,
                'Category': 'Metals' if 'XAU' in symbol else ('Index' if symbol in ['NAS100', 'US30', 'SPX500'] else 'Forex'),
                'Total Trades': total,
                'Wins': wins,
                'Losses': losses,
                'Win Rate (%)': round(win_rate, 1),
                'Profit Factor': round(profit_factor, 2),
                'Net R': round(total_r, 1),
                'Max DD (R)': round(max_dd_r, 1),
                'Avg Hold (min)': round(avg_duration, 1)
            })
            
        except Exception as e:
            print(f"    Error processing {symbol}: {e}")

    fetcher.close()
    
    # Save overall summary
    df_summary = pd.DataFrame(summary_stats)
    df_summary.sort_values(by='Profit Factor', ascending=False, inplace=True)
    
    summary_path = 'experiments/multi_pair_expansion/results/benchmark_summary.csv'
    df_summary.to_csv(summary_path, index=False)
    
    if len(all_trades) > 0:
        df_all = pd.DataFrame(all_trades)
        trades_path = 'experiments/multi_pair_expansion/results/all_simulated_trades.csv'
        df_all.to_csv(trades_path, index=False)
        
    print("\n" + "="*70)
    print("MULTI-PAIR BENCHMARK SUMMARY")
    print("="*70)
    print(df_summary.to_string(index=False))
    print("="*70)
    
    # Write Markdown Report
    report_md = "# DCC Strategy - Multi-Pair Benchmark Results\n\n"
    report_md += f"**Executed:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n"
    report_md += "**Configuration:** 1H 9/20 EMA Trend + 1H ADX(14) > 20 + Session VWAP + 2H Room + 5M Bar-Close Flip + 1H ATR*0.9 SL + 1:2 RR  \n\n"
    # Markdown Table formatting
    headers = list(df_summary.columns)
    md_table = "| " + " | ".join(headers) + " |\n"
    md_table += "| " + " | ".join(["---"] * len(headers)) + " |\n"
    for _, row in df_summary.iterrows():
        md_table += "| " + " | ".join([str(row[h]) for h in headers]) + " |\n"
    report_md += md_table + "\n"
    report_md += "## Top Performing Asset Insights\n\n"
    
    for idx, row in df_summary.iterrows():
        status = "🌟 ELITE PAIR" if row['Profit Factor'] >= 1.7 and row['Win Rate (%)'] >= 48.0 else ("✅ SOLID PERFORMER" if row['Profit Factor'] >= 1.2 else "⚠️ LOW CONVICTION")
        report_md += f"### {row['Symbol']} — {status}\n"
        report_md += f"- **Win Rate:** {row['Win Rate (%)']}% ({row['Wins']}W / {row['Losses']}L)\n"
        report_md += f"- **Profit Factor:** {row['Profit Factor']}\n"
        report_md += f"- **Net Profit:** {row['Net R']:+.1f}R\n"
        report_md += f"- **Max Drawdown:** -{row['Max DD (R)']}R\n"
        report_md += f"- **Average Hold Time:** {row['Avg Hold (min)']} mins\n\n"
        
    with open('experiments/multi_pair_expansion/benchmark_report.md', 'w', encoding='utf-8') as f:
        f.write(report_md)
        
    print(f"\nFull report written to: experiments/multi_pair_expansion/benchmark_report.md")

if __name__ == '__main__':
    main()
