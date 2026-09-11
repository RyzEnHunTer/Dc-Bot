"""
3-Asset Portfolio Simulation: Gold (XAUUSD) + Nasdaq (NAS100) + Dow Jones (US30).
Tests whether adding US30 hedges Gold & Nasdaq chop months (Feb, Jul, Aug) and boosts prop firm payouts.
"""

import os
import sys
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# Load existing Phase 1 & Phase 2 trades for Gold & Nasdaq
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

# Load US30 trades
us30_csv = os.path.join(REPORTS_DIR, "trades_US30.csv")
df_us30 = pd.read_csv(us30_csv)
df_us30['entry_dt'] = pd.to_datetime(df_us30['entry_time'], format='mixed', utc=True)
df_us30['exit_dt'] = pd.to_datetime(df_us30['exit_time'], format='mixed', utc=True)
df_us30['month'] = df_us30['entry_dt'].dt.strftime('%Y-%m')

# Combine 3 Assets
df_trio = pd.concat([df_baseline, df_us30], ignore_index=True)
df_trio.sort_values('entry_dt', inplace=True)
df_trio.reset_index(drop=True, inplace=True)


def simulate_prop_firm_payouts(trades_df: pd.DataFrame, label: str):
    trades = trades_df.copy().reset_index(drop=True)
    bal_funded = 5000.0
    lowest_base_bal = 5000.0
    cycle_start = trades.iloc[0]['entry_dt']
    payouts = []
    cycle_trades = []
    cycle_min = 5000.0
    cycle_num = 1

    for i, tr in trades.iterrows():
        bal_funded += tr['net_pnl']
        if bal_funded < lowest_base_bal:
            lowest_base_bal = bal_funded
        if bal_funded < cycle_min:
            cycle_min = bal_funded
        cycle_trades.append(tr)

        days = (tr['exit_dt'] - cycle_start).total_seconds() / 86400.0
        is_last = (i == len(trades) - 1)

        if days >= 14.0 or is_last:
            profit = bal_funded - 5000.0
            payout_gross = max(0.0, profit)
            payout_80 = payout_gross * 0.80
            cycle_loss_from_base = (5000.0 - cycle_min) / 5000.0 * 100.0 if cycle_min < 5000.0 else 0.0

            payouts.append({
                'cycle': f'Cycle {cycle_num}',
                'start': str(cycle_start)[:10],
                'end': str(tr['exit_dt'])[:10],
                'trades': len(cycle_trades),
                'profit': profit,
                'payout_80': payout_80,
                'min_bal': cycle_min,
                'base_loss': cycle_loss_from_base
            })
            bal_funded = 5000.0
            cycle_min = 5000.0
            cycle_start = tr['exit_dt']
            cycle_trades = []
            cycle_num += 1

    max_loss_from_base = (5000.0 - lowest_base_bal) / 5000.0 * 100.0 if lowest_base_bal < 5000.0 else 0.0
    total_cash = sum(p['payout_80'] for p in payouts)

    return {
        "label": label,
        "trades": len(trades),
        "net_pnl": trades['net_pnl'].sum(),
        "total_cash": total_cash,
        "max_loss_from_base": max_loss_from_base,
        "lowest_base_bal": lowest_base_bal,
        "payouts": payouts
    }


def compare_portfolio():
    print("=" * 105)
    print("     PORTFOLIO HEAD-TO-HEAD: BASELINE (GOLD + NASDAQ) vs TRIO (GOLD + NASDAQ + US30)")
    print("                         Full Year 2026 (January 1 - September 8, 2026)")
    print("=" * 105)

    months = sorted(df_baseline['month'].unique())

    print(f"\n1. MONTH-BY-MONTH NET PROFIT COMPARISON ($):")
    print(f"{'Month':<10} | {'Baseline (Gold+Nas)':<22} | {'US30 Profit':<14} | {'Trio Portfolio (All 3)':<24} | {'Chop Impact'}")
    print("-" * 95)

    base_tot = 0.0
    trio_tot = 0.0

    for m in months:
        b_pnl = df_baseline[df_baseline['month'] == m]['net_pnl'].sum()
        u_pnl = df_us30[df_us30['month'] == m]['net_pnl'].sum() if len(df_us30[df_us30['month'] == m]) > 0 else 0.0
        t_pnl = b_pnl + u_pnl

        base_tot += b_pnl
        trio_tot += t_pnl

        tag = ""
        if m in ["2026-02", "2026-07", "2026-08"]:
            tag = "[CHOP MONTH]"

        print(f"{m:<10} | ${b_pnl:<21,.2f} | ${u_pnl:<13,.2f} | ${t_pnl:<23,.2f} | {tag}")

    print("-" * 95)
    print(f"{'TOTAL':<10} | ${base_tot:<21,.2f} | ${df_us30['net_pnl'].sum():<13,.2f} | ${trio_tot:<23,.2f} | +${trio_tot - base_tot:,.2f} BOOST")

    # 2. Prop Firm Bi-Weekly Payout Comparison
    r_base = simulate_prop_firm_payouts(df_baseline, "Baseline (Gold + Nasdaq)")
    r_trio = simulate_prop_firm_payouts(df_trio, "Trio Portfolio (Gold + Nasdaq + US30)")

    print("\n" + "=" * 105)
    print("2. PROP FIRM 14-DAY BI-WEEKLY PAYOUT COMPARISON (80% PROFIT SPLIT):")
    print("=" * 105)
    print(f"  Baseline Banked Cash (Gold + Nasdaq):    ${r_base['total_cash']:,.2f} (Across {r_base['trades']} trades)")
    print(f"  Trio Portfolio Banked Cash (+ US30):     ${r_trio['total_cash']:,.2f} (Across {r_trio['trades']} trades)")
    print(f"  NET EXTRA CASH IN POCKET:                +${r_trio['total_cash'] - r_base['total_cash']:,.2f} (+{(r_trio['total_cash'] - r_base['total_cash'])/r_base['total_cash']*100:.1f}%)")
    print(f"  Baseline Max Drop Below Base:            {r_base['max_loss_from_base']:.2f}% (Lowest: ${r_base['lowest_base_bal']:,.2f})")
    print(f"  Trio Portfolio Max Drop Below Base:      {r_trio['max_loss_from_base']:.2f}% (Lowest: ${r_trio['lowest_base_bal']:,.2f})")
    print("=" * 105)


if __name__ == "__main__":
    compare_portfolio()
