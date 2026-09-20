"""
Tuning Experiment: v1.2 Candidate vs Baseline on Official Prop Firm Engine
Simulates:
1. Baseline Official Strategy (Exact 471-trade reference)
2. v1.2 Candidate: Official Rules + 1H EMA Stretch Guard (<= 1.00x ATR)
Runs both through:
- Evaluation Challenge Phase (14% = $700 target)
- 17 Bi-Weekly Payout Cycles with $5,000 Capital Resets
- Daily Circuit Breakers & Drawdown Floors
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dcc_engine import DCCEngine
from experimental.test_5m_liquidity_sweep import detect_liquidity_sweep

CACHE_DIR = os.path.join(PROJECT_ROOT, "data_cache")
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

    # Calculate 1H EMA stretch ratio
    df_prep['stretch_ratio'] = (df_prep['close'] - df_prep['ema20_1h']).abs() / (df_prep['atr_1h'] + 1e-9)

    eval_bars = df_prep[(df_prep.index >= '2026-01-01') & (df_prep.index <= '2026-09-08')].copy()
    return eval_bars, df_m5, cfg


def simulate_official(sym: str, max_stretch: float = None) -> pd.DataFrame:
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
                pos['duration_m'] = (t - pos['entry_time']).total_seconds() / 60.0
                pts1 = pos['tp1_dist']
                pts2 = pos['tp2_dist']
                p1_dollars = pos['part_lots'] * pts1 * val_per_pt
                p2_dollars = pos['run_lots'] * pts2 * val_per_pt
                comm = pos['total_lots'] * cfg["comm_per_lot"]
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
                comm = pos['total_lots'] * cfg["comm_per_lot"]
                if pos['tp1_hit']:
                    pos['exit_reason'] = "TP1_THEN_BE"
                    p1_dollars = pos['part_lots'] * pos['tp1_dist'] * val_per_pt
                    p2_dollars = 0.0
                    net = p1_dollars - comm
                else:
                    pos['exit_reason'] = "SL"
                    net = -50.0 - comm
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
                pos['net_pnl'] = round(net, 2)
                current_balance += net
                pos['ending_balance'] = round(current_balance, 2)
                completed_trades.append(pos)
                active_trade = None
                continue

        # Entry Check
        if active_trade is None:
            if daily_losses >= 2:
                continue
            if not (6 <= t_hour < 21) or t_hour in [9, 13]:
                continue

            bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
            if bias == 0 or c_bar['adx_1h'] < cfg["adx_min"]:
                continue
            atr = float(c_bar['atr_1h'])
            if atr <= 0:
                continue

            # Stretch Guard
            if max_stretch is not None:
                if c_bar['stretch_ratio'] > max_stretch:
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
                    "exit_time": None,
                    "entry_price": entry_p,
                    "sl_price": sl_p,
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
                    "be_hit": False,
                    "starting_balance": round(current_balance, 2)
                }

    df_trades = pd.DataFrame(completed_trades)
    df_trades['entry_dt'] = pd.to_datetime(df_trades['entry_time'])
    df_trades['exit_dt'] = pd.to_datetime(df_trades['exit_time'])
    df_trades['month'] = df_trades['entry_dt'].dt.strftime('%Y-%m')
    df_trades['date'] = df_trades['exit_dt'].dt.strftime('%Y-%m-%d')
    return df_trades


def run_prop_firm_audit(df_trades: pd.DataFrame, label: str):
    """Runs the 14% Challenge + 17 Bi-Weekly Payout Cycles with $5k Resets."""
    df = df_trades.sort_values('entry_dt').reset_index(drop=True)
    initial_cap = 5000.0
    challenge_target = 700.0
    challenge_bal = initial_cap
    passed_idx = None
    passed_date = None
    ch_trades = []

    for idx, row in df.iterrows():
        challenge_bal += row['net_pnl']
        ch_trades.append({'pnl': row['net_pnl'], 'balance': challenge_bal})
        if (challenge_bal - initial_cap) >= challenge_target:
            passed_idx = idx
            passed_date = row['exit_dt']
            break

    ch_df = pd.DataFrame(ch_trades)
    ch_peak = ch_df['balance'].cummax()
    ch_dd = ((ch_peak - ch_df['balance']) / ch_peak * 100).max()
    eval_days = (passed_date - df['entry_dt'].iloc[0]).days

    # Funded Phase
    funded_pool = df.iloc[passed_idx + 1:].copy().reset_index(drop=True)
    events = []
    for idx, tr in funded_pool.iterrows():
        if tr['entry_dt'].hour >= 19:
            continue
        events.append({'time': tr['entry_dt'], 'type': 'ENTRY_REQ', 'data': tr})
        events.append({'time': tr['exit_dt'], 'type': 'EXIT', 'data': tr})
    events = sorted(events, key=lambda x: (x['time'], 0 if x['type'] == 'EXIT' else 1))

    current_balance = 5000.0
    cycle_length_days = 14
    current_cycle_start = passed_date
    current_cycle_end = current_cycle_start + timedelta(days=cycle_length_days)
    cycle_num = 1
    cum_banked_cash = 0.0
    lowest_balance = 5000.0
    payout_ledger = []
    day_closed_pnl = 0.0
    current_day = None
    daily_summary = []

    for ev in events:
        ev_time = ev['time']
        ev_date = ev_time.date()

        while ev_time >= current_cycle_end:
            profit = current_balance - 5000.0
            payout = max(0.0, profit * 0.80)
            cum_banked_cash += payout
            new_bal = 5000.0 if profit > 0 else current_balance
            payout_ledger.append({
                'cycle': cycle_num,
                'gross_profit': profit,
                'trader_payout': payout,
                'cum_banked': cum_banked_cash
            })
            current_balance = new_bal
            cycle_num += 1
            current_cycle_start = current_cycle_end
            current_cycle_end = current_cycle_start + timedelta(days=cycle_length_days)

        if ev_date != current_day:
            if current_day is not None:
                daily_summary.append({'date': current_day, 'day_pnl': day_closed_pnl})
            current_day = ev_date
            day_closed_pnl = 0.0

        if ev['type'] == 'EXIT':
            pnl = ev['data']['net_pnl']
            current_balance += pnl
            day_closed_pnl += pnl
            if current_balance < lowest_balance:
                lowest_balance = current_balance

    df_led = pd.DataFrame(payout_ledger)
    df_d = pd.DataFrame(daily_summary)
    worst_day = df_d['day_pnl'].min() if not df_d.empty else 0.0
    worst_day_pct = (abs(worst_day) / 5000.0) * 100 if worst_day < 0 else 0.0
    max_base_dd = ((5000.0 - lowest_balance) / 5000.0) * 100 if lowest_balance < 5000 else 0.0
    prof_cycles = len(df_led[df_led['trader_payout'] > 0])
    tot_cycles = len(df_led)

    print("\n" + "=" * 90)
    print(f"REPORT: {label}")
    print("=" * 90)
    print(f"Total Trades Taken:           {len(df)} trades")
    print(f"Total Full-Year Net Profit:   ${df['net_pnl'].sum():+,.2f} (ROI: {df['net_pnl'].sum()/50:+.2f}%)")
    print(f"Win Rate:                     {len(df[df['net_pnl']>0])/len(df)*100:.1f}%")
    print(f"Challenge Evaluation:         Passed in {eval_days} days ({len(ch_df)} trades) | Max DD: {ch_dd:.2f}%")
    print(f"Funded Bi-Weekly Cycles:      {prof_cycles}/{tot_cycles} profitable ({(prof_cycles/tot_cycles)*100:.1f}%)")
    print(f"Trader Banked Cash (80%):     ${cum_banked_cash:+,.2f} in your pocket!")
    print(f"Lowest Balance Ever:          ${lowest_balance:,.2f} (Max DD Floor: $4,500.00)")
    print(f"Max Base Drawdown:            {max_base_dd:.2f}%")
    print(f"Worst Single Day Loss:        ${worst_day:+,.2f} ({worst_day_pct:.2f}% of $5k)")
    print(f"Breach Detected:              {'NONE (100% CLEAN PASS)' if max_base_dd < 10.0 and worst_day_pct < 5.0 else 'BREACH'}")


if __name__ == "__main__":
    print("Executing tuning backtests on Gold and Nasdaq...")
    
    # 1. Baseline
    xau_b = simulate_official("XAUUSD", max_stretch=None)
    nas_b = simulate_official("NAS100", max_stretch=None)
    df_base = pd.concat([xau_b, nas_b]).sort_values('entry_dt').reset_index(drop=True)
    run_prop_firm_audit(df_base, "BASELINE LOCKED (Exact Official 471-Trade Reference)")

    # 2. v1.2 Candidate (With Stretch Guard 1.00x ATR)
    xau_v12 = simulate_official("XAUUSD", max_stretch=1.00)
    nas_v12 = simulate_official("NAS100", max_stretch=1.00)
    df_v12 = pd.concat([xau_v12, nas_v12]).sort_values('entry_dt').reset_index(drop=True)
    run_prop_firm_audit(df_v12, "v1.2 CANDIDATE (Baseline + 1H EMA Stretch Guard <= 1.00x ATR)")

    # 3. v1.2 Candidate Tight (With Stretch Guard 0.85x ATR)
    xau_t = simulate_official("XAUUSD", max_stretch=0.85)
    nas_t = simulate_official("NAS100", max_stretch=0.85)
    df_t = pd.concat([xau_t, nas_t]).sort_values('entry_dt').reset_index(drop=True)
    run_prop_firm_audit(df_t, "v1.2 CANDIDATE TIGHT (Baseline + 1H EMA Stretch Guard <= 0.85x ATR)")
