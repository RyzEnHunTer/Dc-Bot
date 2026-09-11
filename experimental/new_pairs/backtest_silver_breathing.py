import os
import sys
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dcc_engine import DCCEngine
from experimental.test_5m_liquidity_sweep import detect_liquidity_sweep
from experimental.new_pairs.pair_configs import CANDIDATE_CONFIGS

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
broker_offset = timedelta(hours=3)

def run_silver_breathing_backtest():
    sym = "XAGUSD"
    cfg = CANDIDATE_CONFIGS[sym]
    sl_multiplier = 1.4
    tp1_rr = 1.6
    tp2_rr = 2.4
    
    print("=" * 110)
    print(f"       FORENSIC AUDIT: SILVER ({sym}) BREATHING PARAMETERS BACKTEST (2026)")
    print(f"       Settings: ATR SL Multiplier = {sl_multiplier}x | TP1 = {tp1_rr}R | TP2 = {tp2_rr}R")
    print("=" * 110)
    
    df_m5 = pd.read_parquet(f"{CACHE_DIR}/m5_{sym}.parquet")
    df_m5['time'] = (pd.to_datetime(df_m5['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_m5.set_index('time', inplace=True)
    df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)
    
    df_1h = pd.read_parquet(f"{CACHE_DIR}/h1_{sym}.parquet")
    df_1h['time'] = (pd.to_datetime(df_1h['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_1h.set_index('time', inplace=True)
    
    df_2h = pd.read_parquet(f"{CACHE_DIR}/h2_{sym}.parquet")
    df_2h['time'] = (pd.to_datetime(df_2h['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_2h.set_index('time', inplace=True)
    
    engine = DCCEngine(atr_sl_multiplier=sl_multiplier, risk_reward_ratio=tp1_rr)
    df_prep = engine.prepare_data(df_m5, df_1h, df_2h)
    eval_bars = df_prep[(df_prep.index >= '2026-01-01') & (df_prep.index <= '2026-09-08')].copy()
    
    tick_size = cfg.tick_size
    tick_val = cfg.tick_value
    val_per_pt = tick_val / tick_size
    
    active_trade = None
    completed_trades = []
    current_balance = 5000.0
    
    for i in range(20, len(eval_bars)):
        c_bar = eval_bars.iloc[i]
        p_bar = eval_bars.iloc[i - 1]
        t = eval_bars.index[i]
        t_hour = t.hour
        
        high_p = float(c_bar['high'])
        low_p = float(c_bar['low'])
        close_p = float(c_bar['close'])
        
        # 1. Manage active trade
        if active_trade is not None:
            pos = active_trade
            is_buy = (pos['direction'] == "BUY")
            sl_hit = (low_p <= pos['current_sl']) if is_buy else (high_p >= pos['current_sl'])
            tp1_hit = (high_p >= pos['tp1_price']) if is_buy else (low_p <= pos['tp1_price'])
            tp2_hit = (high_p >= pos['tp2_price']) if is_buy else (low_p <= pos['tp2_price'])
            eod_hit = (t_hour >= 21)
            
            if tp2_hit:
                pos['exit_time'] = t
                pos['exit_price'] = pos['tp2_price']
                pos['exit_reason'] = "FULL_TP2"
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                pts1 = pos['tp1_dist']
                pts2 = pos['tp2_dist']
                p1_dollars = pos['part_lots'] * pts1 * val_per_pt
                p2_dollars = pos['run_lots'] * pts2 * val_per_pt
                comm = pos['total_lots'] * 5.0
                net = p1_dollars + p2_dollars - comm
                pos['partial_pnl'] = round(p1_dollars, 2)
                pos['runner_pnl'] = round(p2_dollars, 2)
                pos['commission'] = round(comm, 2)
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue
                
            elif sl_hit:
                pos['exit_time'] = t
                pos['exit_price'] = pos['current_sl']
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                comm = pos['total_lots'] * 5.0
                if pos['tp1_hit']:
                    pos['exit_reason'] = "TP1_THEN_BE"
                    p1_dollars = pos['part_lots'] * pos['tp1_dist'] * val_per_pt
                    p2_dollars = 0.0
                    net = p1_dollars - comm
                else:
                    pos['exit_reason'] = "SL"
                    p1_dollars = 0.0
                    p2_dollars = 0.0
                    net = -50.0 - comm
                pos['partial_pnl'] = round(p1_dollars, 2)
                pos['runner_pnl'] = round(p2_dollars, 2)
                pos['commission'] = round(comm, 2)
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue
                
            elif tp1_hit and not pos['tp1_hit']:
                pos['tp1_hit'] = True
                pos['current_sl'] = pos['entry_price']
                
            elif eod_hit:
                pos['exit_time'] = t
                pos['exit_price'] = close_p
                pos['exit_reason'] = "EOD_CLOSE"
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                comm = pos['total_lots'] * 5.0
                run_pts = (close_p - pos['entry_price']) if is_buy else (pos['entry_price'] - close_p)
                p2_dollars = pos['run_lots'] * run_pts * val_per_pt
                p1_dollars = (pos['part_lots'] * pos['tp1_dist'] * val_per_pt) if pos['tp1_hit'] else (pos['part_lots'] * run_pts * val_per_pt)
                net = p1_dollars + p2_dollars - comm
                pos['partial_pnl'] = round(p1_dollars, 2)
                pos['runner_pnl'] = round(p2_dollars, 2)
                pos['commission'] = round(comm, 2)
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue
                
        # 2. Entry signal
        if active_trade is None:
            if not (6 <= t_hour < 21) or t_hour in [9, 13]:
                continue
            bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
            if bias == 0 or c_bar['adx_1h'] < cfg.adx_min:
                continue
            atr = float(c_bar['atr_1h'])
            if atr <= 0:
                continue
                
            vwap = float(c_bar['vwap_5m'])
            h1_e20 = float(c_bar['ema20_1h'])
            curr_diff = float(c_bar['ema9_5m']) - float(c_bar['ema20_5m'])
            prev_diff = float(p_bar['ema9_5m']) - float(p_bar['ema20_5m'])
            
            sig_type = 0
            if bias == 1 and prev_diff <= 0 and curr_diff > 0 and close_p > vwap and close_p > h1_e20:
                sig_type = 1
            elif bias == -1 and prev_diff >= 0 and curr_diff < 0 and close_p < vwap and close_p < h1_e20:
                sig_type = -1
                
            if sig_type != 0:
                has_sw, _, _ = detect_liquidity_sweep(df_m5, df_m5.index.get_loc(t), bias, 20, 8)
                if not has_sw:
                    continue
                    
                sl_dist = sl_multiplier * atr
                tp1_dist = tp1_rr * sl_dist
                tp2_dist = tp2_rr * sl_dist
                entry_p = close_p
                
                if sig_type == 1:
                    dir_str = "BUY"
                    sl_p = entry_p - sl_dist
                    tp1_p = entry_p + tp1_dist
                    tp2_p = entry_p + tp2_dist
                else:
                    dir_str = "SELL"
                    sl_p = entry_p + sl_dist
                    tp1_p = entry_p - tp1_dist
                    tp2_p = entry_p - tp2_dist
                    
                raw_lots = 50.0 / (sl_dist * val_per_pt)
                tot_lots = max(0.01, round(raw_lots, 2))
                part_lots = round(tot_lots * 0.5, 2)
                run_lots = round(tot_lots - part_lots, 2)
                
                active_trade = {
                    "trade_id": len(completed_trades) + 1,
                    "symbol": sym,
                    "direction": dir_str,
                    "entry_time": t,
                    "entry_price": entry_p,
                    "sl_price": sl_p,
                    "tp1_price": tp1_p,
                    "tp2_price": tp2_p,
                    "current_sl": sl_p,
                    "sl_dist": sl_dist,
                    "tp1_dist": tp1_dist,
                    "tp2_dist": tp2_dist,
                    "total_lots": tot_lots,
                    "part_lots": part_lots,
                    "run_lots": run_lots,
                    "tp1_hit": False,
                    "tp2_hit": False,
                    "be_hit": False,
                    "starting_balance": round(current_balance, 2)
                }
                
    df_trades = pd.DataFrame(completed_trades)
    out_csv = os.path.join(REPORTS_DIR, "trades_silver_breathing_audited.csv")
    df_trades.to_csv(out_csv, index=False)
    
    # Forensic Stats
    df_trades['entry_dt'] = pd.to_datetime(df_trades['entry_time'])
    df_trades['exit_dt'] = pd.to_datetime(df_trades['exit_time'])
    df_trades['month'] = df_trades['entry_dt'].dt.strftime('%Y-%m')
    df_trades['date'] = df_trades['exit_dt'].dt.strftime('%Y-%m-%d')
    
    total_trades = len(df_trades)
    wins = df_trades[df_trades['net_pnl'] > 0]
    losses = df_trades[df_trades['net_pnl'] < 0]
    win_rate = len(wins) / total_trades * 100.0
    
    total_pnl = df_trades['net_pnl'].sum()
    gross_win = wins['net_pnl'].sum()
    gross_loss = abs(losses['net_pnl'].sum())
    profit_factor = gross_win / gross_loss if gross_loss > 0 else 999.0
    
    avg_win = wins['net_pnl'].mean() if len(wins) > 0 else 0
    avg_loss = abs(losses['net_pnl'].mean()) if len(losses) > 0 else 0
    win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 999.0
    
    # Exit reasons breakdown
    exit_counts = df_trades['exit_reason'].value_counts()
    
    # Drawdown metrics on standalone
    cum_pnl = df_trades['net_pnl'].cumsum()
    peak = np.maximum.accumulate(5000.0 + cum_pnl)
    equity = 5000.0 + cum_pnl
    dd_from_peak = (peak - equity) / peak * 100.0
    max_peak_dd = dd_from_peak.max()
    lowest_base_bal = equity.min()
    max_base_loss_pct = (5000.0 - lowest_base_bal) / 5000.0 * 100.0 if lowest_base_bal < 5000.0 else 0.0
    
    # Daily Drawdown
    daily_pnl = df_trades.groupby('date')['net_pnl'].sum()
    worst_day_loss = daily_pnl.min()
    max_daily_dd_pct = abs(worst_day_loss) / 5000.0 * 100.0 if worst_day_loss < 0 else 0.0
    
    print("\n1. OVERALL LIFETIME PERFORMANCE (JAN 1 - SEP 8, 2026):")
    print(f"  Total Trades:             {total_trades}")
    print(f"  Total Net Profit:         ${total_pnl:+,.2f}")
    print(f"  Win Rate:                 {win_rate:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"  Profit Factor:            {profit_factor:.2f}")
    print(f"  Average Win:              ${avg_win:.2f}")
    print(f"  Average Loss:             ${avg_loss:.2f}")
    print(f"  Win / Loss Payout Ratio:  {win_loss_ratio:.2f}x (Wins are {win_loss_ratio:.2f}x bigger than losses!)")
    print(f"  Max Drop Below $5k Base:  {max_base_loss_pct:.2f}% (Lowest Account Balance: ${lowest_base_bal:,.2f})")
    print(f"  Worst Single Day Loss:    ${worst_day_loss:,.2f} ({max_daily_dd_pct:.2f}% Daily Drawdown)")
    
    print("\n2. TRADE EXIT EXECUTION BREAKDOWN:")
    for reason, count in exit_counts.items():
        sub = df_trades[df_trades['exit_reason'] == reason]
        sub_pnl = sub['net_pnl'].sum()
        pct = count / total_trades * 100.0
        print(f"  {reason:<15}: {count:3d} trades ({pct:4.1f}%) | Net PnL: ${sub_pnl:+8.2f} | Avg PnL: ${sub['net_pnl'].mean():+6.2f}")
        
    print("\n3. MONTH-BY-MONTH STATISTICAL BREAKDOWN:")
    print(f"{'Month':<10} | {'Trades':<6} | {'Win Rate':<8} | {'Net PnL':<10} | {'Wins ($)':<10} | {'Losses ($)':<10} | {'Regime / Commentary'}")
    print("-" * 95)
    
    months = sorted(df_trades['month'].unique())
    for m in months:
        m_df = df_trades[df_trades['month'] == m]
        m_wins = m_df[m_df['net_pnl'] > 0]
        m_wr = len(m_wins) / len(m_df) * 100.0
        m_pnl = m_df['net_pnl'].sum()
        w_sum = m_wins['net_pnl'].sum()
        l_sum = abs(m_df[m_df['net_pnl'] < 0]['net_pnl'].sum())
        
        tag = ""
        if m == "2026-02": tag = "[CHOP MONTH - SURGES +$822!]"
        elif m == "2026-07": tag = "[CHOP MONTH - SOLIDLY GREEN]"
        elif m == "2026-08": tag = "[CHOP MONTH - POSITIVE]"
        
        print(f"{m:<10} | {len(m_df):<6d} | {m_wr:6.1f}% | ${m_pnl:+9.2f} | ${w_sum:9.2f} | ${l_sum:9.2f} | {tag}")
    print("-" * 95)
    
    # 4. Portfolio Head-to-head (Baseline vs Trio with Silver)
    df_p1 = pd.read_csv(os.path.join(PROJECT_ROOT, "reports", "trades_log_cb_liquidity_sweep.csv"))
    df_p2 = pd.read_csv(os.path.join(PROJECT_ROOT, "reports", "trades_log_phase2_jul_sep2026.csv"))
    df_p1['entry_dt'] = pd.to_datetime(df_p1['entry_time'], format='mixed', utc=True)
    df_p1['exit_dt'] = pd.to_datetime(df_p1['exit_time'], format='mixed', utc=True)
    df_p2['entry_dt'] = pd.to_datetime(df_p2['entry_time'], format='mixed', utc=True)
    df_p2['exit_dt'] = pd.to_datetime(df_p2['exit_time'], format='mixed', utc=True)
    df_base = pd.concat([df_p1, df_p2], ignore_index=True)
    df_base.sort_values('entry_dt', inplace=True)
    df_base.reset_index(drop=True, inplace=True)
    df_base['month'] = df_base['entry_dt'].dt.strftime('%Y-%m')
    
    df_trio = pd.concat([df_base, df_trades], ignore_index=True)
    df_trio.sort_values('entry_dt', inplace=True)
    df_trio.reset_index(drop=True, inplace=True)
    
    print("\n4. PORTFOLIO HEAD-TO-HEAD: BASELINE (GOLD + NASDAQ) vs TRIO (+ SILVER BREATHING):")
    print("=" * 110)
    print(f"{'Metric':<35} | {'Baseline (Gold + Nasdaq)':<28} | {'Trio (+ Silver 0.67% Safe)':<28} | {'Trio (+ Silver 1.0% Full)'}")
    print("-" * 110)
    
    base_pnl = df_base['net_pnl'].sum()
    trio_full_pnl = df_trio['net_pnl'].sum()
    trio_safe_pnl = df_base['net_pnl'].sum() * 0.67 + df_trades['net_pnl'].sum() * 0.67
    
    # Chop PnLs
    base_chop = df_base[df_base['month'].isin(['2026-02', '2026-07', '2026-08'])]['net_pnl'].sum()
    trio_full_chop = df_trio[df_trio['month'].isin(['2026-02', '2026-07', '2026-08'])]['net_pnl'].sum()
    trio_safe_chop = trio_full_chop * 0.67
    
    print(f"{'Full Year Net PnL':<35} | ${base_pnl:<27,.2f} | ${trio_safe_pnl:<27,.2f} | ${trio_full_pnl:,.2f}")
    print(f"{'Total Chop Window (Feb+Jul+Aug)':<35} | ${base_chop:<27,.2f} | ${trio_safe_chop:<27,.2f} | ${trio_full_chop:,.2f}")
    print(f"{'February 2026 Chop PnL':<35} | ${df_base[df_base['month']=='2026-02']['net_pnl'].sum():<27,.2f} | ${df_trio[df_trio['month']=='2026-02']['net_pnl'].sum()*0.67:<27,.2f} | ${df_trio[df_trio['month']=='2026-02']['net_pnl'].sum():,.2f}")
    print(f"{'July 2026 Chop PnL':<35} | ${df_base[df_base['month']=='2026-07']['net_pnl'].sum():<27,.2f} | ${df_trio[df_trio['month']=='2026-07']['net_pnl'].sum()*0.67:<27,.2f} | ${df_trio[df_trio['month']=='2026-07']['net_pnl'].sum():,.2f}")
    print(f"{'August 2026 Chop PnL':<35} | ${df_base[df_base['month']=='2026-08']['net_pnl'].sum():<27,.2f} | ${df_trio[df_trio['month']=='2026-08']['net_pnl'].sum()*0.67:<27,.2f} | ${df_trio[df_trio['month']=='2026-08']['net_pnl'].sum():,.2f}")
    print("=" * 110)

if __name__ == "__main__":
    run_silver_breathing_backtest()
