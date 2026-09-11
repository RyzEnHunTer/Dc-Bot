import os
import sys
import pandas as pd
import numpy as np

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

# 2. Load Candidates
def load_candidate(fname):
    df = pd.read_csv(os.path.join(REPORTS_DIR, fname))
    df['entry_dt'] = pd.to_datetime(df['entry_time'], format='mixed', utc=True)
    df['exit_dt'] = pd.to_datetime(df['exit_time'], format='mixed', utc=True)
    df['month'] = df['entry_dt'].dt.strftime('%Y-%m')
    return df

df_us30 = load_candidate("trades_US30_opt_0.8_1.2.csv")
df_silver = load_candidate("trades_XAGUSD_opt_1.4_1.6.csv")
df_audjpy = load_candidate("trades_AUDJPY_opt_1.2_1.4.csv")

def simulate_prop_firm(trades_df: pd.DataFrame, risk_scale: float = 1.0):
    df = trades_df.copy()
    df['scaled_pnl'] = df['net_pnl'] * risk_scale
    df.sort_values('entry_dt', inplace=True)
    df.reset_index(drop=True, inplace=True)
    
    # Bi-weekly payouts (14 days reset)
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
    
    # Daily Drawdown (Midnight-to-Midnight)
    df['date'] = df['exit_dt'].dt.strftime('%Y-%m-%d')
    daily_pnl = df.groupby('date')['scaled_pnl'].sum()
    worst_day_loss = daily_pnl.min() if len(daily_pnl) > 0 else 0.0
    max_daily_dd_pct = abs(worst_day_loss) / 5000.0 * 100.0 if worst_day_loss < 0 else 0.0
    
    # Monthly PnLs
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

# Build Combinations
res_base = simulate_prop_firm(df_baseline, 1.0)

# Trio US30
df_trio_us30 = pd.concat([df_baseline, df_us30], ignore_index=True)
res_us30_full = simulate_prop_firm(df_trio_us30, 1.0)
res_us30_safe = simulate_prop_firm(df_trio_us30, 0.67)

# Trio Silver (XAGUSD)
df_trio_silver = pd.concat([df_baseline, df_silver], ignore_index=True)
res_silver_full = simulate_prop_firm(df_trio_silver, 1.0)
res_silver_safe = simulate_prop_firm(df_trio_silver, 0.67)

# Trio AUDJPY
df_trio_audjpy = pd.concat([df_baseline, df_audjpy], ignore_index=True)
res_audjpy_full = simulate_prop_firm(df_trio_audjpy, 1.0)
res_audjpy_safe = simulate_prop_firm(df_trio_audjpy, 0.67)

print("=" * 135)
print("                          ACE CARD CANDIDATES: PROP FIRM HEAD-TO-HEAD AUDIT (2026)")
print("=" * 135)

configs = [
    ("Baseline: Gold + Nasdaq (1.0% / $50)", res_base),
    ("Trio US30: + US30 (1.0% / $50)", res_us30_full),
    ("Trio US30 Safe: + US30 (0.67% / $33.33)", res_us30_safe),
    ("Trio Silver: + Silver XAG (1.0% / $50)", res_silver_full),
    ("Trio Silver Safe: + Silver XAG (0.67% / $33.33)", res_silver_safe),
    ("Trio AUDJPY: + AUDJPY (1.0% / $50)", res_audjpy_full),
    ("Trio AUDJPY Safe: + AUDJPY (0.67% / $33.33)", res_audjpy_safe),
]

print(f"{'Portfolio Configuration':<38} | {'Trades':<6} | {'Total PnL':<11} | {'Banked Cash':<12} | {'Max Base DD':<11} | {'Lowest Bal':<11} | {'Worst Day':<10} | {'Daily DD'}")
print("-" * 135)
for name, r in configs:
    status = "[PASS]" if r['lowest_bal'] >= 4500 and r['max_daily_dd_pct'] < 5.0 else "[FAIL]"
    print(f"{name:<38} | {r['trades']:<6d} | ${r['total_pnl']:<10,.2f} | ${r['banked_cash']:<11,.2f} | {r['max_base_loss_pct']:<10.2f}% | ${r['lowest_bal']:<10,.2f} | ${r['worst_day_loss']:<9,.2f} | {r['max_daily_dd_pct']:<5.2f}% {status}")

print("\n" + "=" * 135)
print("                                 MONTH-BY-MONTH NET PROFIT BREAKDOWN ($)")
print("=" * 135)
months = ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09']

header = f"{'Month':<10} | " + " | ".join([f"{name[:14]:<14}" for name, _ in configs])
print(header)
print("-" * 135)

for m in months:
    row = f"{m:<10} | "
    tag = " [CHOP]" if m in ['2026-02', '2026-07', '2026-08'] else ""
    vals = []
    for _, r in configs:
        val = r['monthly'].get(m, 0.0)
        vals.append(f"${val:<13,.2f}")
    print(row + " | ".join(vals) + tag)

print("-" * 135)
chop_row = f"{'CHOP TOTAL':<10} | " + " | ".join([f"${r['chop_tot']:<13,.2f}" for _, r in configs])
print(chop_row)
print("=" * 135)
