"""
Comprehensive Forensic Audit of Deadzone (Trap Hours 09:00 & 13:00 UTC).
Investigates:
1. Is Deadzone blocking really working?
2. Does it work specifically in Ranging/Choppy markets or Trendy markets?
3. What happens if we dynamically allow Deadzone trades ONLY when the market is strongly trending?
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.strategy_optimizer.backtester import BacktestDataset
from experiments.strategy_optimizer.optimizer_engine import detect_liquidity_sweep_fast

SPECS = {
    "XAUUSD": {"tick_size": 0.01, "tick_val": 1.0, "atr_sl_mult": 1.3, "tp1_rr": 1.4, "tp2_rr": 2.1, "comm_per_lot": 5.0},
    "NAS100": {"tick_size": 0.1, "tick_val": 0.1, "atr_sl_mult": 1.4, "tp1_rr": 1.4, "tp2_rr": 2.1, "comm_per_lot": 5.0}
}


def simulate_all_hours(sym: str) -> pd.DataFrame:
    eval_bars, df_m5 = BacktestDataset.get_data(sym)
    cfg = SPECS[sym]
    tick_size = cfg["tick_size"]
    tick_val = cfg["tick_val"]
    val_per_pt = tick_val / tick_size
    sl_multiplier = cfg["atr_sl_mult"]
    comm_per_lot = cfg["comm_per_lot"]

    active_trade = None
    completed_trades = []
    current_day = ""
    daily_losses = 0

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
                completed_trades.append(pos)
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
                completed_trades.append(pos)
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
                completed_trades.append(pos)
                active_trade = None
                continue

        if active_trade is None:
            if daily_losses >= 2:
                continue
            # Session filter: 06:00 to 20:55 UTC (NO HOURS BLOCKED)
            if not (6 <= t_hour < 21):
                continue

            bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
            if bias == 0:
                continue

            adx = float(c_bar['adx_1h'])
            if adx < 15.0:
                continue

            atr = float(c_bar['atr_1h'])
            if atr <= 0:
                continue

            stretch = float(c_bar['stretch_ratio'])
            chase = abs(close_p - float(c_bar['ema9_5m'])) / atr

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
                tp1_dist = cfg["tp1_rr"] * sl_dist
                tp2_dist = cfg["tp2_rr"] * sl_dist
                entry_p = close_p

                sl_p = entry_p - sl_dist if sig_type == 1 else entry_p + sl_dist
                tp1_p = entry_p + tp1_dist if sig_type == 1 else entry_p - tp1_dist
                tp2_p = entry_p + tp2_dist if sig_type == 1 else entry_p - tp2_dist

                raw_lots = 50.0 / (sl_dist * val_per_pt)
                tot_lots = max(0.01, round(raw_lots, 2))
                part_lots = round(tot_lots * 0.5, 2)
                run_lots = round(tot_lots - part_lots, 2)

                active_trade = {
                    "symbol": sym,
                    "direction": "BUY" if sig_type == 1 else "SELL",
                    "entry_time": t,
                    "entry_hour": t_hour,
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
                    "adx_1h": adx,
                    "is_deadzone": (t_hour in [9, 13])
                }

    return pd.DataFrame(completed_trades)


def main():
    print("Simulating all trades from Jan to Sep with ZERO hours blocked...")
    df_xau = simulate_all_hours("XAUUSD")
    df_nas = simulate_all_hours("NAS100")
    df_all = pd.concat([df_xau, df_nas]).sort_values('entry_time').reset_index(drop=True)

    print(f"Total trades across all hours (6 to 20 UTC): {len(df_all)}")
    
    # 1. Classify Market Regime
    # Trendy: ADX >= 25.0 and Stretch >= 0.50
    # Choppy: ADX < 20.0 or Stretch < 0.40
    # Moderate: in between
    def classify_regime(r):
        if r['adx_1h'] >= 25.0 and r['stretch_ratio'] >= 0.50:
            return 'TRENDY'
        elif r['adx_1h'] < 20.0 or r['stretch_ratio'] < 0.40:
            return 'CHOPPY/RANGING'
        else:
            return 'MODERATE'

    df_all['regime'] = df_all.apply(classify_regime, axis=1)

    dz_trades = df_all[df_all['is_deadzone']]
    non_dz_trades = df_all[~df_all['is_deadzone']]

    print("\n" + "=" * 90)
    print("1. OVERALL DEADZONE [09:00 & 13:00 UTC] vs NORMAL HOURS")
    print("=" * 90)
    
    for name, grp in [("Normal Active Hours (6-8, 10-12, 14-20)", non_dz_trades), ("Deadzone Hours (09:00 & 13:00 UTC)", dz_trades)]:
        wr = (grp['net_pnl'] > 0).mean() * 100
        pnl = grp['net_pnl'].sum()
        print(f"{name:<45} | Trades: {len(grp):3d} | Win Rate: {wr:5.1f}% | Net PnL: ${pnl:+8.2f} | Avg: ${pnl/len(grp):+5.2f}")

    print("\n" + "=" * 90)
    print("2. DEADZONE PERFORMANCE BY MARKET REGIME (TRENDY vs CHOPPY)")
    print("=" * 90)
    for regime in ['TRENDY', 'MODERATE', 'CHOPPY/RANGING']:
        sub_all = df_all[df_all['regime'] == regime]
        sub_dz = dz_trades[dz_trades['regime'] == regime]
        sub_non = non_dz_trades[non_dz_trades['regime'] == regime]

        wr_dz = (sub_dz['net_pnl'] > 0).mean() * 100 if len(sub_dz) > 0 else 0
        pnl_dz = sub_dz['net_pnl'].sum() if len(sub_dz) > 0 else 0

        wr_non = (sub_non['net_pnl'] > 0).mean() * 100 if len(sub_non) > 0 else 0
        pnl_non = sub_non['net_pnl'].sum() if len(sub_non) > 0 else 0

        print(f"\n--- Regime: {regime} (Total Trades: {len(sub_all)}) ---")
        print(f"  Deadzone (09:00 & 13:00 UTC): Trades={len(sub_dz):2d} | Win Rate={wr_dz:5.1f}% | Net PnL=${pnl_dz:+7.2f}")
        print(f"  Normal Active Hours         : Trades={len(sub_non):2d} | Win Rate={wr_non:5.1f}% | Net PnL=${pnl_non:+7.2f}")

    print("\n" + "=" * 90)
    print("3. HOUR-BY-HOUR PERFORMANCE BREAKDOWN (ALL 24 HOURS)")
    print("=" * 90)
    h_group = df_all.groupby('entry_hour')
    for h, grp in h_group:
        wr = (grp['net_pnl'] > 0).mean() * 100
        pnl = grp['net_pnl'].sum()
        label = " [DEADZONE]" if h in [9, 13] else ""
        print(f"  Hour {h:02d}:00 UTC{label:<12} | Trades: {len(grp):2d} | Win Rate: {wr:5.1f}% | Net PnL: ${pnl:+8.2f} | Avg: ${pnl/len(grp):+5.2f}")

    # 4. Compare Full Portfolio Strategies:
    # A. Always Block Deadzone (Official baseline approach)
    # B. Never Block Deadzone (Allow all hours)
    # C. Dynamic Deadzone (Allow Deadzone ONLY if Market is TRENDY)
    print("\n" + "=" * 90)
    print("4. PORTFOLIO STRATEGY COMPARISON: ALWAYS BLOCK vs NEVER BLOCK vs DYNAMIC")
    print("=" * 90)

    strat_A = df_all[~df_all['is_deadzone']]  # Always Block
    strat_B = df_all                         # Never Block
    strat_C = df_all[(~df_all['is_deadzone']) | (df_all['regime'] == 'TRENDY')]  # Dynamic: Only in Trends

    for label, sub in [
        ("A. Always Block Deadzone (Official 9, 13 Block)", strat_A),
        ("B. Never Block Deadzone (Trade All Hours)", strat_B),
        ("C. Dynamic Deadzone (Allow 9 & 13 ONLY in Trends)", strat_C)
    ]:
        bal = 5000.0
        eq = [bal]
        for _, t in sub.sort_values('entry_time').iterrows():
            scale = bal / 5000.0
            bal += t['net_pnl'] * scale
            eq.append(bal)
        eq_s = pd.Series(eq)
        max_dd = ((eq_s.cummax() - eq_s) / eq_s.cummax() * 100).max()
        wr = (sub['net_pnl'] > 0).mean() * 100
        flat_pnl = sub['net_pnl'].sum()
        print(f"{label:<52} | Trades: {len(sub):3d} | WR: {wr:5.1f}% | Flat: ${flat_pnl:+8.2f} | Comp: ${bal-5000:+9.2f} | Max DD: {max_dd:5.2f}%")


if __name__ == "__main__":
    main()
