#!/usr/bin/env python3
"""
Official Audit Script: DCC Strategy Final Verified Performance
Replays the locked trade logs and displays key metrics, prop firm compliance,
and monthly breakdown.
"""

import os
import pandas as pd
import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))

def audit_file(path, label):
    if not os.path.exists(path):
        print(f"Error: {path} not found.")
        return
        
    df = pd.read_csv(path)
    df['exit_dt'] = pd.to_datetime(df['exit_time'], format='mixed', utc=True)
    df['entry_dt'] = pd.to_datetime(df['entry_time'], format='mixed', utc=True)
    df = df.sort_values('exit_dt').reset_index(drop=True)
    
    total = len(df)
    wins = df[df['net_pnl'] > 0]
    losses = df[df['net_pnl'] <= 0]
    wr = len(wins) / total * 100
    pnl = df['net_pnl'].sum()
    gp = wins['net_pnl'].sum()
    gl = abs(losses['net_pnl'].sum())
    pf = gp / gl if gl > 0 else 999.0
    
    # Balance & Peak-to-Trough Drawdown
    bal = 5000.0
    equity = [bal]
    for p in df['net_pnl']:
        bal += p
        equity.append(bal)
    eq_s = pd.Series(equity)
    peak = eq_s.cummax()
    pt_dd = ((peak - eq_s) / peak * 100).max()
    lowest = eq_s.min()
    base_dd = (5000.0 - lowest) / 5000.0 * 100.0 if lowest < 5000 else 0.0
    
    # Daily Drawdown
    df['exit_date'] = df['exit_dt'].dt.date
    daily = df.groupby('exit_date')['net_pnl'].sum()
    worst_day = daily.min()
    daily_dd = abs(worst_day) / 5000.0 * 100
    
    # 14-day Bi-Weekly Banked Cash (80% Payouts)
    df['p14'] = (df['exit_dt'] - df['exit_dt'].min()).dt.days // 14
    banked = 0.0
    cur_b = 5000.0
    for p, grp in df.groupby('p14'):
        cur_b += grp['net_pnl'].sum()
        if cur_b > 5000:
            banked += (cur_b - 5000) * 0.80
            cur_b = 5000.0
            
    df['m'] = df['entry_dt'].dt.strftime('%Y-%m')
    m_pnl = df.groupby('m')['net_pnl'].sum()
    
    print("=" * 68)
    print(f"REPORT: {label}")
    print("=" * 68)
    print(f"Source File: {os.path.basename(path)}")
    print(f"Total Trades: {total}")
    print(f"Net Profit: ${pnl:,.2f} (+{pnl/50:.2f}% ROI on $5,000 base capital)")
    print(f"Ending Balance: ${equity[-1]:,.2f}")
    print(f"Win Rate: {wr:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"Profit Factor: {pf:.2f}")
    print(f"Max Peak-to-Trough Drawdown: {pt_dd:.2f}%")
    print(f"Lowest Account Balance: ${lowest:,.2f} (Base Drawdown: {base_dd:.2f}%)")
    print(f"Worst Single Day Loss: -${abs(worst_day):,.2f} (Daily DD: {daily_dd:.2f}%)")
    print(f"14-Day Banked Cash (80% Split): ${banked:,.2f}")
    print("\nMonth-by-Month Record:")
    for m, val in m_pnl.items():
        status = "[GREEN]" if val >= 0 else "[RED]"
        print(f"  {m}: ${val:>9,.2f}  {status}")
    print("=" * 68 + "\n")

if __name__ == "__main__":
    continuous_csv = os.path.join(DIR, "trades_log_official_continuous_no_eod.csv")
    baseline_csv = os.path.join(DIR, "trades_log_official_eod_baseline.csv")
    
    audit_file(continuous_csv, "OFFICIAL FINAL VERSION: CONTINUOUS HOLDING (NO EOD CUTOFF)")
    audit_file(baseline_csv, "OFFICIAL ORIGINAL BASELINE: WITH 21:00 UTC EOD CUTOFF")
