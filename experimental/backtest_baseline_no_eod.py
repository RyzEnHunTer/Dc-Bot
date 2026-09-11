"""
Forensic Backtest: Baseline Strategy (Gold XAUUSD + Nasdaq NAS100) WITHOUT EOD Exit.
Runs the exact Flagship Strategy with 5M Liquidity Sweep Confluence & Daily 2-Loss Cap:
1. Version A: Standard EOD Exit (Closed at 21:00 UTC)
2. Version B: NO EOD Exit (Holding overnight until TP2 / BE / SL is reached)
Compares both across all of 2026 (Jan 1 - Sep 8, 2026).
"""

import os
import sys
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dcc_engine import DCCEngine
from experimental.test_5m_liquidity_sweep import detect_liquidity_sweep

CACHE_DIR = os.path.join(PROJECT_ROOT, "data_cache")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
broker_offset = timedelta(hours=3)

SPECS = {
    "XAUUSD": {
        "tick_size": 0.01,
        "tick_val": 1.0,
        "contract_size": 100.0,
        "atr_sl_mult": 1.3,
        "tp1_rr": 1.4,
        "tp2_rr": 2.1,
        "adx_min": 15.0,
        "comm_per_lot": 5.0
    },
    "NAS100": {
        "tick_size": 0.1,
        "tick_val": 0.1,
        "contract_size": 10.0,
        "atr_sl_mult": 1.4,
        "tp1_rr": 1.4,
        "tp2_rr": 2.1,
        "adx_min": 15.0,
        "comm_per_lot": 5.0
    }
}

def load_data(sym: str):
    df_m5 = pd.read_parquet(f"{CACHE_DIR}/m5_bars_{sym}.parquet")
    df_m5['time'] = (pd.to_datetime(df_m5['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_m5.set_index('time', inplace=True)
    df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)
    
    df_1h = pd.read_parquet(f"{CACHE_DIR}/h1_bars_{sym}.parquet")
    df_1h['time'] = (pd.to_datetime(df_1h['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_1h.set_index('time', inplace=True)
    
    df_2h = pd.read_parquet(f"{CACHE_DIR}/h2_bars_{sym}.parquet")
    df_2h['time'] = (pd.to_datetime(df_2h['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_2h.set_index('time', inplace=True)
    
    cfg = SPECS[sym]
    engine = DCCEngine(atr_sl_multiplier=cfg["atr_sl_mult"], risk_reward_ratio=cfg["tp1_rr"])
    df_prep = engine.prepare_data(df_m5, df_1h, df_2h)
    eval_bars = df_prep[(df_prep.index >= '2026-01-01') & (df_prep.index <= '2026-09-08')].copy()
    return eval_bars, df_m5, cfg

def simulate_symbol(sym: str, allow_overnight: bool):
    eval_bars, df_m5, cfg = load_data(sym)
    
    tick_size = cfg["tick_size"]
    tick_val = cfg["tick_val"]
    val_per_pt = tick_val / tick_size
    sl_multiplier = cfg["atr_sl_mult"]
    tp1_rr = cfg["tp1_rr"]
    tp2_rr = cfg["tp2_rr"]
    
    active_trade = None
    completed_trades = []
    current_balance = 5000.0
    
    daily_losses = 0
    current_day = ""
    
    for i in range(20, len(eval_bars)):
        c_bar = eval_bars.iloc[i]
        p_bar = eval_bars.iloc[i - 1]
        t = eval_bars.index[i]
        t_hour = t.hour
        t_day = t.strftime('%Y-%m-%d')
        
        if t_day != current_day:
            current_day = t_day
            daily_losses = 0
            
        high_p = float(c_bar['high'])
        low_p = float(c_bar['low'])
        close_p = float(c_bar['close'])
        
        # 1. Manage Active Trade
        if active_trade is not None:
            pos = active_trade
            is_buy = (pos['direction'] == "BUY")
            sl_hit = (low_p <= pos['current_sl']) if is_buy else (high_p >= pos['current_sl'])
            tp1_hit = (high_p >= pos['tp1_price']) if is_buy else (low_p <= pos['tp1_price'])
            tp2_hit = (high_p >= pos['tp2_price']) if is_buy else (low_p <= pos['tp2_price'])
            eod_hit = (t_hour >= 21) if not allow_overnight else False
            
            if tp2_hit:
                pos['exit_time'] = t
                pos['exit_price'] = pos['tp2_price']
                pos['exit_reason'] = "FULL_TP2"
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                pts1 = pos['tp1_dist']
                pts2 = pos['tp2_dist']
                p1_dollars = pos['part_lots'] * pts1 * val_per_pt
                p2_dollars = pos['run_lots'] * pts2 * val_per_pt
                comm = pos['total_lots'] * cfg["comm_per_lot"]
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
                comm = pos['total_lots'] * cfg["comm_per_lot"]
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
                    daily_losses += 1
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
                pos['exit_reason'] = "EOD_EXIT"
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                comm = pos['total_lots'] * cfg["comm_per_lot"]
                run_pts = (close_p - pos['entry_price']) if is_buy else (pos['entry_price'] - close_p)
                p2_dollars = pos['run_lots'] * run_pts * val_per_pt
                p1_dollars = (pos['part_lots'] * pos['tp1_dist'] * val_per_pt) if pos['tp1_hit'] else (pos['part_lots'] * run_pts * val_per_pt)
                net = p1_dollars + p2_dollars - comm
                if net < 0:
                    daily_losses += 1
                pos['partial_pnl'] = round(p1_dollars, 2)
                pos['runner_pnl'] = round(p2_dollars, 2)
                pos['commission'] = round(comm, 2)
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue
                
            elif i == len(eval_bars) - 1:
                pos['exit_time'] = t
                pos['exit_price'] = close_p
                pos['exit_reason'] = "END_OF_DATA"
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                comm = pos['total_lots'] * cfg["comm_per_lot"]
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
            # Circuit breaker check: max 2 losses per day
            if daily_losses >= 2:
                continue
                
            # Session filter (06 to 21 UTC, excluding trap hours 9, 13)
            if not (6 <= t_hour < 21) or t_hour in [9, 13]:
                continue
                
            bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
            if bias == 0 or c_bar['adx_1h'] < cfg["adx_min"]:
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
    df_trades['entry_dt'] = pd.to_datetime(df_trades['entry_time'])
    df_trades['exit_dt'] = pd.to_datetime(df_trades['exit_time'])
    df_trades['month'] = df_trades['entry_dt'].dt.strftime('%Y-%m')
    df_trades['date'] = df_trades['exit_dt'].dt.strftime('%Y-%m-%d')
    return df_trades

def run_comparative_audit():
    print("=" * 115)
    print("      BASELINE (GOLD + NASDAQ) FORENSIC AUDIT: WITH EOD EXIT vs NO EOD EXIT (2026)")
    print("      Strategy: DCC Flagship + 5M Liquidity Sweep Confluence + Daily 2-Loss Cap")
    print("=" * 115)
    
    # 1. Simulate With EOD
    xau_eod = simulate_symbol("XAUUSD", allow_overnight=False)
    nas_eod = simulate_symbol("NAS100", allow_overnight=False)
    base_eod = pd.concat([xau_eod, nas_eod]).sort_values('entry_dt').reset_index(drop=True)
    
    # 2. Simulate Without EOD (Holding Overnight)
    xau_noeod = simulate_symbol("XAUUSD", allow_overnight=True)
    nas_noeod = simulate_symbol("NAS100", allow_overnight=True)
    base_noeod = pd.concat([xau_noeod, nas_noeod]).sort_values('entry_dt').reset_index(drop=True)
    
    # Save CSVs
    base_eod.to_csv(os.path.join(REPORTS_DIR, "trades_baseline_with_eod.csv"), index=False)
    base_noeod.to_csv(os.path.join(REPORTS_DIR, "trades_baseline_no_eod.csv"), index=False)
    
    def calc_stats(df):
        tot = len(df)
        wins = df[df['net_pnl'] > 0]
        losses = df[df['net_pnl'] < 0]
        wr = len(wins) / tot * 100.0 if tot > 0 else 0
        pnl = df['net_pnl'].sum()
        gw = wins['net_pnl'].sum()
        gl = abs(losses['net_pnl'].sum())
        pf = gw / gl if gl > 0 else 999.0
        avg_w = wins['net_pnl'].mean() if len(wins) > 0 else 0
        avg_l = abs(losses['net_pnl'].mean()) if len(losses) > 0 else 0
        ratio = avg_w / avg_l if avg_l > 0 else 999.0
        
        # Prop firm payouts (14-day reset)
        bal = 5000.0
        lowest_bal = 5000.0
        c_start = df.iloc[0]['entry_dt']
        payouts = []
        for i, r in df.iterrows():
            bal += r['net_pnl']
            if bal < lowest_bal: lowest_bal = bal
            days = (r['exit_dt'] - c_start).total_seconds() / 86400.0
            if days >= 14.0 or i == len(df) - 1:
                payouts.append(max(0.0, bal - 5000.0) * 0.80)
                bal = 5000.0
                c_start = r['exit_dt']
        cash = sum(payouts)
        base_dd = (5000.0 - lowest_bal) / 50.0 if lowest_bal < 5000.0 else 0.0
        
        daily = df.groupby('date')['net_pnl'].sum()
        worst_day = daily.min() if len(daily) > 0 else 0
        daily_dd = abs(worst_day) / 50.0 if worst_day < 0 else 0
        
        feb = df[df['month'] == '2026-02']['net_pnl'].sum() if '2026-02' in df['month'].values else 0
        jul = df[df['month'] == '2026-07']['net_pnl'].sum() if '2026-07' in df['month'].values else 0
        aug = df[df['month'] == '2026-08']['net_pnl'].sum() if '2026-08' in df['month'].values else 0
        chop = feb + jul + aug
        
        return {
            "trades": tot, "pnl": pnl, "wr": wr, "pf": pf,
            "avg_w": avg_w, "avg_l": avg_l, "ratio": ratio,
            "cash": cash, "base_dd": base_dd, "lowest_bal": lowest_bal,
            "worst_day": worst_day, "daily_dd": daily_dd,
            "feb": feb, "jul": jul, "aug": aug, "chop": chop,
            "exits": df['exit_reason'].value_counts().to_dict()
        }
        
    s_eod = calc_stats(base_eod)
    s_noeod = calc_stats(base_noeod)
    
    print("\n1. LIFETIME HEAD-TO-HEAD: WITH EOD CLOSE vs WITHOUT EOD CLOSE:")
    print("=" * 115)
    print(f"{'Metric':<35} | {'With EOD Exit (21:00 UTC)':<32} | {'NO EOD Exit (Overnight Hold)':<30} | {'Edge / Difference'}")
    print("-" * 115)
    print(f"{'Total Trades Executed':<35} | {s_eod['trades']:<32d} | {s_noeod['trades']:<30d} | {s_noeod['trades'] - s_eod['trades']} trades")
    print(f"{'Full Year Net Profit ($)':<35} | ${s_eod['pnl']:<31,.2f} | ${s_noeod['pnl']:<29,.2f} | ${s_noeod['pnl'] - s_eod['pnl']:+,.2f} ({(s_noeod['pnl']-s_eod['pnl'])/s_eod['pnl']*100:+.1f}%)")
    print(f"{'Profit Factor':<35} | {s_eod['pf']:<32.2f} | {s_noeod['pf']:<30.2f} | {s_noeod['pf'] - s_eod['pf']:+.2f}")
    print(f"{'Win Rate (%)':<35} | {s_eod['wr']:<32.1f}% | {s_noeod['wr']:<30.1f}% | {s_noeod['wr'] - s_eod['wr']:+.1f}%")
    print(f"{'Average Win ($)':<35} | ${s_eod['avg_w']:<31.2f} | ${s_noeod['avg_w']:<29.2f} | ${s_noeod['avg_w'] - s_eod['avg_w']:+.2f}")
    print(f"{'Average Loss ($)':<35} | ${s_eod['avg_l']:<31.2f} | ${s_noeod['avg_l']:<29.2f} | ${s_noeod['avg_l'] - s_eod['avg_l']:+.2f}")
    print(f"{'Win / Loss Payout Ratio':<35} | {s_eod['ratio']:<32.2f}x | {s_noeod['ratio']:<30.2f}x | {s_noeod['ratio'] - s_eod['ratio']:+.2f}x")
    print(f"{'14-Day Banked Cash (80% Split)':<35} | ${s_eod['cash']:<31,.2f} | ${s_noeod['cash']:<29,.2f} | ${s_noeod['cash'] - s_eod['cash']:+,.2f}")
    print(f"{'Max Base Drawdown %':<35} | {s_eod['base_dd']:<32.2f}% | {s_noeod['base_dd']:<30.2f}% | {s_noeod['base_dd'] - s_eod['base_dd']:+.2f}%")
    print(f"{'Lowest Account Balance':<35} | ${s_eod['lowest_bal']:<31,.2f} | ${s_noeod['lowest_bal']:<29,.2f} | ${s_noeod['lowest_bal'] - s_eod['lowest_bal']:+,.2f}")
    print(f"{'Worst Single Day Loss ($)':<35} | ${s_eod['worst_day']:<31,.2f} | ${s_noeod['worst_day']:<29,.2f} | ${s_noeod['worst_day'] - s_eod['worst_day']:+,.2f}")
    print(f"{'Max Daily Drawdown %':<35} | {s_eod['daily_dd']:<32.2f}% | {s_noeod['daily_dd']:<30.2f}% | {s_noeod['daily_dd'] - s_eod['daily_dd']:+.2f}%")
    print("=" * 115)
    
    print("\n2. EXIT REASON BREAKDOWN:")
    print(f"{'Exit Reason':<20} | {'With EOD Exit':<20} | {'NO EOD Exit (Overnight Hold)'}")
    print("-" * 65)
    all_reasons = set(s_eod['exits'].keys()).union(set(s_noeod['exits'].keys()))
    for r in sorted(all_reasons):
        c1 = s_eod['exits'].get(r, 0)
        c2 = s_noeod['exits'].get(r, 0)
        print(f"{r:<20} | {c1:<20d} | {c2:<20d}")
    print("-" * 65)
    
    print("\n3. MONTH-BY-MONTH HEAD-TO-HEAD COMPARISON ($):")
    print(f"{'Month':<10} | {'With EOD Exit':<22} | {'NO EOD Exit':<22} | {'Difference':<15} | {'Regime'}")
    print("-" * 80)
    months = sorted(base_eod['month'].unique())
    for m in months:
        p1 = base_eod[base_eod['month'] == m]['net_pnl'].sum() if m in base_eod['month'].values else 0
        p2 = base_noeod[base_noeod['month'] == m]['net_pnl'].sum() if m in base_noeod['month'].values else 0
        diff = p2 - p1
        tag = "[CHOP MONTH]" if m in ['2026-02', '2026-07', '2026-08'] else ""
        print(f"{m:<10} | ${p1:<21,.2f} | ${p2:<21,.2f} | ${diff:<14,.2f} | {tag}")
    print("-" * 80)
    print(f"{'CHOP TOTAL':<10} | ${s_eod['chop']:<21,.2f} | ${s_noeod['chop']:<21,.2f} | ${s_noeod['chop'] - s_eod['chop']:<14,.2f} | [Feb+Jul+Aug]")
    print(f"{'FULL TOTAL':<10} | ${s_eod['pnl']:<21,.2f} | ${s_noeod['pnl']:<21,.2f} | ${s_noeod['pnl'] - s_eod['pnl']:<14,.2f} | [Full 2026]")
    print("=" * 80)

if __name__ == "__main__":
    run_comparative_audit()
