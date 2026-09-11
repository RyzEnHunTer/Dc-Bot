import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# 1. Load Baseline Gold & Nasdaq
df_p1 = pd.read_csv(os.path.join(PROJECT_ROOT, "reports", "trades_log_cb_liquidity_sweep.csv"))
df_p2 = pd.read_csv(os.path.join(PROJECT_ROOT, "reports", "trades_log_phase2_jul_sep2026.csv"))
df_p1['entry_dt'] = pd.to_datetime(df_p1['entry_time'], format='mixed', utc=True)
df_p1['exit_dt'] = pd.to_datetime(df_p1['exit_time'], format='mixed', utc=True)
df_p2['entry_dt'] = pd.to_datetime(df_p2['entry_time'], format='mixed', utc=True)
df_p2['exit_dt'] = pd.to_datetime(df_p2['exit_time'], format='mixed', utc=True)

df_baseline = pd.concat([df_p1, df_p2], ignore_index=True)
df_baseline.sort_values('entry_dt', inplace=True)
df_baseline.reset_index(drop=True, inplace=True)
df_baseline['month'] = df_baseline['entry_dt'].dt.strftime('%Y-%m')

# 2. Load US30 & GBPJPY
df_us30_default = pd.read_csv(os.path.join(REPORTS_DIR, "trades_US30.csv"))
df_us30_default['entry_dt'] = pd.to_datetime(df_us30_default['entry_time'], format='mixed', utc=True)
df_us30_default['exit_dt'] = pd.to_datetime(df_us30_default['exit_time'], format='mixed', utc=True)
df_us30_default['month'] = df_us30_default['entry_dt'].dt.strftime('%Y-%m')

df_us30_opt = pd.read_csv(os.path.join(REPORTS_DIR, "trades_US30_opt_0.8_1.2.csv"))
df_us30_opt['entry_dt'] = pd.to_datetime(df_us30_opt['entry_time'], format='mixed', utc=True)
df_us30_opt['exit_dt'] = pd.to_datetime(df_us30_opt['exit_time'], format='mixed', utc=True)
df_us30_opt['month'] = df_us30_opt['entry_dt'].dt.strftime('%Y-%m')

df_gbpjpy_opt = pd.read_csv(os.path.join(REPORTS_DIR, "trades_GBPJPY_opt_0.8_1.2.csv"))
df_gbpjpy_opt['entry_dt'] = pd.to_datetime(df_gbpjpy_opt['entry_time'], format='mixed', utc=True)
df_gbpjpy_opt['exit_dt'] = pd.to_datetime(df_gbpjpy_opt['exit_time'], format='mixed', utc=True)
df_gbpjpy_opt['month'] = df_gbpjpy_opt['entry_dt'].dt.strftime('%Y-%m')

def simulate_prop_firm(trades_df: pd.DataFrame, risk_scale: float = 1.0):
    df = trades_df.copy()
    df['scaled_pnl'] = df['net_pnl'] * risk_scale
    df.sort_values('entry_dt', inplace=True)
    df.reset_index(drop=True, inplace=True)
    
    # 1. Bi-weekly payouts (14 days reset)
    bal_funded = 5000.0
    lowest_base_bal = 5000.0
    cycle_start = df.iloc[0]['entry_dt']
    payouts = []
    cycle_min = 5000.0
    
    for i, tr in df.iterrows():
        bal_funded += tr['scaled_pnl']
        if bal_funded < lowest_base_bal:
            lowest_base_bal = bal_funded
        if bal_funded < cycle_min:
            cycle_min = bal_funded
            
        days = (tr['exit_dt'] - cycle_start).total_seconds() / 86400.0
        is_last = (i == len(df) - 1)
        
        if days >= 14.0 or is_last:
            profit = bal_funded - 5000.0
            payout_gross = max(0.0, profit)
            payout_80 = payout_gross * 0.80
            payouts.append(payout_80)
            bal_funded = 5000.0
            cycle_min = 5000.0
            cycle_start = tr['exit_dt']
            
    total_banked_cash = sum(payouts)
    max_loss_from_base = (5000.0 - lowest_base_bal) / 5000.0 * 100.0 if lowest_base_bal < 5000.0 else 0.0
    
    # 2. Daily Drawdown (Midnight-to-Midnight)
    df['date'] = df['exit_dt'].dt.strftime('%Y-%m-%d')
    daily_pnl = df.groupby('date')['scaled_pnl'].sum()
    worst_day_loss = daily_pnl.min() if len(daily_pnl) > 0 else 0.0
    max_daily_dd_pct = abs(worst_day_loss) / 5000.0 * 100.0 if worst_day_loss < 0 else 0.0
    
    # 3. Monthly PnLs
    monthly = df.groupby('month')['scaled_pnl'].sum().to_dict()
    tot_pnl = df['scaled_pnl'].sum()
    wr = (df['scaled_pnl'] > 0).mean() * 100.0
    
    feb = monthly.get('2026-02', 0.0)
    jul = monthly.get('2026-07', 0.0)
    aug = monthly.get('2026-08', 0.0)
    chop_tot = feb + jul + aug
    
    return {
        "trades": len(df),
        "total_pnl": tot_pnl,
        "win_rate": wr,
        "banked_cash": total_banked_cash,
        "max_base_loss_pct": max_loss_from_base,
        "lowest_bal": lowest_base_bal,
        "worst_day_loss": worst_day_loss,
        "max_daily_dd_pct": max_daily_dd_pct,
        "monthly": monthly,
        "feb": feb,
        "jul": jul,
        "aug": aug,
        "chop_tot": chop_tot
    }

# Run configurations
res_base = simulate_prop_firm(df_baseline, risk_scale=1.0)

# Trio A: Base + US30 default (1.0% each)
df_trio_a = pd.concat([df_baseline, df_us30_default], ignore_index=True)
res_trio_a = simulate_prop_firm(df_trio_a, risk_scale=1.0)

# Trio B: Base + US30 opt (1.0% each)
df_trio_b = pd.concat([df_baseline, df_us30_opt], ignore_index=True)
res_trio_b = simulate_prop_firm(df_trio_b, risk_scale=1.0)

# Trio C: Base + US30 opt (Risk-balanced 0.67% each -> $33.33 risk)
res_trio_c = simulate_prop_firm(df_trio_b, risk_scale=0.67)

# Trio D: Base + US30 opt (Risk 0.75% each -> $37.50 risk)
res_trio_d = simulate_prop_firm(df_trio_b, risk_scale=0.75)

# Portfolio E: Base + GBPJPY opt (1.0% each)
df_trio_gbp = pd.concat([df_baseline, df_gbpjpy_opt], ignore_index=True)
res_trio_gbp = simulate_prop_firm(df_trio_gbp, risk_scale=1.0)

# Quad: Base + US30 opt + GBPJPY opt (0.5% each)
df_quad = pd.concat([df_baseline, df_us30_opt, df_gbpjpy_opt], ignore_index=True)
res_quad = simulate_prop_firm(df_quad, risk_scale=0.50)

print("=" * 125)
print("                           PROP FIRM MULTI-ASSET PORTFOLIO AUDIT (FULL YEAR 2026)")
print("=" * 125)

configs = [
    ("Baseline (Gold + Nasdaq)", res_base),
    ("Trio A: + US30 Def (1.0% risk)", res_trio_a),
    ("Trio B: + US30 Opt (1.0% risk)", res_trio_b),
    ("Trio C: + US30 Opt (0.67% risk)", res_trio_c),
    ("Trio D: + US30 Opt (0.75% risk)", res_trio_d),
    ("Trio E: + GBPJPY Opt (1.0% risk)", res_trio_gbp),
    ("Quad: All 4 Assets (0.50% risk)", res_quad),
]

print(f"{'Portfolio Configuration':<32} | {'Trades':<6} | {'Total PnL':<11} | {'Banked Cash':<12} | {'Max Base DD':<11} | {'Lowest Bal':<11} | {'Worst Day':<10} | {'Daily DD'}")
print("-" * 125)
for name, r in configs:
    status = "[PASS]" if r['lowest_bal'] >= 4500 and r['max_daily_dd_pct'] < 5.0 else "[FAIL]"
    print(f"{name:<32} | {r['trades']:<6d} | ${r['total_pnl']:<10,.2f} | ${r['banked_cash']:<11,.2f} | {r['max_base_loss_pct']:<10.2f}% | ${r['lowest_bal']:<10,.2f} | ${r['worst_day_loss']:<9,.2f} | {r['max_daily_dd_pct']:<5.2f}% {status}")

print("\n" + "=" * 125)
print("                                 MONTH-BY-MONTH BREAKDOWN ($)")
print("=" * 125)
months = ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09']

header = f"{'Month':<10} | " + " | ".join([f"{name[:14]:<14}" for name, _ in configs])
print(header)
print("-" * 125)

for m in months:
    row = f"{m:<10} | "
    tag = " [CHOP]" if m in ['2026-02', '2026-07', '2026-08'] else ""
    vals = []
    for _, r in configs:
        val = r['monthly'].get(m, 0.0)
        vals.append(f"${val:<13,.2f}")
    print(row + " | ".join(vals) + tag)

print("-" * 125)
chop_row = f"{'CHOP TOTAL':<10} | " + " | ".join([f"${r['chop_tot']:<13,.2f}" for _, r in configs])
print(chop_row)
print("=" * 125)
