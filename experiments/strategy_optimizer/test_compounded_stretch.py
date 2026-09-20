"""
Test Compounded DCC Strategy with:
1. Baseline Official Settings (0.9x ATR Gold, 1.0x ATR NAS, 1.4R/2.1R, Compounding 1%)
2. Baseline + 1H EMA Stretch Guard (<= 1.00x ATR)
3. Baseline + 1H EMA Stretch Guard (<= 0.85x ATR)
Measures: Total PnL, ROI %, Max DD %, Daily DD %, Trades on Sep 8 & Sep 15.
"""

import os
import sys
import pandas as pd
import numpy as np

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.strategy_optimizer.backtester import BacktestDataset
from experiments.strategy_optimizer.optimizer_engine import detect_liquidity_sweep_fast

SPECS = {
    "XAUUSD": {"tick_size": 0.01, "tick_val": 1.0, "contract_size": 100.0, "atr_sl_mult": 0.90, "tp1_rr": 1.4, "tp2_rr": 2.2, "comm_per_lot": 5.0},
    "NAS100": {"tick_size": 0.1, "tick_val": 0.1, "contract_size": 10.0, "atr_sl_mult": 1.00, "tp1_rr": 1.5, "tp2_rr": 2.0, "comm_per_lot": 5.0}
}


def simulate_compounded(sym: str, max_stretch: float = None):
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
                net = p1 + p2 - comm
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
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
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue

            elif tp1_hit and not pos['tp1_hit']:
                pos['tp1_hit'] = True
                pos['current_sl'] = pos['entry_price']

            elif i == n_bars - 1:
                pos['exit_time'] = t
                pos['exit_price'] = close_p
                pos['exit_reason'] = "END_OF_DATA"
                comm = pos['total_lots'] * comm_per_lot
                run_pts = (close_p - pos['entry_price']) if is_buy else (pos['entry_price'] - close_p)
                p2 = pos['run_lots'] * run_pts * val_per_pt
                p1 = (pos['part_lots'] * pos['tp1_dist'] * val_per_pt) if pos['tp1_hit'] else (pos['part_lots'] * run_pts * val_per_pt)
                net = p1 + p2 - comm
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue

        # Entry Signal
        if active_trade is None:
            if daily_losses >= 2:
                continue
            if not (6 <= t_hour < 21):
                continue

            bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
            if bias == 0 or c_bar['adx_1h'] < 15.0:
                continue

            atr = float(c_bar['atr_1h'])
            if atr <= 0:
                continue

            # Stretch Guard Check
            if max_stretch is not None:
                stretch = float(c_bar['stretch_ratio'])
                if stretch > max_stretch:
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
                tp1_dist = cfg["tp1_rr"] * sl_dist
                tp2_dist = cfg["tp2_rr"] * sl_dist
                entry_p = close_p
                sl_p = entry_p - sl_dist if sig_type == 1 else entry_p + sl_dist
                tp1_p = entry_p + tp1_dist if sig_type == 1 else entry_p - tp1_dist
                tp2_p = entry_p + tp2_dist if sig_type == 1 else entry_p - tp2_dist

                # 1.0% dynamic compounding
                risk_dollars = current_balance * 0.01
                raw_lots = risk_dollars / (sl_dist * val_per_pt)
                tot_lots = max(0.01, round(raw_lots, 2))
                part_lots = round(tot_lots * 0.5, 2)
                run_lots = round(tot_lots - part_lots, 2)

                active_trade = {
                    "symbol": sym,
                    "direction": "BUY" if sig_type == 1 else "SELL",
                    "entry_time": t,
                    "entry_price": entry_p,
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
                    "starting_balance": round(current_balance, 2)
                }

    if not completed_trades:
        return pd.DataFrame()
    df_t = pd.DataFrame(completed_trades)
    df_t['entry_dt'] = pd.to_datetime(df_t['entry_time'])
    df_t['exit_dt'] = pd.to_datetime(df_t['exit_time'])
    df_t['date'] = df_t['exit_dt'].dt.strftime('%Y-%m-%d')
    df_t['month'] = df_t['entry_dt'].dt.strftime('%Y-%m')
    return df_t


def run_compounded_comparison():
    variations = [
        ("Official Baseline (No Stretch Cap)", None),
        ("Baseline + Stretch Cap (1.25x ATR)", 1.25),
        ("Baseline + Stretch Cap (1.00x ATR)", 1.00),
        ("Baseline + Stretch Cap (0.85x ATR)", 0.85),
    ]

    for label, stretch in variations:
        df_x = simulate_compounded("XAUUSD", stretch)
        df_n = simulate_compounded("NAS100", stretch)
        df = pd.concat([df_x, df_n]).sort_values('entry_dt').reset_index(drop=True)

        tot = len(df)
        wins = df[df['net_pnl'] > 0]
        losses = df[df['net_pnl'] < 0]
        wr = len(wins) / tot * 100 if tot > 0 else 0
        pnl = df['net_pnl'].sum()
        roi = (pnl / 5000.0) * 100

        # Drawdown calculation
        df['cum_pnl'] = df['net_pnl'].cumsum()
        df['eq'] = 5000.0 + df['cum_pnl']
        df['pk'] = df['eq'].cummax()
        df['dd_pct'] = (df['pk'] - df['eq']) / df['pk'] * 100
        max_dd = df['dd_pct'].max()

        # Worst day
        d_pnl = df.groupby('date')['net_pnl'].sum()
        worst_day = d_pnl.min()
        worst_day_pct = (abs(worst_day) / 5000.0) * 100

        # Sep 8 & Sep 15 performance
        sep8 = df[df['entry_dt'].dt.strftime('%Y-%m-%d') == '2026-09-08']
        sep15 = df[df['entry_dt'].dt.strftime('%Y-%m-%d') == '2026-09-15']

        print("\n" + "=" * 80)
        print(f"CONFIGURATION: {label}")
        print("=" * 80)
        print(f"Total Trades Taken: {tot}")
        print(f"Ending Balance:     ${5000.0 + pnl:,.2f}")
        print(f"Total Net Profit:   ${pnl:+,.2f} (ROI: {roi:+.2f}%)")
        print(f"Win Rate:           {wr:.1f}% ({len(wins)}W / {len(losses)}L)")
        print(f"Max Peak-to-Trough: {max_dd:.2f}%")
        print(f"Worst Daily Loss:   ${worst_day:+,.2f} ({worst_day_pct:.2f}% of $5k)")
        print(f"Sep 08 Trades:      {len(sep8)} trades, Net PnL: ${sep8['net_pnl'].sum():+,.2f}")
        print(f"Sep 15 Trades:      {len(sep15)} trades, Net PnL: ${sep15['net_pnl'].sum():+,.2f}")
        
        print("\nMonthly PnL Summary:")
        m_summary = df.groupby('month')['net_pnl'].sum()
        for m_str, m_val in m_summary.items():
            print(f"  {m_str}: ${m_val:>9,.2f} [{'GREEN' if m_val > 0 else 'RED'}]")


if __name__ == "__main__":
    run_compounded_comparison()
