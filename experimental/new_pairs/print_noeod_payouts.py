import os
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

df_p1 = pd.read_csv(os.path.join(PROJECT_ROOT, "reports", "trades_log_cb_liquidity_sweep.csv"))
df_p2 = pd.read_csv(os.path.join(PROJECT_ROOT, "reports", "trades_log_phase2_jul_sep2026.csv"))
df_p1['entry_dt'] = pd.to_datetime(df_p1['entry_time'], format='mixed', utc=True)
df_p1['exit_dt'] = pd.to_datetime(df_p1['exit_time'], format='mixed', utc=True)
df_p2['entry_dt'] = pd.to_datetime(df_p2['entry_time'], format='mixed', utc=True)
df_p2['exit_dt'] = pd.to_datetime(df_p2['exit_time'], format='mixed', utc=True)

df_base = pd.concat([df_p1, df_p2], ignore_index=True)
df_base.sort_values('entry_dt', inplace=True)
df_base.reset_index(drop=True, inplace=True)

df_silver = pd.read_csv(os.path.join(REPORTS_DIR, "trades_silver_no_eod.csv"))
df_silver['entry_dt'] = pd.to_datetime(df_silver['entry_time'], format='mixed', utc=True)
df_silver['exit_dt'] = pd.to_datetime(df_silver['exit_time'], format='mixed', utc=True)

df_trio = pd.concat([df_base, df_silver]).sort_values('entry_dt').reset_index(drop=True)

def audit(df, scale):
    d = df.copy()
    d['p'] = d['net_pnl'] * scale
    bal = 5000.0
    min_bal = 5000.0
    c_start = d.iloc[0]['entry_dt']
    payouts = []
    for i, r in d.iterrows():
        bal += r['p']
        if bal < min_bal: min_bal = bal
        days = (r['exit_dt'] - c_start).total_seconds() / 86400.0
        if days >= 14.0 or i == len(d) - 1:
            payouts.append(max(0.0, bal - 5000.0) * 0.80)
            bal = 5000.0
            c_start = r['exit_dt']
    cash = sum(payouts)
    base_dd = (5000.0 - min_bal) / 50.0 if min_bal < 5000.0 else 0.0
    d['date'] = d['exit_dt'].dt.strftime('%Y-%m-%d')
    w_day = d.groupby('date')['p'].sum().min()
    return cash, base_dd, min_bal, w_day

c_base, dd_base, b_base, w_base = audit(df_base, 1.0)
c_full, dd_full, b_full, w_full = audit(df_trio, 1.0)
c_safe, dd_safe, b_safe, w_safe = audit(df_trio, 0.67)

print(f"{'Configuration':<35} | {'Banked Cash':<14} | {'Base DD %':<11} | {'Lowest Bal':<13} | {'Worst Day'}")
print("-" * 95)
print(f"{'Baseline: Gold + Nasdaq (1.0% / $50)':<35} | ${c_base:<13,.2f} | {dd_base:<10.2f}% | ${b_base:<12,.2f} | ${w_base:,.2f}")
print(f"{'Trio Full: + Silver NO EOD (1.0% / $50)':<35} | ${c_full:<13,.2f} | {dd_full:<10.2f}% | ${b_full:<12,.2f} | ${w_full:,.2f}")
print(f"{'Trio Safe: + Silver NO EOD (0.67% / $33)':<35} | ${c_safe:<13,.2f} | {dd_safe:<10.2f}% | ${b_safe:<12,.2f} | ${w_safe:,.2f}")
