"""
Forensic Audit: Candle-to-EMA Distance & Overstretch Analysis
Proves whether the distance between price and EMAs correlates with losses vs wins.
"""

import os
import sys
import pandas as pd
import numpy as np

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.strategy_optimizer.backtester import BacktestDataset

def run_audit():
    csv_path = os.path.join(PROJECT_ROOT, "final_dcc_strategy", "trades_log_official_continuous_no_eod.csv")
    df_trades = pd.read_csv(csv_path)
    df_trades['entry_dt'] = pd.to_datetime(df_trades['entry_time'], format='mixed', utc=True)

    eval_xau, df_m5_xau = BacktestDataset.get_data('XAUUSD')
    eval_nas, df_m5_nas = BacktestDataset.get_data('NAS100')

    results = []
    for idx, row in df_trades.iterrows():
        sym = row['symbol']
        eval_bars = eval_xau if sym == 'XAUUSD' else eval_nas
        df_m5 = df_m5_xau if sym == 'XAUUSD' else df_m5_nas
        t = row['entry_dt']
        
        # Match bar
        locs = eval_bars.index.get_indexer([t], method='pad')
        if locs[0] == -1:
            continue
        bar = eval_bars.iloc[locs[0]]
        
        close = float(bar['close'])
        ema20_1h = float(bar['ema20_1h'])
        ema9_5m = float(bar['ema9_5m'])
        ema20_5m = float(bar['ema20_5m'])
        atr_1h = float(bar['atr_1h'])
        vwap = float(bar.get('vwap_5m', close))
        
        dist_1h_ema20 = abs(close - ema20_1h)
        stretch_1h_atr = dist_1h_ema20 / atr_1h if atr_1h > 0 else 0.0
        
        dist_5m_ema20 = abs(close - ema20_5m)
        dist_5m_ema9 = abs(close - ema9_5m)
        dist_5m_ema_gap = abs(ema9_5m - ema20_5m)
        dist_vwap = abs(close - vwap)
        
        # Also compute 5M EMA gap as ratio of 5M ATR
        # Check if price was overextended from 5M EMA 20
        high = float(bar['high'])
        low = float(bar['low'])
        candle_body = abs(close - float(bar['open']))
        candle_range = high - low
        
        is_win = (row['net_pnl'] > 0)
        is_sl = (row['exit_reason'] == 'SL')
        
        results.append({
            'trade_id': row['trade_id'],
            'symbol': sym,
            'direction': row['direction'],
            'entry_time': t,
            'pnl': row['net_pnl'],
            'exit_reason': row['exit_reason'],
            'is_win': is_win,
            'is_sl': is_sl,
            'close': close,
            'ema20_1h': ema20_1h,
            'stretch_1h_atr': stretch_1h_atr,
            'dist_1h_ema20': dist_1h_ema20,
            'dist_5m_ema9': dist_5m_ema9,
            'dist_5m_ema20': dist_5m_ema20,
            'dist_5m_ema_gap': dist_5m_ema_gap,
            'dist_vwap': dist_vwap,
            'adx_1h': float(bar.get('adx_1h', 0.0)),
            'atr_1h': atr_1h
        })

    res_df = pd.DataFrame(results)
    print(f"Successfully matched {len(res_df)} of {len(df_trades)} official trades.")

    print("\n" + "=" * 80)
    print("1. STATISTICAL PROOF: DISTANCE TO EMAs (WINNING vs LOSING TRADES)")
    print("=" * 80)
    wins = res_df[res_df['is_win']]
    losses = res_df[res_df['is_sl']]

    print(f"{'Metric':<35} | {'Winning Trades (' + str(len(wins)) + ')':<20} | {'Losing Trades (' + str(len(losses)) + ')':<20} | {'Difference':<12}")
    print("-" * 92)
    
    m_1h_str_w = wins['stretch_1h_atr'].mean()
    m_1h_str_l = losses['stretch_1h_atr'].mean()
    print(f"{'1H EMA 20 Stretch (x ATR)':<35} | {m_1h_str_w:<20.3f} | {m_1h_str_l:<20.3f} | {m_1h_str_l - m_1h_str_w:+10.3f}")

    # For XAUUSD specifically
    xau_w = wins[wins['symbol'] == 'XAUUSD']
    xau_l = losses[losses['symbol'] == 'XAUUSD']
    print(f"{'  - Gold 1H Stretch (x ATR)':<35} | {xau_w['stretch_1h_atr'].mean():<20.3f} | {xau_l['stretch_1h_atr'].mean():<20.3f} | {xau_l['stretch_1h_atr'].mean() - xau_w['stretch_1h_atr'].mean():+10.3f}")

    # For NAS100 specifically
    nas_w = wins[wins['symbol'] == 'NAS100']
    nas_l = losses[losses['symbol'] == 'NAS100']
    print(f"{'  - Nasdaq 1H Stretch (x ATR)':<35} | {nas_w['stretch_1h_atr'].mean():<20.3f} | {nas_l['stretch_1h_atr'].mean():<20.3f} | {nas_l['stretch_1h_atr'].mean() - nas_w['stretch_1h_atr'].mean():+10.3f}")

    m_5m_gap_w = wins['dist_5m_ema_gap'].mean()
    m_5m_gap_l = losses['dist_5m_ema_gap'].mean()
    print(f"{'5M EMA 9/20 Gap Distance':<35} | {m_5m_gap_w:<20.3f} | {m_5m_gap_l:<20.3f} | {m_5m_gap_l - m_5m_gap_w:+10.3f}")

    print("\n" + "=" * 80)
    print("2. WIN RATE & PROFITABILITY BY 1H EMA20 STRETCH BRACKET")
    print("=" * 80)
    res_df['stretch_bracket'] = pd.cut(
        res_df['stretch_1h_atr'],
        bins=[0.0, 0.50, 0.75, 1.00, 1.25, 10.0],
        labels=["<= 0.50x (Tight / Close)", "0.50x - 0.75x (Moderate)", "0.75x - 1.00x (Healthy)", "1.00x - 1.25x (Extended)", "> 1.25x (Extreme Overstretch)"]
    )
    
    grp = res_df.groupby('stretch_bracket', observed=False).agg(
        trades=('trade_id', 'count'),
        wins=('is_win', 'sum'),
        losses=('is_sl', 'sum'),
        total_pnl=('pnl', 'sum'),
        avg_pnl=('pnl', 'mean')
    )
    grp['win_rate'] = (grp['wins'] / grp['trades'] * 100).round(1)
    grp['sl_rate'] = (grp['losses'] / grp['trades'] * 100).round(1)
    
    print(f"{'Stretch Bracket':<32} | {'Trades':<7} | {'Wins':<5} | {'SLs':<5} | {'Win Rate':<9} | {'SL Rate':<8} | {'Total PnL ($)':<14} | {'Avg PnL':<9}")
    print("-" * 105)
    for b_name, r in grp.iterrows():
        pnl_str = f"+${r['total_pnl']:,.2f}" if r['total_pnl'] >= 0 else f"-${abs(r['total_pnl']):,.2f}"
        avg_str = f"+${r['avg_pnl']:.2f}" if r['avg_pnl'] >= 0 else f"-${abs(r['avg_pnl']):.2f}"
        print(f"{b_name:<32} | {r['trades']:<7} | {r['wins']:<5} | {r['losses']:<5} | {r['win_rate']:<8.1f}% | {r['sl_rate']:<7.1f}% | {pnl_str:<14} | {avg_str:<9}")

    print("\n" + "=" * 80)
    print("3. WHAT HAPPENS IN CHOPPY CONSOLIDATIONS VS HEALTHY TRENDS?")
    print("=" * 80)
    # Define Chop Indicator: High stretch + low ADX OR frequent 5M flips
    # When ADX < 20 and Stretch > 0.8x (Chop trap)
    chop_trades = res_df[(res_df['adx_1h'] < 20) & (res_df['stretch_1h_atr'] > 0.75)]
    trend_trades = res_df[(res_df['adx_1h'] >= 20) & (res_df['stretch_1h_atr'] <= 1.00)]
    
    print(f"Healthy Trend Setups (ADX >= 20 & Stretch <= 1.00x ATR):")
    print(f"  Total Trades: {len(trend_trades)}")
    print(f"  Win Rate:     {len(trend_trades[trend_trades['is_win']]) / len(trend_trades) * 100:.1f}%")
    print(f"  Net PnL:      ${trend_trades['pnl'].sum():,.2f}")
    
    if len(chop_trades) > 0:
        print(f"\nOverextended Chop Traps (ADX < 20 & Stretch > 0.75x ATR):")
        print(f"  Total Trades: {len(chop_trades)}")
        print(f"  Win Rate:     {len(chop_trades[chop_trades['is_win']]) / len(chop_trades) * 100:.1f}%")
        print(f"  Net PnL:      ${chop_trades['pnl'].sum():,.2f}")

    # Symbol-specific normalized analysis
    print("\n" + "=" * 80)
    print("5. NORMALIZED CANDLE-TO-EMA DISTANCE (GOLD vs NASDAQ)")
    print("=" * 80)
    for sym in ['XAUUSD', 'NAS100']:
        sub = res_df[res_df['symbol'] == sym]
        s_wins = sub[sub['is_win']]
        s_losses = sub[sub['is_sl']]
        
        print(f"\n--- {sym} ({len(sub)} trades: {len(s_wins)} Wins, {len(s_losses)} Losses) ---")
        
        # 5M EMA9 Distance / 1H ATR
        w_e9 = (s_wins['dist_5m_ema9'] / s_wins['atr_1h']).mean()
        l_e9 = (s_losses['dist_5m_ema9'] / s_losses['atr_1h']).mean()
        print(f"Candle to 5M EMA 9:   Wins = {w_e9:.3f}x ATR  |  Losses = {l_e9:.3f}x ATR  | Diff = {l_e9 - w_e9:+.3f}")

        # 5M EMA20 Distance / 1H ATR
        w_e20 = (s_wins['dist_5m_ema20'] / s_wins['atr_1h']).mean()
        l_e20 = (s_losses['dist_5m_ema20'] / s_losses['atr_1h']).mean()
        print(f"Candle to 5M EMA 20:  Wins = {w_e20:.3f}x ATR  |  Losses = {l_e20:.3f}x ATR  | Diff = {l_e20 - w_e20:+.3f}")

        # 1H EMA20 Distance / 1H ATR (Stretch)
        w_h1 = s_wins['stretch_1h_atr'].mean()
        l_h1 = s_losses['stretch_1h_atr'].mean()
        print(f"Candle to 1H EMA 20:  Wins = {w_h1:.3f}x ATR  |  Losses = {l_h1:.3f}x ATR  | Diff = {l_h1 - w_h1:+.3f}")

    # Now let's analyze what happens on the September 15 trades specifically!
    print("\n" + "=" * 80)
    print("6. SEPTEMBER 15 FORENSIC BREAKDOWN (THE VPS RECENT DAY)")
    print("=" * 80)
    # Check if Sep 15 bars are in eval_bars
    for sym in ['XAUUSD', 'NAS100']:
        e_bars = eval_xau if sym == 'XAUUSD' else eval_nas
        sep15_bars = e_bars[e_bars.index.strftime('%Y-%m-%d') == '2026-09-15']
        if len(sep15_bars) > 0:
            print(f"[{sym}] Found {len(sep15_bars)} bars on Sep 15. Avg Stretch: {sep15_bars['stretch_ratio'].mean():.2f}x ATR | Max Stretch: {sep15_bars['stretch_ratio'].max():.2f}x ATR")

if __name__ == "__main__":
    run_audit()
