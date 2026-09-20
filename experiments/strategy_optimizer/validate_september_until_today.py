"""
September Forensic Validation: Baseline vs Version A vs Version B
Evaluates live MT5 broker data from September 1, 2026 through September 18, 2026 (Today).
Simulates:
1. Baseline Locked (Original DCC Strategy)
2. Version A: Anti-Chop (1H Stretch >= 0.40x) + No-Chase (5M Chase <= 0.50x)
3. Version B: Anti-Chop (1H Stretch >= 0.40x) + ADX Ceiling (1H ADX <= 45.0) + No-Chase (5M Chase <= 0.50x)
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dcc_engine import DCCEngine
from experiments.strategy_optimizer.optimizer_engine import detect_liquidity_sweep_fast

SPECS = {
    "XAUUSD": {"tick_size": 0.01, "tick_val": 1.0, "atr_sl_mult": 0.90, "tp1_rr": 1.4, "tp2_rr": 2.2, "comm_per_lot": 5.0},
    "NAS100": {"tick_size": 0.1, "tick_val": 0.1, "atr_sl_mult": 1.00, "tp1_rr": 1.5, "tp2_rr": 2.0, "comm_per_lot": 5.0}
}

broker_offset = timedelta(hours=3)

def fetch_mt5_data(sym: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Fetches high-resolution M5/H1/H2 bars from MT5 with warm-up from mid-August."""
    if not mt5.initialize():
        raise RuntimeError("Failed to initialize MT5")

    matched = sym
    if not mt5.symbol_select(sym, True):
        for alias in [f"{sym}.m", f"{sym}_i", f"{sym}pro", "GOLD" if "XAU" in sym else "USTEC"]:
            if mt5.symbol_select(alias, True):
                matched = alias
                break

    start_warmup = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
    end_now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

    r_m5 = mt5.copy_rates_range(matched, mt5.TIMEFRAME_M5, start_warmup, end_now)
    r_h1 = mt5.copy_rates_range(matched, mt5.TIMEFRAME_H1, start_warmup, end_now)
    r_h2 = mt5.copy_rates_range(matched, mt5.TIMEFRAME_H2, start_warmup, end_now)

    df_m5 = pd.DataFrame(r_m5)
    df_m5['time'] = (pd.to_datetime(df_m5['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_m5.set_index('time', inplace=True)
    df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)

    df_h1 = pd.DataFrame(r_h1)
    df_h1['time'] = (pd.to_datetime(df_h1['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_h1.set_index('time', inplace=True)

    df_h2 = pd.DataFrame(r_h2)
    df_h2['time'] = (pd.to_datetime(df_h2['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_h2.set_index('time', inplace=True)

    cfg = SPECS[sym]
    engine = DCCEngine(atr_sl_multiplier=cfg["atr_sl_mult"], risk_reward_ratio=cfg["tp1_rr"])
    df_prep = engine.prepare_data(df_m5, df_h1, df_h2)

    df_prep['dist_to_h1_ema20'] = (df_prep['close'] - df_prep['ema20_1h']).abs()
    df_prep['stretch_ratio'] = df_prep['dist_to_h1_ema20'] / (df_prep['atr_1h'] + 1e-9)
    df_prep['dist_to_5m_ema9'] = (df_prep['close'] - df_prep['ema9_5m']).abs()
    df_prep['chase_ratio'] = df_prep['dist_to_5m_ema9'] / (df_prep['atr_1h'] + 1e-9)

    # Truncate strictly to September 1, 2026 onwards
    eval_bars = df_prep[df_prep.index >= '2026-09-01 00:00:00'].copy()
    return eval_bars, df_m5


def simulate_version(
    name: str,
    min_stretch: Optional[float] = None,
    max_adx: Optional[float] = None,
    max_chase: Optional[float] = None,
    blocked_hours: Optional[List[int]] = None
) -> pd.DataFrame:
    all_trades = []
    blocked_hours = blocked_hours if blocked_hours is not None else [9, 13]

    for sym in ["XAUUSD", "NAS100"]:
        eval_bars, df_m5 = fetch_mt5_data(sym)
        sym_spec = SPECS[sym]
        val_per_pt = sym_spec["tick_val"] / sym_spec["tick_size"]
        sl_multiplier = sym_spec["atr_sl_mult"]
        comm_per_lot = sym_spec["comm_per_lot"]

        active_trade = None
        daily_losses = 0
        current_day = ""
        n_bars = len(eval_bars)

        for i in range(25, n_bars):
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

            # Manage Active Trade
            if active_trade is not None:
                pos = active_trade
                is_buy = (pos['direction'] == "BUY")
                sl_hit = (low_p <= pos['current_sl']) if is_buy else (high_p >= pos['current_sl'])
                tp1_hit = (high_p >= pos['tp1_price']) if is_buy else (low_p <= pos['tp1_price'])
                tp2_hit = (high_p >= pos['tp2_price']) if is_buy else (low_p <= pos['tp2_price'])

                if tp2_hit:
                    pos['exit_time'] = t
                    pos['exit_price'] = pos['tp2_price']
                    pos['exit_reason'] = "FULL_TP2"
                    p1 = pos['part_lots'] * pos['tp1_dist'] * val_per_pt
                    p2 = pos['run_lots'] * pos['tp2_dist'] * val_per_pt
                    comm = pos['total_lots'] * comm_per_lot
                    pos['net_pnl'] = round(p1 + p2 - comm, 2)
                    all_trades.append(pos)
                    active_trade = None
                    continue

                elif sl_hit:
                    pos['exit_time'] = t
                    pos['exit_price'] = pos['current_sl']
                    comm = pos['total_lots'] * comm_per_lot
                    if pos['tp1_hit']:
                        pos['exit_reason'] = "TP1_THEN_BE"
                        p1 = pos['part_lots'] * pos['tp1_dist'] * val_per_pt
                        net = p1 - comm
                    else:
                        pos['exit_reason'] = "SL"
                        risk_lost = pos['total_lots'] * pos['sl_dist'] * val_per_pt
                        net = -risk_lost - comm
                        daily_losses += 1
                    pos['net_pnl'] = round(net, 2)
                    all_trades.append(pos)
                    active_trade = None
                    continue

                elif tp1_hit and not pos['tp1_hit']:
                    pos['tp1_hit'] = True
                    pos['current_sl'] = pos['entry_price']

                elif i == n_bars - 1:
                    pos['exit_time'] = t
                    pos['exit_price'] = close_p
                    pos['exit_reason'] = "OPEN"
                    comm = pos['total_lots'] * comm_per_lot
                    run_pts = (close_p - pos['entry_price']) if is_buy else (pos['entry_price'] - close_p)
                    p2 = pos['run_lots'] * run_pts * val_per_pt
                    p1 = (pos['part_lots'] * pos['tp1_dist'] * val_per_pt) if pos['tp1_hit'] else (pos['part_lots'] * run_pts * val_per_pt)
                    pos['net_pnl'] = round(p1 + p2 - comm, 2)
                    all_trades.append(pos)
                    active_trade = None
                    continue

            # Check Entry
            if active_trade is None:
                if daily_losses >= 2:
                    continue
                if not (6 <= t_hour < 21):
                    continue
                if blocked_hours and t_hour in blocked_hours:
                    continue

                bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
                if bias == 0:
                    continue
                
                adx = float(c_bar['adx_1h'])
                if adx < 15.0:
                    continue
                if max_adx is not None and adx > max_adx:
                    continue

                atr = float(c_bar['atr_1h'])
                if atr <= 0:
                    continue

                stretch = float(c_bar['stretch_ratio'])
                if min_stretch is not None and stretch < min_stretch:
                    continue

                chase = float(c_bar['chase_ratio'])
                if max_chase is not None and chase > max_chase:
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
                    m5_idx = df_m5.index.get_loc(t)
                    has_sw, _, _ = detect_liquidity_sweep_fast(df_m5, m5_idx, bias, 20, 8)
                    if not has_sw:
                        continue

                    sl_dist = sl_multiplier * atr
                    tp1_dist = sym_spec["tp1_rr"] * sl_dist
                    tp2_dist = sym_spec["tp2_rr"] * sl_dist
                    entry_p = close_p
                    
                    sl_p = entry_p - sl_dist if sig_type == 1 else entry_p + sl_dist
                    tp1_p = entry_p + tp1_dist if sig_type == 1 else entry_p - tp1_dist
                    tp2_p = entry_p + tp2_dist if sig_type == 1 else entry_p - tp2_dist

                    raw_lots = 50.0 / (sl_dist * val_per_pt)
                    tot_lots = max(0.01, round(raw_lots, 2))
                    part_lots = round(tot_lots * 0.5, 2)
                    run_lots = round(tot_lots - part_lots, 2)

                    active_trade = {
                        "version": name,
                        "symbol": sym,
                        "direction": "BUY" if sig_type == 1 else "SELL",
                        "entry_time": t,
                        "exit_time": None,
                        "entry_price": entry_p,
                        "exit_price": None,
                        "current_sl": sl_p,
                        "sl_dist": sl_dist,
                        "tp1_dist": tp1_dist,
                        "tp2_dist": tp2_dist,
                        "tp1_price": tp1_p,
                        "tp2_price": tp2_p,
                        "total_lots": tot_lots,
                        "part_lots": part_lots,
                        "run_lots": run_lots,
                        "tp1_hit": False,
                        "tp2_hit": False,
                        "stretch_ratio": stretch,
                        "chase_ratio": chase,
                        "adx_1h": adx
                    }

    if not all_trades:
        return pd.DataFrame()
    df_res = pd.DataFrame(all_trades)
    df_res['entry_dt'] = pd.to_datetime(df_res['entry_time'])
    df_res['exit_dt'] = pd.to_datetime(df_res['exit_time'])
    return df_res.sort_values('entry_dt').reset_index(drop=True)


def print_scorecard(label: str, df: pd.DataFrame):
    if df.empty:
        print(f"[{label}] No trades found.")
        return

    tot = len(df)
    wins = df[df['net_pnl'] > 0]
    losses = df[df['net_pnl'] <= 0]
    wr = len(wins) / tot * 100
    pnl = df['net_pnl'].sum()
    gp = wins['net_pnl'].sum()
    gl = abs(losses['net_pnl'].sum())
    pf = gp / gl if gl > 0 else 999.0

    df['entry_date'] = df['entry_dt'].dt.strftime('%Y-%m-%d')
    daily_pnl = df.groupby('entry_date')['net_pnl'].sum()
    worst_day = daily_pnl.min()

    # Peak-to-trough DD
    bal = 5000.0
    equity = [bal]
    for p in df['net_pnl']:
        bal += p
        equity.append(bal)
    eq_s = pd.Series(equity)
    peak = eq_s.cummax()
    max_dd = ((peak - eq_s) / peak * 100).max()

    print(f"\n{'=' * 85}")
    print(f"REPORT: {label} (SEPTEMBER 1 - SEPTEMBER 18, 2026)")
    print(f"{'=' * 85}")
    print(f"Total Trades Taken:       {tot}")
    print(f"Winning Trades:           {len(wins)} ({wr:.1f}%)")
    print(f"Losing Trades:            {len(losses)} ({100 - wr:.1f}%)")
    print(f"Profit Factor:            {pf:.2f}")
    print(f"Total Net Profit:         {'+$' if pnl >= 0 else '-$'}{abs(pnl):,.2f}")
    print(f"Max Peak-to-Trough DD:    {max_dd:.2f}%")
    print(f"Worst Single Day Loss:    {'-$' if worst_day < 0 else '+$'}{abs(worst_day):,.2f}")
    
    print("\nDay-by-Day Performance in September:")
    for d, d_pnl in daily_pnl.items():
        status = "[GREEN]" if d_pnl >= 0 else "[RED]"
        print(f"  {d}: {'+$' if d_pnl >= 0 else '-$'}{abs(d_pnl):>8,.2f}  {status}")


def main():
    mt5.initialize()
    
    # 1. Baseline Locked
    print("Running Baseline Locked...")
    df_base = simulate_version("Baseline Locked", min_stretch=None, max_adx=None, max_chase=None)

    # 2. Version A (High ROI: Stretch >= 0.40, Chase <= 0.50)
    print("Running Version A...")
    df_verA = simulate_version("Version A", min_stretch=0.40, max_adx=None, max_chase=0.50)

    # 3. Version B (Max Safety: Stretch >= 0.40, ADX <= 45, Chase <= 0.50)
    print("Running Version B...")
    df_verB = simulate_version("Version B", min_stretch=0.40, max_adx=45.0, max_chase=0.50)

    print_scorecard("1. BASELINE LOCKED (ORIGINAL)", df_base)
    print_scorecard("2. VERSION A (HIGH ROI: STRETCH >= 0.40x, CHASE <= 0.50x)", df_verA)
    print_scorecard("3. VERSION B (MAX SAFETY: STRETCH >= 0.40x, ADX <= 45, CHASE <= 0.50x)", df_verB)

    # Forensic on Tuesday, Sep 15
    print("\n" + "=" * 85)
    print("TUESDAY, SEPTEMBER 15 FORENSIC COMPARISON (THE TOPPING-OUT DAY)")
    print("=" * 85)
    for name, d_frame in [("Baseline", df_base), ("Version A", df_verA), ("Version B", df_verB)]:
        sep15 = d_frame[d_frame['entry_date'] == '2026-09-15']
        pnl_15 = sep15['net_pnl'].sum() if not sep15.empty else 0.0
        print(f"\n[{name}] Trades on Sep 15: {len(sep15)} | Net PnL: {'+$' if pnl_15 >= 0 else '-$'}{abs(pnl_15):,.2f}")
        for _, r in sep15.iterrows():
            print(f"  {r['symbol']} {r['direction']} at {str(r['entry_time'])[11:16]} UTC | Exit: {r['exit_reason']} | PnL: {'+$' if r['net_pnl'] >= 0 else '-$'}{abs(r['net_pnl']):.2f} | 1H ADX: {r['adx_1h']:.1f} | Stretch: {r['stretch_ratio']:.2f}x | Chase: {r['chase_ratio']:.2f}x")

    mt5.shutdown()

if __name__ == "__main__":
    main()
