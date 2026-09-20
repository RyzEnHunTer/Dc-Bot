"""
DCC Strategy Institutional Benchmark: v1.1 Baseline vs v1.2 TripleGuard Policies
================================================================================
Official Prop Firm Simulation Engine:
- Connects directly to the 471 MT5 real-broker continuous trades (Jan 1 – Sep 8, 2026)
- 14% Evaluation Challenge Phase (Target: +$700 on $5,000 base)
- 17 Bi-Weekly 80% Payout Cycles with $5,000 Capital Resets
- 3.0% ($150) Shared Portfolio Daily Circuit Breaker
- Evaluates:
    * Baseline DCC v1.1 (All 471 trades)
    * Option 1: Pure v1.2 TripleGuard (338 trades)
    * Option 2: v1.2 with Monday PM Blocked (14:00 - 18:00 UTC) (323 trades)
    * Option 3: v1.2 Smart Calendar Shield (Mon PM + Fri 06 & 20) (315 trades)
    * Option 4: v1.2 Half-Risk (0.5%) on Monday PM (338 trades)
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List
import pandas as pd
import numpy as np

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.strategy_optimizer.test_dynamic_distance_methods import (
    load_annotated_official_trades,
    simulate_prop_firm_exact
)


def run_benchmark():
    print("=" * 115)
    print("      DCC CAPITAL STRATEGY INSTITUTIONAL BENCHMARK: v1.1 vs v1.2 TRIPLEGUARD POLICIES")
    print("      Replaying 8.25-Month MT5 Broker Trades on Official Prop Firm Engine ($5,000 Base)")
    print("=" * 115)

    df_all = load_annotated_official_trades()

    # Base TripleGuard v1.2 Mask:
    # 1. Stretch Ratio >= 0.40 (Anti-Chop Floor)
    # 2. 1H ADX <= 45.0 (ADX Exhaustion Ceiling)
    # 3. Chase Ratio <= 0.50 (No-Chase Guard)
    mask_tg = (
        (df_all['stretch_ratio'] >= 0.40) &
        (df_all['adx_1h'] <= 45.0) &
        (df_all['chase_ratio'] <= 0.50)
    )
    df_v12 = df_all[mask_tg].copy()
    df_v12['weekday'] = df_v12['entry_dt'].dt.day_name()
    df_v12['hour'] = df_v12['entry_dt'].dt.hour

    # Masks for Calendar Policies
    # Option 2: Monday PM Block (14:00 - 18:00 UTC)
    is_mon_pm = (df_v12['weekday'] == 'Monday') & (df_v12['hour'].isin([14, 15, 17, 18]))
    
    # Option 3: Smart Calendar Shield (Mon PM + Fri 06:00 & 20:00 UTC)
    is_fri_06 = (df_v12['weekday'] == 'Friday') & (df_v12['hour'] == 6)
    is_fri_20 = (df_v12['weekday'] == 'Friday') & (df_v12['hour'] == 20)
    is_smart_shield = is_mon_pm | is_fri_06 | is_fri_20

    # Option 4: Half-Risk on Monday PM
    df_opt4 = df_v12.copy()
    mon_pm_indices = df_opt4[is_mon_pm].index
    df_opt4.loc[mon_pm_indices, 'net_pnl'] = df_opt4.loc[mon_pm_indices, 'net_pnl'] * 0.50

    policies = [
        ("Baseline DCC v1.1 (Official Reference)", df_all),
        ("Option 1: Pure v1.2 (No Calendar Rules)", df_v12),
        ("Option 2: Hard Block Monday PM (14-18 UTC) [LOCKED]", df_v12[~is_mon_pm].copy()),
        ("Option 3: Smart Calendar Shield (Mon PM + Fri 06 & 20)", df_v12[~is_smart_shield].copy()),
        ("Option 4: Half-Risk (0.5%) on Monday PM", df_opt4),
    ]

    results = []
    for label, sub_df in policies:
        stats = simulate_prop_firm_exact(sub_df, label)
        results.append({
            "Policy / Strategy Version": label,
            "Trades": stats["Total Trades"],
            "Win Rate": stats["Win Rate"],
            "Total Net PnL": stats["Total Net PnL"],
            "Banked Cash (80%)": stats["Banked Cash (80%)"],
            "Max Base DD": stats["Max Base DD"],
            "Worst Single Day": stats["Worst Single Day"],
            "Evaluation Pass": stats["Challenge Pass"],
            "Prop Firm Status": stats["Status"]
        })

    report_df = pd.DataFrame(results)

    print("\n" + "=" * 115)
    print("                                   CANONICAL VERIFICATION SCORECARD")
    print("=" * 115)
    print(report_df.to_string(index=False))
    print("=" * 115)

    # Save CSV
    out_csv = os.path.join(PROJECT_ROOT, "v1.2", "BENCHMARK_RESULTS.csv")
    report_df.to_csv(out_csv, index=False)
    print(f"\n[OK] Canonical benchmark scorecard saved to: {out_csv}")


if __name__ == "__main__":
    run_benchmark()
