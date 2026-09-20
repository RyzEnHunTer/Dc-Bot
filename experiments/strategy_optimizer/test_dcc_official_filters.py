"""
Test Official DCC PDF Rules against the Baseline:
1. Rule from PDF Page 10/11: Chop Filter (Disqualify if >=2 flips in last 12 bars)
2. Rule from PDF Page 7/8: ADX > 40 Exhaustion Guard
3. Rule from PDF Page 2: Dynamic 2H TP Sizing (Bank 1.5R/1.2R before 2H levels)
"""

import os
import sys
import pandas as pd
import numpy as np

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.strategy_optimizer.backtester import BacktestDataset
from experimental.test_5m_liquidity_sweep import detect_liquidity_sweep
from experiments.strategy_optimizer.optimizer_engine import count_recent_flips, compute_dynamic_tp

SPECS = {
    "XAUUSD": {"tick_size": 0.01, "tick_val": 1.0, "contract_size": 100.0, "atr_sl_mult": 1.3, "tp1_rr": 1.4, "tp2_rr": 2.1, "comm_per_lot": 5.0},
    "NAS100": {"tick_size": 0.1, "tick_val": 0.1, "contract_size": 10.0, "atr_sl_mult": 1.4, "tp1_rr": 1.4, "tp2_rr": 2.1, "comm_per_lot": 5.0}
}


def simulate_dcc_rules(sym: str, use_chop_filter: bool = False, use_adx_exhaustion: bool = False, use_dyn_tp: bool = False):
    eval_bars, df_m5 = BacktestDataset.get_data(sym)
    cfg = SPECS[sym]
    tick_size = cfg["tick_size"]
    tick_val = cfg["tick_val"]
    val_per_pt = tick_val / tick_size
    sl_multiplier = cfg["atr_sl_mult"]
    comm_per_lot = cfg["comm_per_lot"]

    active_trade = None
    completed_trades = []
    current_balance = 5000.0
    daily_losses = 0
    current_day = ""

    for i in range(25, len(eval_bars)):
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
                net = p1 + p2 - comm
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                completed_trades.append(pos)
                active_trade = None
                continue

            elif sl_hit:
                pos['exit_time'] = t
                pos['exit_price'] = pos['current_sl']
                comm = pos['total_lots'] * comm_per_lot
                if pos['tp1_hit']:
                    pos['exit_reason'] = "TP1_THEN_BE"
                    net = (pos['part_lots'] * pos['tp1_dist'] * val_per_pt) - comm
                else:
                    pos['exit_reason'] = "SL"
                    net = -50.0 - comm
                    daily_losses += 1
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                completed_trades.append(pos)
                active_trade = None
                continue

            elif tp1_hit and not pos['tp1_hit']:
                pos['tp1_hit'] = True
                pos['current_sl'] = pos['entry_price']

            elif i == len(eval_bars) - 1:
                pos['exit_time'] = t
                pos['exit_price'] = close_p
                pos['exit_reason'] = "END_OF_DATA"
                comm = pos['total_lots'] * comm_per_lot
                run_pts = (close_p - pos['entry_price']) if is_buy else (pos['entry_price'] - close_p)
                p2 = pos['run_lots'] * run_pts * val_per_pt
                p1 = (pos['part_lots'] * pos['tp1_dist'] * val_per_pt) if pos['tp1_hit'] else (pos['part_lots'] * run_pts * val_per_pt)
                pos['net_pnl'] = round(p1 + p2 - comm, 2)
                current_balance += net
                completed_trades.append(pos)
                active_trade = None
                continue

        if active_trade is None:
            if daily_losses >= 2:
                continue
            if not (6 <= t_hour < 21) or t_hour in [9, 13]:
                continue

            bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
            if bias == 0 or c_bar['adx_1h'] < 15.0:
                continue
            atr = float(c_bar['atr_1h'])
            if atr <= 0:
                continue

            # DCC PDF Rule: ADX > 40 Exhaustion Filter
            if use_adx_exhaustion and c_bar['adx_1h'] > 40.0:
                # If ADX > 40 and price is far from 1H EMA20 (>1.25x ATR), disqualify
                if c_bar['stretch_ratio'] > 1.25:
                    continue

            # DCC PDF Rule: 5M Chop Filter (Page 10/11)
            if use_chop_filter:
                flips = count_recent_flips(eval_bars, i, lookback_bars=12)
                if flips >= 2:
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
                entry_p = close_p
                sw_h_2h = float(c_bar.get('swing_high_2h', 0.0))
                sw_l_2h = float(c_bar.get('swing_low_2h', 0.0))

                if use_dyn_tp:
                    tp1_p, tp2_p, eff_tp1, eff_tp2 = compute_dynamic_tp(entry_p, sl_dist, sig_type, sw_h_2h, sw_l_2h, cfg["tp1_rr"], cfg["tp2_rr"])
                    tp1_dist = abs(tp1_p - entry_p)
                    tp2_dist = abs(tp2_p - entry_p)
                else:
                    tp1_dist = cfg["tp1_rr"] * sl_dist
                    tp2_dist = cfg["tp2_rr"] * sl_dist
                    tp1_p = entry_p + tp1_dist if sig_type == 1 else entry_p - tp1_dist
                    tp2_p = entry_p + tp2_dist if sig_type == 1 else entry_p - tp2_dist

                sl_p = entry_p - sl_dist if sig_type == 1 else entry_p + sl_dist
                raw_lots = 50.0 / (sl_dist * val_per_pt)
                tot_lots = max(0.01, round(raw_lots, 2))
                part_lots = round(tot_lots * 0.5, 2)
                run_lots = round(tot_lots - part_lots, 2)

                active_trade = {
                    "symbol": sym,
                    "direction": "BUY" if sig_type == 1 else "SELL",
                    "entry_time": t,
                    "entry_price": entry_p,
                    "current_sl": sl_p,
                    "tp1_price": tp1_p,
                    "tp2_price": tp2_p,
                    "sl_dist": sl_dist,
                    "tp1_dist": tp1_dist,
                    "tp2_dist": tp2_dist,
                    "total_lots": tot_lots,
                    "part_lots": part_lots,
                    "run_lots": run_lots,
                    "tp1_hit": False,
                    "tp2_hit": False,
                }

    df_t = pd.DataFrame(completed_trades)
    df_t['entry_dt'] = pd.to_datetime(df_t['entry_time'])
    df_t['exit_dt'] = pd.to_datetime(df_t['exit_time'])
    return df_t


def run_tests():
    configs = [
        ("Baseline (No Additional Filters)", False, False, False),
        ("v1.2 Option A: + Chop Filter (>=2 Flips Avoided)", True, False, False),
        ("v1.2 Option B: + Dynamic 2H TP Sizing", False, False, True),
        ("v1.2 Option C: + ADX>40 Exhaustion Filter", False, True, False),
        ("v1.2 Option D: + Chop Filter + Dynamic 2H TP", True, False, True),
    ]

    for label, chop, adx, dyntp in configs:
        xau = simulate_dcc_rules("XAUUSD", chop, adx, dyntp)
        nas = simulate_dcc_rules("NAS100", chop, adx, dyntp)
        df = pd.concat([xau, nas]).sort_values('entry_dt').reset_index(drop=True)

        tot = len(df)
        wins = df[df['net_pnl'] > 0]
        losses = df[df['net_pnl'] < 0]
        wr = len(wins) / tot * 100
        pnl = df['net_pnl'].sum()

        df['cum_pnl'] = df['net_pnl'].cumsum()
        df['eq'] = 5000.0 + df['cum_pnl']
        df['pk'] = df['eq'].cummax()
        df['dd'] = (df['pk'] - df['eq']) / df['pk'] * 100
        max_dd = df['dd'].max()

        df['date'] = df['exit_dt'].dt.strftime('%Y-%m-%d')
        daily_loss = df.groupby('date')['net_pnl'].sum().min()

        print(f"\n[{label}]")
        print(f"  Trades: {tot} | Net Profit: ${pnl:+,.2f} | Win Rate: {wr:.1f}% | Max DD: {max_dd:.2f}% | Worst Day: ${daily_loss:+,.2f}")


if __name__ == "__main__":
    run_tests()
