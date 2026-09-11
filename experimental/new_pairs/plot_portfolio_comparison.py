import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# 1. Load Baseline Gold & Nasdaq
df_p1 = pd.read_csv(os.path.join(PROJECT_ROOT, "reports", "trades_log_cb_liquidity_sweep.csv"))
df_p2 = pd.read_csv(os.path.join(PROJECT_ROOT, "reports", "trades_log_phase2_jul_sep2026.csv"))
df_p1['entry_dt'] = pd.to_datetime(df_p1['entry_time'], format='mixed', utc=True)
df_p1['exit_dt'] = pd.to_datetime(df_p1['exit_time'], format='mixed', utc=True)
df_p2['entry_dt'] = pd.to_datetime(df_p2['entry_time'], format='mixed', utc=True)
df_p2['exit_dt'] = pd.to_datetime(df_p2['exit_time'], format='mixed', utc=True)

df_base = pd.concat([df_p1, df_p2], ignore_index=True)
df_base.sort_values('entry_dt', inplace=True)
df_base.reset_index(drop=True, inplace=True)

# 2. Load US30 Opt
df_us30 = pd.read_csv(os.path.join(REPORTS_DIR, "trades_US30_opt_0.8_1.2.csv"))
df_us30['entry_dt'] = pd.to_datetime(df_us30['entry_time'], format='mixed', utc=True)
df_us30['exit_dt'] = pd.to_datetime(df_us30['exit_time'], format='mixed', utc=True)

# 3. Combine
df_trio = pd.concat([df_base, df_us30], ignore_index=True)
df_trio.sort_values('entry_dt', inplace=True)
df_trio.reset_index(drop=True, inplace=True)

# Build equity series over time
def build_equity_series(df, scale=1.0):
    d = df.copy()
    d['pnl'] = d['net_pnl'] * scale
    d.sort_values('exit_dt', inplace=True)
    d['cum_pnl'] = d['pnl'].cumsum()
    d['equity'] = 5000.0 + d['cum_pnl']
    return d

base_eq = build_equity_series(df_base, 1.0)
trio_eq = build_equity_series(df_trio, 1.0)
trio_safe_eq = build_equity_series(df_trio, 0.67)

plt.figure(figsize=(14, 8), dpi=150)
plt.style.use('dark_background')

plt.subplot(2, 1, 1)
plt.plot(base_eq['exit_dt'], base_eq['equity'], label=f"Baseline: Gold + Nasdaq (+$9,080 | Cash: $7,585)", color='#00d2ff', linewidth=2.0)
plt.plot(trio_safe_eq['exit_dt'], trio_safe_eq['equity'], label=f"Trio (Balanced 0.67% Risk): Gold + Nasdaq + US30 (+$7,632 | Max Base DD: 9.75% [SAFE])", color='#00ff88', linewidth=2.0)
plt.plot(trio_eq['exit_dt'], trio_eq['equity'], label=f"Trio (1.0% Risk): Gold + Nasdaq + US30 (+$11,390 | Cash: $9,539)", color='#ffbb00', linewidth=1.5, linestyle='--')

plt.axhline(5000, color='gray', linestyle=':', alpha=0.6, label="Base Capital ($5,000)")
plt.axhline(4500, color='red', linestyle='--', alpha=0.8, label="Prop Firm Floor ($4,500 / 10% DD)")

# Highlight chop months
plt.axvspan(pd.to_datetime('2026-02-01', utc=True), pd.to_datetime('2026-02-28', utc=True), color='yellow', alpha=0.12, label="Chop Months (Feb, Jul, Aug)")
plt.axvspan(pd.to_datetime('2026-07-01', utc=True), pd.to_datetime('2026-07-31', utc=True), color='yellow', alpha=0.12)
plt.axvspan(pd.to_datetime('2026-08-01', utc=True), pd.to_datetime('2026-08-31', utc=True), color='yellow', alpha=0.12)

plt.title("2026 Equity Curve: Baseline (Gold+Nasdaq) vs Multi-Asset Trio (+ US30 Dow Jones)", fontsize=14, fontweight='bold', pad=12)
plt.ylabel("Account Balance ($)", fontsize=11)
plt.legend(loc='upper left', fontsize=9, framealpha=0.8)
plt.grid(True, alpha=0.2)

# Subplot 2: Monthly Comparison
plt.subplot(2, 1, 2)
months = ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09']
base_m = [df_base[df_base['entry_dt'].dt.strftime('%Y-%m') == m]['net_pnl'].sum() for m in months]
us30_m = [df_us30[df_us30['entry_dt'].dt.strftime('%Y-%m') == m]['net_pnl'].sum() for m in months]
trio_m = [b + u for b, u in zip(base_m, us30_m)]

x = np.arange(len(months))
width = 0.28

plt.bar(x - width, base_m, width, label='Baseline (Gold + Nasdaq)', color='#00d2ff', alpha=0.85)
plt.bar(x, us30_m, width, label='US30 Contribution', color='#ffbb00', alpha=0.85)
plt.bar(x + width, trio_m, width, label='Trio Combined (1.0% Risk)', color='#00ff88', alpha=0.85)

plt.xticks(x, [f"{m}\n[CHOP]" if m in ['2026-02', '2026-07', '2026-08'] else m for m in months], fontsize=9)
plt.ylabel("Monthly Net Profit ($)", fontsize=11)
plt.title("Monthly Net Profit Breakdown Across 2026", fontsize=12, fontweight='bold', pad=8)
plt.legend(loc='upper left', fontsize=9, framealpha=0.8)
plt.grid(True, alpha=0.2, axis='y')

plt.tight_layout()
out_img = os.path.join(PROJECT_ROOT, "reports", "multi_asset_us30_comparison.png")
plt.savefig(out_img, bbox_inches='tight')
print(f"Saved plot to {out_img}")
