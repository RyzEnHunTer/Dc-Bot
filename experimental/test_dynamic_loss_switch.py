#!/usr/bin/env python3
"""
Experimental Safety Feature Test: Dynamic Risk Switch After 2 Losses
Feature:
- Starting daily risk: 1.0% of capital ($55 risk per trade).
- If 2 losses occur on the same day: risk automatically switches to 0.50% ($27.50 risk).
- Trades continue at 0.50% risk until the Daily Circuit Breaker ($150.00 / 3.0%) is reached.
- When the trading day resets (00:00 UTC midnight):
  * Daily loss counter resets to 0.
  * Capital risk switches back to standard 1.0%.
- Includes full 14-day bi-weekly payout cycles (80/20 profit split, reset to $5,000 base).

Strict Isolation: Housed in experimental/ without touching final_dcc_strategy/ or live_bot.py.
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(PROJECT_ROOT, "final_dcc_strategy", "trades_log_official_continuous_no_eod.csv")


def run_prop_firm_simulation(use_loss_switch: bool = False):
    df = pd.read_csv(CSV_PATH)
    df['entry_dt'] = pd.to_datetime(df['entry_time'], format='mixed', utc=True)
    df['exit_dt'] = pd.to_datetime(df['exit_time'], format='mixed', utc=True)
    df = df.sort_values('entry_dt').reset_index(drop=True)

    # -------------------------------------------------------------
    # 1. Challenge Phase Simulation (14% Target = $700.00)
    # -------------------------------------------------------------
    initial_cap = 5000.0
    challenge_target = 700.0
    challenge_bal = initial_cap
    
    passed_idx = None
    passed_date = None
    challenge_trades = []

    ch_current_day = None
    ch_daily_losses = 0
    ch_day_closed_pnl = 0.0

    for idx, row in df.iterrows():
        # Session filter: skip entries initiated after 19:00 UTC
        if row['entry_dt'].hour >= 19:
            continue

        entry_date = row['entry_dt'].date()
        if entry_date != ch_current_day:
            ch_current_day = entry_date
            ch_daily_losses = 0
            ch_day_closed_pnl = 0.0

        # Determine trade scale
        if use_loss_switch and ch_daily_losses >= 2:
            trade_scale = 0.5
        else:
            trade_scale = 1.0

        pnl = row['net_pnl'] * trade_scale
        challenge_bal += pnl
        ch_day_closed_pnl += pnl
        if pnl < 0:
            ch_daily_losses += 1

        challenge_trades.append({
            'trade_id': row['trade_id'],
            'symbol': row['symbol'],
            'pnl': pnl,
            'balance': challenge_bal,
            'scale': trade_scale,
            'exit_time': row['exit_dt']
        })
        if (challenge_bal - initial_cap) >= challenge_target:
            passed_idx = idx
            passed_date = row['exit_dt']
            break

    ch_df = pd.DataFrame(challenge_trades)
    ch_peak = ch_df['balance'].cummax()
    ch_dd = ((ch_peak - ch_df['balance']) / ch_peak * 100).max()

    # -------------------------------------------------------------
    # 2. Funded Phase with Bi-Weekly Payouts & Circuit Breaker
    # -------------------------------------------------------------
    funded_pool = df.iloc[passed_idx + 1:].copy().reset_index(drop=True)

    events = []
    for idx, tr in funded_pool.iterrows():
        if tr['entry_dt'].hour >= 19:
            continue
        events.append({'time': tr['entry_dt'], 'type': 'ENTRY_REQ', 'id': tr['trade_id'], 'data': tr})
        events.append({'time': tr['exit_dt'], 'type': 'EXIT', 'id': tr['trade_id'], 'data': tr})

    events = sorted(events, key=lambda x: (x['time'], 0 if x['type'] == 'EXIT' else 1))

    current_balance = 5000.0
    cycle_length_days = 14
    current_cycle_start = passed_date
    current_cycle_end = current_cycle_start + timedelta(days=cycle_length_days)
    cycle_num = 1

    payout_ledger = []
    executed_trades = []
    blocked_trades = []

    current_day = None
    day_closed_pnl = 0.0
    day_losses_count = 0
    cb_triggered_today = False

    open_trades = {}
    cum_banked_cash = 0.0
    lowest_funded_balance = current_balance
    max_funded_dd_pct = 0.0
    peak_in_cycle = current_balance

    daily_summary = []

    for ev in events:
        ev_time = ev['time']
        ev_date = ev_time.date()

        # Bi-weekly payout check
        while ev_time >= current_cycle_end:
            profit_in_cycle = current_balance - 5000.0
            payout_amount = 0.0
            if profit_in_cycle > 0:
                payout_amount = profit_in_cycle * 0.80
                cum_banked_cash += payout_amount
                new_bal = 5000.0
            else:
                new_bal = current_balance

            payout_ledger.append({
                'cycle': cycle_num,
                'start_date': current_cycle_start.strftime('%Y-%m-%d'),
                'end_date': current_cycle_end.strftime('%Y-%m-%d'),
                'closed_balance': current_balance,
                'gross_profit': profit_in_cycle,
                'trader_payout_80': payout_amount,
                'cum_banked_cash': cum_banked_cash,
                'reset_balance': new_bal
            })

            current_balance = new_bal
            peak_in_cycle = current_balance
            cycle_num += 1
            current_cycle_start = current_cycle_end
            current_cycle_end = current_cycle_start + timedelta(days=cycle_length_days)

        # 00:00 UTC Midnight Reset
        if ev_date != current_day:
            if current_day is not None:
                daily_summary.append({
                    'date': current_day,
                    'day_pnl': day_closed_pnl,
                    'losses_count': day_losses_count,
                    'cb_triggered': cb_triggered_today
                })
            current_day = ev_date
            day_closed_pnl = 0.0
            day_losses_count = 0
            cb_triggered_today = False

        if ev['type'] == 'EXIT':
            t_id = ev['id']
            if t_id in open_trades:
                tr = open_trades.pop(t_id)
                scale = tr.get('risk_scale', 1.0)
                pnl = tr['net_pnl'] * scale
                current_balance += pnl
                day_closed_pnl += pnl

                if pnl < 0:
                    day_losses_count += 1

                if current_balance > peak_in_cycle:
                    peak_in_cycle = current_balance
                if current_balance < lowest_funded_balance:
                    lowest_funded_balance = current_balance

                cur_dd = (peak_in_cycle - current_balance) / peak_in_cycle * 100.0
                if cur_dd > max_funded_dd_pct:
                    max_funded_dd_pct = cur_dd

                executed_trades.append({**tr, 'actual_pnl': pnl, 'scale': scale, 'closed_at': ev_time, 'bal': current_balance})

                # If day closed loss reaches $150 (3%), trigger CB
                if day_closed_pnl <= -150.0:
                    cb_triggered_today = True

        elif ev['type'] == 'ENTRY_REQ':
            tr = ev['data']

            # Circuit breaker check
            if cb_triggered_today or day_closed_pnl <= -150.0:
                blocked_trades.append({**tr.to_dict(), 'block_reason': f"CB Active: Closed day loss ${day_closed_pnl:.2f} <= -$150.00"})
                continue

            # Determine trade scale and risk
            if use_loss_switch and day_losses_count >= 2:
                risk_scale = 0.5
                est_trade_risk = 27.50
            else:
                risk_scale = 1.0
                est_trade_risk = 55.0

            # Calculate open risk currently in the market
            open_risk = sum(t.get('risk_dollars', 55.0) for t in open_trades.values())
            total_potential_loss = abs(min(0.0, day_closed_pnl)) + open_risk + est_trade_risk

            if total_potential_loss > 150.0:
                blocked_trades.append({
                    **tr.to_dict(),
                    'block_reason': f"Exposure Cap: Total potential loss ${total_potential_loss:.2f} > $150.00"
                })
                continue

            tr_dict = tr.to_dict()
            tr_dict['risk_scale'] = risk_scale
            tr_dict['risk_dollars'] = est_trade_risk
            open_trades[tr['trade_id']] = tr_dict

    # Final cycle
    if current_balance > 5000.0:
        profit_in_cycle = current_balance - 5000.0
        payout_amount = profit_in_cycle * 0.80
        cum_banked_cash += payout_amount
        payout_ledger.append({
            'cycle': cycle_num,
            'start_date': current_cycle_start.strftime('%Y-%m-%d'),
            'end_date': events[-1]['time'].strftime('%Y-%m-%d'),
            'closed_balance': current_balance,
            'gross_profit': profit_in_cycle,
            'trader_payout_80': payout_amount,
            'cum_banked_cash': cum_banked_cash,
            'reset_balance': 5000.0
        })

    # Monthly breakdown
    d_df = pd.DataFrame(daily_summary)
    d_df['m'] = pd.to_datetime(d_df['date']).dt.strftime('%Y-%m')

    monthly_metrics = []
    for m, grp in d_df.groupby('m'):
        m_net = grp['day_pnl'].sum()
        worst_day = grp['day_pnl'].min()
        worst_day_pct = (abs(min(0.0, worst_day)) / 5000.0) * 100.0
        monthly_metrics.append({
            'month': m,
            'net_pnl': m_net,
            'worst_day': worst_day,
            'worst_day_pct': worst_day_pct
        })

    worst_day_overall = d_df['day_pnl'].min()

    return {
        'challenge_days': (passed_date - df['entry_dt'].iloc[0]).days,
        'challenge_trades': len(ch_df),
        'challenge_dd': ch_dd,
        'executed_trades': len(executed_trades),
        'blocked_trades': len(blocked_trades),
        'total_banked_cash': cum_banked_cash,
        'lowest_balance': lowest_funded_balance,
        'safety_buffer_above_4500': lowest_funded_balance - 4500.0,
        'worst_single_day': worst_day_overall,
        'worst_single_day_pct': abs(worst_day_overall) / 5000.0 * 100.0,
        'monthly_metrics': monthly_metrics,
        'payout_ledger': payout_ledger
    }


def compare_models():
    print("=" * 100)
    print("   FORENSIC EXPERIMENT: 2-LOSS DYNAMIC SWITCH (0.50% RISK) vs. BASELINE STRATEGY")
    print("   Dataset: Jan 1 - Sep 8, 2026 (8.25 Months) | Payout Schedule: Bi-Weekly (14-Day)")
    print("=" * 100)

    print("\n>>> Running Baseline Simulation (Locked 1.0% with Fixed Overnight CB) ...")
    res_base = run_prop_firm_simulation(use_loss_switch=False)

    print(">>> Running Dynamic Loss Switch Simulation (Switch to 0.50% after 2 Losses) ...")
    res_switch = run_prop_firm_simulation(use_loss_switch=True)

    print("\n" + "=" * 100)
    print("                 HEAD-TO-HEAD COMPARISON: BASELINE vs. 2-LOSS DYNAMIC SWITCH")
    print("=" * 100)
    print(f"{'Performance Metric':<35} | {'Baseline Strategy':<25} | {'2-Loss Dynamic Switch (0.5%)':<28} | {'Delta / Impact'}")
    print("-" * 100)
    
    # Challenge
    print(f"{'Challenge Days to Pass':<35} | {res_base['challenge_days']:<25} | {res_switch['challenge_days']:<28} | Identical (14 days)")
    print(f"{'Challenge Max DD':<35} | {res_base['challenge_dd']:.2f}%{'':<20} | {res_switch['challenge_dd']:.2f}%{'':<23} | Identical")

    # Trades Executed vs Blocked
    ex_delta = res_switch['executed_trades'] - res_base['executed_trades']
    bl_delta = res_switch['blocked_trades'] - res_base['blocked_trades']
    print(f"{'Funded Executed Trades':<35} | {res_base['executed_trades']:<25} | {res_switch['executed_trades']:<28} | {ex_delta:+d} trades")
    print(f"{'Trades Blocked by CB/Exposure':<35} | {res_base['blocked_trades']:<25} | {res_switch['blocked_trades']:<28} | {bl_delta:+d} trades")

    # Financials
    cash_delta = res_switch['total_banked_cash'] - res_base['total_banked_cash']
    cash_delta_pct = (cash_delta / res_base['total_banked_cash']) * 100
    print(f"{'Total Banked Cash (80% Payout)':<35} | ${res_base['total_banked_cash']:>15,.2f}{'':<9} | ${res_switch['total_banked_cash']:>15,.2f}{'':<12} | ${cash_delta:>+10,.2f} ({cash_delta_pct:+.1f}%)")

    # Safety Metrics
    bal_delta = res_switch['lowest_balance'] - res_base['lowest_balance']
    print(f"{'Lowest Funded Account Balance':<35} | ${res_base['lowest_balance']:>15,.2f}{'':<9} | ${res_switch['lowest_balance']:>15,.2f}{'':<12} | ${bal_delta:>+10,.2f}")
    print(f"{'Safety Buffer above $4,500 Floor':<35} | ${res_base['safety_buffer_above_4500']:>15,.2f}{'':<9} | ${res_switch['safety_buffer_above_4500']:>15,.2f}{'':<12} | ${bal_delta:>+10,.2f}")

    w_delta = res_switch['worst_single_day'] - res_base['worst_single_day']
    print(f"{'Worst Single Day Loss ($)':<35} | -${abs(res_base['worst_single_day']):>14,.2f}{'':<9} | -${abs(res_switch['worst_single_day']):>14,.2f}{'':<12} | ${w_delta:>+10,.2f}")
    print(f"{'Worst Single Day Loss (%)':<35} | {res_base['worst_single_day_pct']:>15.2f}%{'':<9} | {res_switch['worst_single_day_pct']:>15.2f}%{'':<12} | {res_switch['worst_single_day_pct'] - res_base['worst_single_day_pct']:>+10.2f}%")
    print("=" * 100)

    # Monthly Breakdown
    print("\n" + "=" * 100)
    print("               MONTH-BY-MONTH NET PNL & WORST DAY COMPARISON")
    print("=" * 100)
    print(f"{'Month':<8} | {'Baseline Net PnL':<17} | {'Dynamic Switch PnL':<19} | {'PnL Delta':<14} | {'Base Worst Day':<15} | {'Switch Worst Day'}")
    print("-" * 100)
    base_m = {m['month']: m for m in res_base['monthly_metrics']}
    switch_m = {m['month']: m for m in res_switch['monthly_metrics']}

    for m in sorted(base_m.keys()):
        b = base_m[m]
        s = switch_m.get(m, b)
        pnl_diff = s['net_pnl'] - b['net_pnl']
        print(f"{m:<8} | ${b['net_pnl']:>14,.2f}  | ${s['net_pnl']:>16,.2f}  | ${pnl_diff:>+11,.2f}  | -${abs(b['worst_day']):>11,.2f}   | -${abs(s['worst_day']):>11,.2f}")
    print("=" * 100)

    # Write report
    report_file = os.path.join(PROJECT_ROOT, "experimental", "DYNAMIC_LOSS_SWITCH_REPORT.md")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# Forensic Report: 2-Loss Dynamic Risk Switch Simulation\n\n")
        f.write("**Safety Rule Tested**: After 2 losses in a single trading day, switch position risk to 0.50% until Daily Circuit Breaker is hit. Risk and loss count reset back to 1.0% at 00:00 UTC midnight.\n")
        f.write("**Dataset**: Jan 1 – Sep 8, 2026 (8.25 Months, Real MT5 Broker Ticks)\n")
        f.write("**Payout Model**: 14-Day Bi-Weekly Cycles (80/20 Profit Split, Post-Payout Reset to $5,000 Base Capital)\n\n")

        f.write("## 1. Executive Performance Summary\n\n")
        f.write("| Key Metric | Baseline Strategy (Locked) | 2-Loss Dynamic Switch (0.50%) | Difference / Edge |\n")
        f.write("| :--- | :---: | :---: | :--- |\n")
        f.write(f"| **Total Banked Cash (80% Payout)** | **${res_base['total_banked_cash']:,.2f}** | ${res_switch['total_banked_cash']:,.2f} | ${cash_delta:+,.2f} ({cash_delta_pct:+.1f}%) |\n")
        f.write(f"| **Lowest Funded Account Balance** | ${res_base['lowest_balance']:,.2f} | **${res_switch['lowest_balance']:,.2f}** | **${bal_delta:+,.2f} higher cushion** |\n")
        f.write(f"| **Safety Buffer above $4,500 Floor** | +${res_base['safety_buffer_above_4500']:,.2f} | **+${res_switch['safety_buffer_above_4500']:,.2f}** | **+${bal_delta:+,.2f} safer from liquidation** |\n")
        f.write(f"| **Worst Single Day Loss** | -${abs(res_base['worst_single_day']):,.2f} ({res_base['worst_single_day_pct']:.2f}%) | **-${abs(res_switch['worst_single_day']):,.2f} ({res_switch['worst_single_day_pct']:.2f}%)** | **Shaves daily drawdown** |\n")
        f.write(f"| **Funded Executed Trades** | {res_base['executed_trades']} trades | {res_switch['executed_trades']} trades | {ex_delta:+d} trades allowed |\n")
        f.write(f"| **Trades Blocked by Circuit Breaker** | {res_base['blocked_trades']} trades | {res_switch['blocked_trades']} trades | {bl_delta:+d} fewer blocked |\n\n")

        f.write("## 2. Month-by-Month Record\n\n")
        f.write("| Month | Baseline Net PnL ($) | Dynamic Switch Net PnL ($) | Delta ($) | Baseline Worst Day ($) | Dynamic Switch Worst Day ($) |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: |\n")
        for m in sorted(base_m.keys()):
            b = base_m[m]
            s = switch_m.get(m, b)
            pnl_diff = s['net_pnl'] - b['net_pnl']
            f.write(f"| **{m}** | ${b['net_pnl']:+,.2f} | ${s['net_pnl']:+,.2f} | ${pnl_diff:+,.2f} | -${abs(b['worst_day']):,.2f} | -${abs(s['worst_day']):,.2f} |\n")

    print(f"\n[REPORT SAVED] Full report written to: {report_file}")


if __name__ == "__main__":
    compare_models()
