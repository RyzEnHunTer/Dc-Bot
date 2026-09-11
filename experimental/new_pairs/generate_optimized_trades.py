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

def generate_trades(sym: str, sl_multiplier: float, tp1_rr: float, suffix: str):
    cfg = CANDIDATE_CONFIGS[sym]
    tp2_rr = tp1_rr * 1.5
    
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
    
    engine = DCCEngine(atr_sl_multiplier=1.0, risk_reward_ratio=1.4)
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
                
    df_out = pd.DataFrame(completed_trades)
    out_file = os.path.join(REPORTS_DIR, f"trades_{sym}_{suffix}.csv")
    df_out.to_csv(out_file, index=False)
    print(f"Generated {out_file}: {len(df_out)} trades | Net PnL: ${df_out['net_pnl'].sum():+,.2f}")
    return df_out

if __name__ == "__main__":
    generate_trades("XAGUSD", sl_multiplier=1.4, tp1_rr=1.6, suffix="opt_1.4_1.6")
    generate_trades("AUDJPY", sl_multiplier=1.2, tp1_rr=1.4, suffix="opt_1.2_1.4")
