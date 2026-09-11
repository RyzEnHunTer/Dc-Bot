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
broker_offset = timedelta(hours=3)

def precompute_signals(sym: str):
    cfg = CANDIDATE_CONFIGS[sym]
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
    
    signals = np.zeros(len(eval_bars), dtype=np.int8)
    n = len(eval_bars)
    
    print(f"Precomputing signals for {sym} ({n} bars)...")
    for i in range(20, n):
        c_bar = eval_bars.iloc[i]
        p_bar = eval_bars.iloc[i-1]
        t = eval_bars.index[i]
        
        if not (6 <= t.hour < 21) or t.hour in [9, 13]:
            continue
            
        bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
        if bias == 0 or c_bar['adx_1h'] < 15:
            continue
        atr = float(c_bar['atr_1h'])
        if atr <= 0:
            continue
            
        c_diff = float(c_bar['ema9_5m']) - float(c_bar['ema20_5m'])
        p_diff = float(p_bar['ema9_5m']) - float(p_bar['ema20_5m'])
        vwap = float(c_bar['vwap_5m'])
        h1_e20 = float(c_bar['ema20_1h'])
        close_p = float(c_bar['close'])
        
        sig = 0
        if bias == 1 and p_diff <= 0 and c_diff > 0 and close_p > vwap and close_p > h1_e20:
            sig = 1
        elif bias == -1 and p_diff >= 0 and c_diff < 0 and close_p < vwap and close_p < h1_e20:
            sig = -1
            
        if sig != 0:
            has_sw, _, _ = detect_liquidity_sweep(df_m5, df_m5.index.get_loc(t), bias, 20, 8)
            if has_sw:
                signals[i] = sig
                
    eval_bars['signal'] = signals
    return eval_bars, cfg

def run_fast_sweep(sym: str):
    eval_bars, cfg = precompute_signals(sym)
    n = len(eval_bars)
    
    times = eval_bars.index.to_pydatetime()
    highs = eval_bars['high'].values
    lows = eval_bars['low'].values
    closes = eval_bars['close'].values
    atrs = eval_bars['atr_1h'].values
    sigs = eval_bars['signal'].values
    hours = np.array([t.hour for t in times])
    dates = np.array([t.strftime('%Y-%m-%d') for t in times])
    months = np.array([t.strftime('%Y-%m') for t in times])
    
    tick_size = cfg.tick_size
    tick_val = cfg.tick_value
    val_per_pt = tick_val / tick_size
    
    print(f"\n==================== {sym} PARAMETER & LOSS CAP SWEEP ====================")
    print(f"{'SL Mult':<8} | {'TP1 RR':<8} | {'LossCap':<8} | {'Trades':<6} | {'WR %':<6} | {'PF':<5} | {'Total PnL':<11} | {'Feb':<9} | {'Jul':<9} | {'Aug':<9} | {'Chop Total':<10}")
    print("-" * 110)
    
    for sl_m in [0.8, 1.0, 1.2, 1.4]:
        for tp1_rr in [1.2, 1.4, 1.6]:
            for cap_losses in [None, 2]:
                tp2_rr = tp1_rr * 1.5
                active = None
                trades = []
                daily_losses = 0
                current_day = ""
                
                for i in range(20, n):
                    day_str = dates[i]
                    if day_str != current_day:
                        current_day = day_str
                        daily_losses = 0
                        
                    h_p, l_p, c_p = highs[i], lows[i], closes[i]
                    t_hr = hours[i]
                    m_str = months[i]
                    
                    if active is not None:
                        is_buy = (active['dir'] == 1)
                        sl_hit = (l_p <= active['sl']) if is_buy else (h_p >= active['sl'])
                        tp1_hit = (h_p >= active['tp1']) if is_buy else (l_p <= active['tp1'])
                        tp2_hit = (h_p >= active['tp2']) if is_buy else (l_p <= active['tp2'])
                        eod_hit = (t_hr >= 21)
                        
                        if tp2_hit:
                            pts1 = active['tp1_dist']
                            pts2 = active['tp2_dist']
                            net = active['part_lots'] * pts1 * val_per_pt + active['run_lots'] * pts2 * val_per_pt - active['comm']
                            active['pnl'] = net
                            active['month'] = m_str
                            trades.append(active)
                            active = None
                            continue
                        elif sl_hit:
                            if active['tp1_hit']:
                                net = active['part_lots'] * active['tp1_dist'] * val_per_pt - active['comm']
                            else:
                                net = -50.0 - active['comm']
                                daily_losses += 1
                            active['pnl'] = net
                            active['month'] = m_str
                            trades.append(active)
                            active = None
                            continue
                        elif tp1_hit and not active['tp1_hit']:
                            active['tp1_hit'] = True
                            active['sl'] = active['entry']
                        elif eod_hit:
                            # EOD exit at close
                            pts = (c_p - active['entry']) if is_buy else (active['entry'] - c_p)
                            if active['tp1_hit']:
                                net = active['part_lots'] * active['tp1_dist'] * val_per_pt + active['run_lots'] * pts * val_per_pt - active['comm']
                            else:
                                net = active['total_lots'] * pts * val_per_pt - active['comm']
                                if net < 0:
                                    daily_losses += 1
                            active['pnl'] = net
                            active['month'] = m_str
                            trades.append(active)
                            active = None
                            continue
                            
                    if active is None:
                        if cap_losses is not None and daily_losses >= cap_losses:
                            continue
                        sig = sigs[i]
                        if sig != 0:
                            atr = atrs[i]
                            sl_pts = sl_m * atr
                            sl_dist_price = sl_pts * tick_size if sym in ["US30", "GER40", "UK100"] else sl_pts
                            
                            # Sizing
                            raw_lots = 50.0 / (sl_pts * val_per_pt)
                            lots = max(0.01, round(raw_lots, 2))
                            p_lots = round(lots * 0.5, 2)
                            r_lots = round(lots - p_lots, 2)
                            
                            entry = c_p
                            tp1_dist = tp1_rr * sl_pts
                            tp2_dist = tp2_rr * sl_pts
                            
                            sl_price = entry - sl_pts if sig == 1 else entry + sl_pts
                            tp1_price = entry + tp1_dist if sig == 1 else entry - tp1_dist
                            tp2_price = entry + tp2_dist if sig == 1 else entry - tp2_dist
                            
                            active = {
                                'dir': sig, 'entry': entry,
                                'sl': sl_price, 'tp1': tp1_price, 'tp2': tp2_price,
                                'tp1_dist': tp1_dist, 'tp2_dist': tp2_dist,
                                'total_lots': lots, 'part_lots': p_lots, 'run_lots': r_lots,
                                'comm': lots * 5.0,
                                'tp1_hit': False
                            }
                            
                df_res = pd.DataFrame(trades)
                if len(df_res) == 0: continue
                tot_pnl = df_res['pnl'].sum()
                wr = (df_res['pnl'] > 0).mean() * 100
                w = df_res[df_res['pnl'] > 0]['pnl'].sum()
                l = abs(df_res[df_res['pnl'] < 0]['pnl'].sum())
                pf = w / l if l > 0 else 999.0
                
                feb = df_res[df_res['month'] == '2026-02']['pnl'].sum() if '2026-02' in df_res['month'].values else 0
                jul = df_res[df_res['month'] == '2026-07']['pnl'].sum() if '2026-07' in df_res['month'].values else 0
                aug = df_res[df_res['month'] == '2026-08']['pnl'].sum() if '2026-08' in df_res['month'].values else 0
                chop = feb + jul + aug
                cap_str = str(cap_losses) if cap_losses is not None else "None"
                print(f"{sl_m:<8.1f} | {tp1_rr:<8.1f} | {cap_str:<8} | {len(df_res):<6d} | {wr:<5.1f}% | {pf:<5.2f} | ${tot_pnl:+10.2f} | ${feb:+8.2f} | ${jul:+8.2f} | ${aug:+8.2f} | ${chop:+9.2f}")

if __name__ == "__main__":
    run_fast_sweep("AUDJPY")
    run_fast_sweep("JPN225")
    run_fast_sweep("XAGUSD")
