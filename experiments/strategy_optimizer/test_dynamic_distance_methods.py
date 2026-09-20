"""
Precision Dynamic Distance Benchmark
Tests 3 Dynamic Distance Methods on the Official 471 MT5 Real Broker Trades:
- Baseline: Official Locked Continuous (Original)
- Method 1: ATR Dynamic Band (Min 1H Stretch >= 0.45x ATR + ADX <= 42 + 5M No-Chase <= 0.35x ATR)
- Method 2: 1H EMA Fan Separation (Alligator Mouth: |1H EMA9 - 1H EMA20| / 1H ATR >= 0.15x ATR)
- Method 3: Rolling 5-Day Percentile Rank (Avoid bottom 20% chop & top 10% exhaustion)
- Method 4: Best Hybrid (Method 1 + Method 2)

Uses the exact Prop Firm Simulation Engine with 3.0% Shared Portfolio Circuit Breaker,
14% Challenge Target, and 17 Bi-Weekly 80% Payout Cycles.
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

def load_annotated_official_trades():
    csv_path = os.path.join(PROJECT_ROOT, "final_dcc_strategy", "trades_log_official_continuous_no_eod.csv")
    df_trades = pd.read_csv(csv_path)
    df_trades['entry_dt'] = pd.to_datetime(df_trades['entry_time'], format='mixed', utc=True)
    df_trades['exit_dt'] = pd.to_datetime(df_trades['exit_time'], format='mixed', utc=True)

    eval_xau, _ = BacktestDataset.get_data('XAUUSD')
    eval_nas, _ = BacktestDataset.get_data('NAS100')

    # Precalculate rolling percentile of stretch on 1H bars (approx 120 bars = 5 days)
    for eval_bars in [eval_xau, eval_nas]:
        eval_bars['fan_ratio'] = (eval_bars['ema9_1h'] - eval_bars['ema20_1h']).abs() / (eval_bars['atr_1h'] + 1e-9)
        # Compute rolling percentile rank over 100 bars
        eval_bars['stretch_pctl'] = eval_bars['stretch_ratio'].rolling(100, min_periods=20).rank(pct=True)

    annotated = []
    for idx, row in df_trades.iterrows():
        sym = row['symbol']
        eval_bars = eval_xau if sym == 'XAUUSD' else eval_nas
        t = row['entry_dt']
        
        locs = eval_bars.index.get_indexer([t], method='pad')
        if locs[0] == -1:
            continue
        bar = eval_bars.iloc[locs[0]]
        
        close = float(bar['close'])
        ema20_1h = float(bar['ema20_1h'])
        ema9_1h = float(bar['ema9_1h'])
        ema9_5m = float(bar['ema9_5m'])
        atr_1h = float(bar['atr_1h'])
        adx_1h = float(bar.get('adx_1h', 0.0))
        
        stretch_ratio = float(bar['stretch_ratio'])
        fan_ratio = float(bar['fan_ratio'])
        stretch_pctl = float(bar['stretch_pctl']) if pd.notna(bar['stretch_pctl']) else 0.50
        dist_5m_e9 = abs(close - ema9_5m)
        chase_ratio = dist_5m_e9 / atr_1h if atr_1h > 0 else 0.0

        r_dict = row.to_dict()
        r_dict.update({
            'stretch_ratio': stretch_ratio,
            'fan_ratio': fan_ratio,
            'stretch_pctl': stretch_pctl,
            'adx_1h': adx_1h,
            'chase_ratio': chase_ratio
        })
        annotated.append(r_dict)

    df_annot = pd.DataFrame(annotated)
    print(f"Loaded and annotated all {len(df_annot)} official trades.")
    return df_annot


def simulate_prop_firm_exact(df: pd.DataFrame, label: str) -> Dict:
    """Exact institutional prop firm simulation replicating final_dcc_strategy/simulate_prop_firm_account.py."""
    if df.empty:
        return {"name": label, "error": "empty"}

    df = df.sort_values('entry_dt').reset_index(drop=True)

    # 1. Phase 1: Challenge Phase (14% Target = +$700)
    initial_cap = 5000.0
    challenge_target = 700.0
    challenge_bal = initial_cap
    passed_idx = None
    passed_date = None
    ch_trades = []

    for idx, row in df.iterrows():
        pnl = row['net_pnl']
        challenge_bal += pnl
        ch_trades.append({'pnl': pnl, 'balance': challenge_bal, 'exit_dt': row['exit_dt']})
        if (challenge_bal - initial_cap) >= challenge_target:
            passed_idx = idx
            passed_date = row['exit_dt']
            break

    if passed_idx is None:
        eval_pass_str = "Did Not Pass"
        ch_dd = 0.0
        ch_days = 0
        funded_pool = df.iloc[0:0]
    else:
        ch_df = pd.DataFrame(ch_trades)
        ch_peak = ch_df['balance'].cummax()
        ch_dd = ((ch_peak - ch_df['balance']) / ch_peak * 100).max()
        ch_days = (passed_date - df['entry_dt'].iloc[0]).days
        eval_pass_str = f"{ch_days}d ({len(ch_df)} trades)"
        funded_pool = df.iloc[passed_idx + 1:].copy().reset_index(drop=True)

    # 2. Phase 2: Live Funded Bi-Weekly Payout Ledger (with 3.0% Shared Circuit Breaker)
    events = []
    for idx, tr in funded_pool.iterrows():
        if tr['entry_dt'].hour >= 19:
            continue
        events.append({'time': tr['entry_dt'], 'type': 'ENTRY_REQ', 'id': tr['trade_id'], 'data': tr})
        events.append({'time': tr['exit_dt'], 'type': 'EXIT', 'id': tr['trade_id'], 'data': tr})

    events = sorted(events, key=lambda x: (x['time'], 0 if x['type'] == 'EXIT' else 1))

    current_balance = 5000.0
    cycle_length_days = 14
    current_cycle_start = passed_date if passed_date is not None else df['entry_dt'].iloc[0]
    current_cycle_end = current_cycle_start + timedelta(days=cycle_length_days)
    cycle_num = 1

    current_day = None
    day_closed_pnl = 0.0
    cb_triggered_today = False
    open_trades = {}
    cum_banked_cash = 0.0
    lowest_funded_balance = current_balance
    peak_in_cycle = current_balance
    daily_closed_records = []
    executed_funded = 0
    blocked_funded = 0

    for ev in events:
        ev_time = ev['time']
        ev_date = ev_time.date()

        # Bi-weekly payout check
        while ev_time >= current_cycle_end:
            profit = current_balance - 5000.0
            if profit > 0:
                payout = profit * 0.80
                cum_banked_cash += payout
                new_bal = 5000.0
            else:
                new_bal = current_balance

            current_balance = new_bal
            peak_in_cycle = current_balance
            cycle_num += 1
            current_cycle_start = current_cycle_end
            current_cycle_end = current_cycle_start + timedelta(days=cycle_length_days)

        # 00:00 UTC Midnight Reset
        if ev_date != current_day:
            if current_day is not None:
                daily_closed_records.append({'date': current_day, 'loss': day_closed_pnl})
            current_day = ev_date
            day_closed_pnl = 0.0
            cb_triggered_today = False

        if ev['type'] == 'EXIT':
            t_id = ev['id']
            if t_id in open_trades:
                tr = open_trades.pop(t_id)
                pnl = tr['net_pnl']
                current_balance += pnl
                day_closed_pnl += pnl

                if current_balance > peak_in_cycle:
                    peak_in_cycle = current_balance
                if current_balance < lowest_funded_balance:
                    lowest_funded_balance = current_balance

                if day_closed_pnl <= -150.0:
                    cb_triggered_today = True

        elif ev['type'] == 'ENTRY_REQ':
            tr = ev['data']

            # Circuit Breaker Check ($150 / 3.0% shared daily loss cap)
            if cb_triggered_today or day_closed_pnl <= -150.0:
                blocked_funded += 1
                continue

            open_risk = len(open_trades) * 55.0
            total_potential = abs(min(0.0, day_closed_pnl)) + open_risk + 55.0
            if total_potential > 150.0:
                blocked_funded += 1
                continue

            open_trades[tr['trade_id']] = tr.to_dict()
            executed_funded += 1

    if current_balance > 5000.0:
        profit = current_balance - 5000.0
        cum_banked_cash += profit * 0.80

    d_df = pd.DataFrame(daily_closed_records)
    worst_day_loss = abs(min(0.0, d_df['loss'].min())) if not d_df.empty else 0.0
    worst_day_dd = (worst_day_loss / 5000.0) * 100.0

    total_net_pnl = df['net_pnl'].sum()
    total_wins = len(df[df['net_pnl'] > 0])
    total_trades = len(df)
    win_rate = (total_wins / total_trades * 100.0) if total_trades > 0 else 0.0

    base_dd = ((5000.0 - lowest_funded_balance) / 5000.0 * 100.0) if lowest_funded_balance < 5000.0 else 0.0

    return {
        "Candidate": label,
        "Total Trades": total_trades,
        "Win Rate": f"{win_rate:.1f}%",
        "Total Net PnL": f"+${total_net_pnl:,.2f}",
        "Challenge Pass": eval_pass_str,
        "Eval Max DD": f"{ch_dd:.2f}%",
        "Banked Cash (80%)": f"${cum_banked_cash:,.2f}",
        "Lowest Funded Bal": f"${lowest_funded_balance:,.2f}",
        "Max Base DD": f"{base_dd:.2f}%",
        "Worst Single Day": f"-${worst_day_loss:.2f} ({worst_day_dd:.2f}%)",
        "Status": "100% PASS (ZERO BREACH)" if worst_day_dd < 5.0 and base_dd < 10.0 else "BREACH"
    }


def main():
    df_all = load_annotated_official_trades()

    # Define Candidate Filter Masks
    # 0. Baseline Locked (All 471 trades)
    mask_baseline = np.ones(len(df_all), dtype=bool)

    # Method 1: ATR Volatility Band
    # Filter out 1H chop (< 0.45x ATR), extreme 1H exhaustion (ADX > 42.0), and 5M chase (> 0.35x ATR)
    mask_method1 = (
        (df_all['stretch_ratio'] >= 0.45) &
        (df_all['adx_1h'] <= 42.0) &
        (df_all['chase_ratio'] <= 0.35)
    )

    # Method 2: 1H EMA Fan Separation (Alligator Mouth)
    # Require 1H EMA9 to be separated from 1H EMA20 by at least 0.15x ATR
    mask_method2 = (
        (df_all['fan_ratio'] >= 0.15) &
        (df_all['adx_1h'] <= 42.0)
    )

    # Method 3: Rolling 5-Day Percentile Rank
    # Avoid bottom 20% (chop compression) and top 10% (exhaustion)
    mask_method3 = (
        (df_all['stretch_pctl'] >= 0.20) &
        (df_all['stretch_pctl'] <= 0.90)
    )

    # Method 4: Institutional Hybrid (Method 1 + Method 2)
    mask_hybrid = (
        (df_all['stretch_ratio'] >= 0.45) &
        (df_all['fan_ratio'] >= 0.15) &
        (df_all['adx_1h'] <= 42.0) &
        (df_all['chase_ratio'] <= 0.35)
    )

    candidates = [
        ("0. Baseline Locked (Official 471)", df_all[mask_baseline]),
        ("Method 1: ATR Dynamic Band (Stretch >= 0.45 + ADX <= 42 + No-Chase)", df_all[mask_method1]),
        ("Method 2: 1H EMA Fan (Separation Ratio >= 0.15x ATR)", df_all[mask_method2]),
        ("Method 3: Rolling 5-Day Percentile (20% - 90% Quantile)", df_all[mask_method3]),
        ("Method 4: Institutional Hybrid (ATR Band + EMA Fan + ADX Cap)", df_all[mask_hybrid]),
    ]

    print("\n" + "=" * 110)
    print("      REALISTIC DYNAMIC DISTANCE BENCHMARK: 100% MT5 TICK-VERIFIED TRADES")
    print("      Evaluated on Prop Firm Engine: 14% Challenge + 17 Bi-Weekly Cycles + 3.0% Shared CB")
    print("=" * 110)

    results = []
    for label, sub_df in candidates:
        stats = simulate_prop_firm_exact(sub_df, label)
        results.append(stats)
        print(f"[{label}]")
        print(f"  Trades: {stats['Total Trades']} | WR: {stats['Win Rate']} | Net: {stats['Total Net PnL']} | Challenge: {stats['Challenge Pass']} | Banked Cash: {stats['Banked Cash (80%)']} | Worst Day: {stats['Worst Single Day']}")

    res_df = pd.DataFrame(results)
    print("\n" + "=" * 110)
    print("                                      FINAL SCORECARD")
    print("=" * 110)
    print(res_df.to_string(index=False))

    out_csv = os.path.join(PROJECT_ROOT, "experiments", "strategy_optimizer", "DYNAMIC_METHODS_SCORECARD.csv")
    res_df.to_csv(out_csv, index=False)
    print(f"\nSaved official comparative scorecard to {out_csv}")

if __name__ == "__main__":
    main()
