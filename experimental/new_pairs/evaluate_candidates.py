"""
Candidate Evaluation Script.
Runs the exact DCC + 5M Liquidity Sweep strategy on candidate symbols across 2026.
Isolates performance during Gold & Nasdaq chop months (February, July, and August 2026).
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dcc_engine import DCCEngine
from experimental.test_5m_liquidity_sweep import detect_liquidity_sweep
from experimental.new_pairs.pair_configs import CANDIDATE_CONFIGS, CandidateConfig

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


def run_candidate_backtest(symbol: str, cfg: CandidateConfig) -> pd.DataFrame:
    m5_file = os.path.join(CACHE_DIR, f"m5_{symbol}.parquet")
    h1_file = os.path.join(CACHE_DIR, f"h1_{symbol}.parquet")
    h2_file = os.path.join(CACHE_DIR, f"h2_{symbol}.parquet")

    if not (os.path.exists(m5_file) and os.path.exists(h1_file) and os.path.exists(h2_file)):
        raise FileNotFoundError(f"Missing parquet cache for {symbol}")

    broker_offset = timedelta(hours=3)

    df_m5 = pd.read_parquet(m5_file)
    df_m5['time'] = (pd.to_datetime(df_m5['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_m5.set_index('time', inplace=True)
    df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)

    df_1h = pd.read_parquet(h1_file)
    df_1h['time'] = (pd.to_datetime(df_1h['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_1h.set_index('time', inplace=True)

    df_2h = pd.read_parquet(h2_file)
    df_2h['time'] = (pd.to_datetime(df_2h['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
    df_2h.set_index('time', inplace=True)

    engine = DCCEngine(
        adx_min_threshold=cfg.adx_min,
        atr_sl_multiplier=cfg.atr_sl_multiplier,
        risk_reward_ratio=cfg.tp1_rr,
        check_2h_room=False,
        use_daily_open_filter=False
    )

    df_prep = engine.prepare_data(df_m5, df_1h, df_2h)

    start_eval = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end_eval = datetime(2026, 9, 8, 23, 59, tzinfo=timezone.utc)

    eval_bars = df_prep[(df_prep.index >= start_eval) & (df_prep.index <= end_eval)]

    active_trade = None
    completed_trades = []
    current_balance = 5000.0

    point = cfg.point
    tick_size = cfg.tick_size
    tick_val = cfg.tick_value

    for i in range(20, len(eval_bars)):
        c_bar = eval_bars.iloc[i]
        p_bar = eval_bars.iloc[i - 1]
        t = eval_bars.index[i]
        t_hour = t.hour

        high_p = float(c_bar['high'])
        low_p = float(c_bar['low'])
        close_p = float(c_bar['close'])

        # 1. Manage Active Trade
        if active_trade is not None:
            pos = active_trade
            dir_str = pos['direction']
            is_buy = (dir_str == "BUY")

            sl_hit = (low_p <= pos['current_sl']) if is_buy else (high_p >= pos['current_sl'])
            tp1_hit = (high_p >= pos['tp1_price']) if is_buy else (low_p <= pos['tp1_price'])
            tp2_hit = (high_p >= pos['tp2_price']) if is_buy else (low_p <= pos['tp2_price'])
            eod_hit = (t_hour >= 21)

            # Exit logic
            if tp2_hit:
                # Full TP2
                pos['exit_time'] = t
                pos['exit_price'] = pos['tp2_price']
                pos['exit_reason'] = "FULL_TP2"
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                pts1 = pos['tp1_dist']
                pts2 = pos['tp2_dist']
                val_per_pt = (tick_val / tick_size)
                p1_dollars = pos['part_lots'] * pts1 * val_per_pt
                p2_dollars = pos['run_lots'] * pts2 * val_per_pt
                comm = pos['total_lots'] * 5.0  # $5 per lot commission
                net = p1_dollars + p2_dollars - comm
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
                val_per_pt = (tick_val / tick_size)
                comm = pos['total_lots'] * 5.0
                if pos['tp1_hit']:
                    # Breakeven stop hit on runner
                    pos['exit_reason'] = "TP1_THEN_BE"
                    p1_dollars = pos['part_lots'] * pos['tp1_dist'] * val_per_pt
                    p2_dollars = 0.0
                    net = p1_dollars - comm
                else:
                    pos['exit_reason'] = "SL"
                    net = -50.0 - comm  # $50 initial risk
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue

            elif tp1_hit and not pos['tp1_hit']:
                pos['tp1_hit'] = True
                pos['current_sl'] = pos['entry_price']  # Move to BE

            elif eod_hit:
                pos['exit_time'] = t
                pos['exit_price'] = close_p
                pos['exit_reason'] = "EOD_EXIT"
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                val_per_pt = (tick_val / tick_size)
                comm = pos['total_lots'] * 5.0
                if is_buy:
                    run_pts = close_p - pos['entry_price']
                else:
                    run_pts = pos['entry_price'] - close_p
                p2_dollars = pos['run_lots'] * run_pts * val_per_pt
                p1_dollars = (pos['part_lots'] * pos['tp1_dist'] * val_per_pt) if pos['tp1_hit'] else (pos['part_lots'] * run_pts * val_per_pt)
                net = p1_dollars + p2_dollars - comm
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue

        # 2. Check for New Signal Entry
        if active_trade is None:
            # Session filter (06-21 UTC, excluding trap hours 09, 13)
            if not (6 <= t_hour < 21) or t_hour in [9, 13]:
                continue

            bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
            if bias == 0:
                continue

            adx = float(c_bar['adx_1h']) if not pd.isna(c_bar['adx_1h']) else 0.0
            if adx < cfg.adx_min:
                continue

            atr = float(c_bar['atr_1h']) if not pd.isna(c_bar['atr_1h']) else 0.0
            if atr <= 0:
                continue

            vwap = float(c_bar['vwap_5m'])
            h1_e20 = float(c_bar['ema20_1h']) if not pd.isna(c_bar['ema20_1h']) else 0.0

            curr_diff = float(c_bar['ema9_5m']) - float(c_bar['ema20_5m'])
            prev_diff = float(p_bar['ema9_5m']) - float(p_bar['ema20_5m'])

            # Liquidity Sweep check
            sweep_dir = bias
            # find index in df_m5
            idx_m5 = df_m5.index.get_loc(t) if t in df_m5.index else i
            has_sweep, _, _ = detect_liquidity_sweep(
                df_m5, idx_m5, sweep_dir, swing_lookback=20, pullback_window=8
            )
            if not has_sweep:
                continue

            sig_type = 0
            if bias == 1:
                is_flip = (prev_diff <= 0 and curr_diff > 0)
                if is_flip and close_p > vwap and close_p > h1_e20:
                    sig_type = 1
            elif bias == -1:
                is_flip = (prev_diff >= 0 and curr_diff < 0)
                if is_flip and close_p < vwap and close_p < h1_e20:
                    sig_type = -1

            if sig_type != 0:
                sl_dist = cfg.atr_sl_multiplier * atr
                tp1_dist = cfg.tp1_rr * sl_dist
                tp2_dist = cfg.tp2_rr * sl_dist

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

                # Lot sizing for $50 risk (1.0% of $5,000)
                val_per_pt = (tick_val / tick_size)
                raw_lots = 50.0 / (sl_dist * val_per_pt)
                tot_lots = max(0.01, round(raw_lots, 2))
                part_lots = round(tot_lots * 0.5, 2)
                run_lots = round(tot_lots - part_lots, 2)

                active_trade = {
                    "trade_id": len(completed_trades) + 1,
                    "symbol": symbol,
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
                    "starting_balance": round(current_balance, 2)
                }

    df_res = pd.DataFrame(completed_trades)
    if len(df_res) > 0:
        df_res['entry_dt'] = pd.to_datetime(df_res['entry_time'], format='mixed', utc=True)
        df_res['month'] = df_res['entry_dt'].dt.strftime('%Y-%m')
        save_path = os.path.join(REPORTS_DIR, f"trades_{symbol}.csv")
        df_res.to_csv(save_path, index=False)

    return df_res


def evaluate_all_candidates():
    print("=" * 105)
    print("            DCC CHOP-HEDGE MULTI-ASSET PERFORMANCE EVALUATION (JAN - SEP 2026)")
    print("                Testing 6 Candidate Assets Against February, July, and August Chop")
    print("=" * 105)

    all_results = {}
    for sym, cfg in CANDIDATE_CONFIGS.items():
        print(f"\n>>> Running Backtest for {sym} <<<")
        df_trades = run_candidate_backtest(sym, cfg)
        all_results[sym] = df_trades

        if len(df_trades) == 0:
            print(f"  [WARN] No trades executed for {sym}")
            continue

        wins = df_trades[df_trades['net_pnl'] > 0]
        wr = len(wins) / len(df_trades) * 100.0
        tot_pnl = df_trades['net_pnl'].sum()
        gross_w = wins['net_pnl'].sum()
        gross_l = abs(df_trades[df_trades['net_pnl'] <= 0]['net_pnl'].sum())
        pf = gross_w / gross_l if gross_l > 0 else 99.0

        print(f"  Trades: {len(df_trades):<4} | Win Rate: {wr:4.1f}% | Total Net Profit: ${tot_pnl:+8.2f} | PF: {pf:4.2f}")

    # Month-by-Month Matrix
    print("\n" + "=" * 115)
    print("                             MONTH-BY-MONTH NET PROFIT MATRIX ($)")
    print("=" * 115)

    months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
    header = f"{'Symbol':<10} | " + " | ".join([f"{m[-2:]} PnL" for m in months]) + " | Total Net PnL | Chop Months (Feb+Jul+Aug)"
    print(header)
    print("-" * 115)

    for sym, df_trades in all_results.items():
        if len(df_trades) == 0:
            continue
        m_pnls = []
        for m in months:
            sub = df_trades[df_trades['month'] == m]
            p = sub['net_pnl'].sum() if len(sub) > 0 else 0.0
            m_pnls.append(p)

        tot = sum(m_pnls)
        # February (idx 1), July (idx 6), August (idx 7)
        chop_sum = m_pnls[1] + m_pnls[6] + m_pnls[7]

        row_str = f"{sym:<10} | " + " | ".join([f"{p:+7.0f}" for p in m_pnls]) + f" | ${tot:+10,.2f} | ${chop_sum:+12,.2f}"
        print(row_str)

    print("=" * 115)


if __name__ == "__main__":
    evaluate_all_candidates()
