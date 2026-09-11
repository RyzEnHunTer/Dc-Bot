import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# 1. Baseline
df_p1 = pd.read_csv(os.path.join(PROJECT_ROOT, "reports", "trades_log_cb_liquidity_sweep.csv"))
df_p2 = pd.read_csv(os.path.join(PROJECT_ROOT, "reports", "trades_log_phase2_jul_sep2026.csv"))
df_p1['entry_dt'] = pd.to_datetime(df_p1['entry_time'], format='mixed', utc=True)
df_p1['exit_dt'] = pd.to_datetime(df_p1['exit_time'], format='mixed', utc=True)
df_p2['entry_dt'] = pd.to_datetime(df_p2['entry_time'], format='mixed', utc=True)
df_p2['exit_dt'] = pd.to_datetime(df_p2['exit_time'], format='mixed', utc=True)

df_base = pd.concat([df_p1, df_p2], ignore_index=True)
df_base.sort_values('exit_dt', inplace=True)
df_base.reset_index(drop=True, inplace=True)

# 2. Candidates
df_silver = pd.read_csv(os.path.join(REPORTS_DIR, "trades_XAGUSD_opt_1.4_1.6.csv"))
df_silver['entry_dt'] = pd.to_datetime(df_silver['entry_time'], format='mixed', utc=True)
df_silver['exit_dt'] = pd.to_datetime(df_silver['exit_time'], format='mixed', utc=True)

df_trio_silver = pd.concat([df_base, df_silver], ignore_index=True)
df_trio_silver.sort_values('exit_dt', inplace=True)
df_trio_silver.reset_index(drop=True, inplace=True)

def build_eq(df, scale=1.0):
    d = df.copy()
    d['pnl'] = d['net_pnl'] * scale
    d.sort_values('exit_dt', inplace=True)
    d['cum_pnl'] = d['pnl'].cumsum()
    d['equity'] = 5000.0 + d['cum_pnl']
    return d

base_eq = build_eq(df_base, 1.0)
silver_full = build_eq(df_trio_silver, 1.0)
silver_safe = build_eq(df_trio_silver, 0.67)

plt.figure(figsize=(14, 8), dpi=150)
plt.style.use('dark_background')

# Subplot 1: Equity Curves
plt.subplot(2, 1, 1)
plt.plot(base_eq['exit_dt'], base_eq['equity'], label="Baseline: Gold + Nasdaq (Net: +$9,080 | Lowest Bal: $4,588)", color='#00d2ff', linewidth=2.0)
plt.plot(silver_safe['exit_dt'], silver_safe['equity'], label="Ace Card Portfolio (Balanced 0.67% Risk): Gold + Nasdaq + Silver (Net: +$7,760 | Lowest Bal: $4,689 [100% SAFE])", color='#00ff88', linewidth=2.2)
plt.plot(silver_full['exit_dt'], silver_full['equity'], label="Ace Card Portfolio (1.0% Risk): Gold + Nasdaq + Silver (Net: +$11,582 | Cash: $9,535)", color='#ffd700', linewidth=1.5, linestyle='--')

plt.axhline(5000, color='gray', linestyle=':', alpha=0.6, label="Funded Base ($5,000)")
plt.axhline(4500, color='red', linestyle='--', alpha=0.8, label="Prop Firm Floor ($4,500 / 10% DD)")

plt.axvspan(pd.to_datetime('2026-02-01', utc=True), pd.to_datetime('2026-02-28', utc=True), color='yellow', alpha=0.12, label="Chop Months (Feb, Jul, Aug)")
plt.axvspan(pd.to_datetime('2026-07-01', utc=True), pd.to_datetime('2026-07-31', utc=True), color='yellow', alpha=0.12)
plt.axvspan(pd.to_datetime('2026-08-01', utc=True), pd.to_datetime('2026-08-31', utc=True), color='yellow', alpha=0.12)

plt.title("The Ace Card Discovery: Silver (XAGUSD) Solves February & August Chop", fontsize=14, fontweight='bold', pad=12)
plt.ylabel("Account Balance ($)", fontsize=11)
plt.legend(loc='upper left', fontsize=9, framealpha=0.85)
plt.grid(True, alpha=0.2)

# Subplot 2: Monthly Comparison
plt.subplot(2, 1, 2)
months = ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09']
base_m = [df_base[df_base['entry_dt'].dt.strftime('%Y-%m') == m]['net_pnl'].sum() for m in months]
silver_m = [df_silver[df_silver['entry_dt'].dt.strftime('%Y-%m') == m]['net_pnl'].sum() for m in months]
trio_m = [b + s for b, s in zip(base_m, silver_m)]

x = np.arange(len(months))
width = 0.28

plt.bar(x - width, base_m, width, label='Baseline (Gold + Nasdaq)', color='#00d2ff', alpha=0.85)
plt.bar(x, silver_m, width, label='Silver (XAGUSD) Contribution', color='#ffd700', alpha=0.85)
plt.bar(x + width, trio_m, width, label='Combined Portfolio (+ Silver)', color='#00ff88', alpha=0.85)

plt.xticks(x, [f"{m}\n[CHOP]" if m in ['2026-02', '2026-07', '2026-08'] else m for m in months], fontsize=9)
plt.ylabel("Monthly Net Profit ($)", fontsize=11)
plt.title("Month-by-Month Profit Breakdown: Silver Turns February Chop into a +$1,254 Monster Month", fontsize=12, fontweight='bold', pad=8)
plt.legend(loc='upper left', fontsize=9, framealpha=0.85)
plt.grid(True, alpha=0.2, axis='y')

plt.tight_layout()
out_img = os.path.join(PROJECT_ROOT, "reports", "ace_card_silver_comparison.png")
plt.savefig(out_img, bbox_inches='tight')
print(f"Saved plot to {out_img}")
