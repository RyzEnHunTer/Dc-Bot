import os
import sys
import pandas as pd
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dcc_engine import DCCEngine
from experimental.test_5m_liquidity_sweep import detect_liquidity_sweep

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
broker_offset = timedelta(hours=3)

def run_sweep(sym):
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
    eval_bars = df_prep[(df_prep.index >= '2026-01-01') & (df_prep.index <= '2026-09-08')]
    
    print(f"\n==================== {sym} PARAMETER SWEEP ====================")
    sl_mults = [0.8, 1.0, 1.2, 1.4] if sym == "US30" else [1.0, 1.2, 1.4]
    rr_list = [1.2, 1.4, 1.6]
    
    for sl_m in sl_mults:
        for tp1_rr in rr_list:
            tp2_rr = tp1_rr * 1.5
            active = None
            trades = []
            
            for i in range(20, len(eval_bars)):
                c_bar = eval_bars.iloc[i]
                p_bar = eval_bars.iloc[i-1]
                t = eval_bars.index[i]
                high_p, low_p, close_p = float(c_bar['high']), float(c_bar['low']), float(c_bar['close'])
                
                if active is not None:
                    is_buy = (active['dir'] == 1)
                    sl_hit = (low_p <= active['sl']) if is_buy else (high_p >= active['sl'])
                    tp1_hit = (high_p >= active['tp1']) if is_buy else (low_p <= active['tp1'])
                    tp2_hit = (high_p >= active['tp2']) if is_buy else (low_p <= active['tp2'])
                    
                    if tp2_hit:
                        pnl = 50.0 * 0.5 * tp1_rr + 50.0 * 0.5 * tp2_rr - 1.0
                        active['exit_pnl'] = pnl
                        active['month'] = t.strftime('%Y-%m')
                        trades.append(active)
                        active = None
                        continue
                    elif sl_hit:
                        pnl = (50.0 * 0.5 * tp1_rr - 1.0) if active['tp1_hit'] else (-50.0 - 1.0)
                        active['exit_pnl'] = pnl
                        active['month'] = t.strftime('%Y-%m')
                        trades.append(active)
                        active = None
                        continue
                    elif tp1_hit and not active['tp1_hit']:
                        active['tp1_hit'] = True
                        active['sl'] = active['entry']
                    elif t.hour >= 21:
                        pnl = 0.0
                        active['exit_pnl'] = pnl
                        active['month'] = t.strftime('%Y-%m')
                        trades.append(active)
                        active = None
                        continue
                        
                if active is None:
                    if not (6 <= t.hour < 21) or t.hour in [9, 13]: continue
                    bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
                    if bias == 0 or c_bar['adx_1h'] < 15: continue
                    atr = float(c_bar['atr_1h'])
                    if atr <= 0: continue
                    
                    c_diff = float(c_bar['ema9_5m']) - float(c_bar['ema20_5m'])
                    p_diff = float(p_bar['ema9_5m']) - float(p_bar['ema20_5m'])
                    vwap = float(c_bar['vwap_5m'])
                    h1_e20 = float(c_bar['ema20_1h'])
                    
                    has_sw, _, _ = detect_liquidity_sweep(df_m5, df_m5.index.get_loc(t), bias, 20, 8)
                    if not has_sw: continue
                    
                    sig = 0
                    if bias == 1 and p_diff <= 0 and c_diff > 0 and close_p > vwap and close_p > h1_e20: sig = 1
                    elif bias == -1 and p_diff >= 0 and c_diff < 0 and close_p < vwap and close_p < h1_e20: sig = -1
                    
                    if sig != 0:
                        sl_d = sl_m * atr
                        entry = close_p
                        active = {
                            'dir': sig, 'entry': entry,
                            'sl': entry - sl_d if sig == 1 else entry + sl_d,
                            'tp1': entry + tp1_rr * sl_d if sig == 1 else entry - tp1_rr * sl_d,
                            'tp2': entry + tp2_rr * sl_d if sig == 1 else entry - tp2_rr * sl_d,
                            'tp1_hit': False
                        }
            dft = pd.DataFrame(trades)
            if len(dft) == 0: continue
            tot = dft['exit_pnl'].sum()
            feb = dft[dft['month'] == '2026-02']['exit_pnl'].sum() if len(dft[dft['month'] == '2026-02']) > 0 else 0
            jul = dft[dft['month'] == '2026-07']['exit_pnl'].sum() if len(dft[dft['month'] == '2026-07']) > 0 else 0
            aug = dft[dft['month'] == '2026-08']['exit_pnl'].sum() if len(dft[dft['month'] == '2026-08']) > 0 else 0
            chop = feb + jul + aug
            print(f"SL={sl_m:.1f}x | TP1={tp1_rr:.1f}R | Trades: {len(dft):3d} | Total: ${tot:+7.2f} | Feb: ${feb:+7.2f} | Jul: ${jul:+7.2f} | Aug: ${aug:+7.2f} | Chop (Feb+Jul+Aug): ${chop:+7.2f}")

if __name__ == "__main__":
    run_sweep("US30")
    run_sweep("GBPJPY")
