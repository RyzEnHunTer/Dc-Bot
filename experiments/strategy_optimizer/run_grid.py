"""
Master Parameter Grid Runner for DCC Strategy Optimization.
Strictly isolated in experiments/strategy_optimizer/ - DOES NOT TOUCH PRODUCTION FILES.

Executes across all of 2026 for both XAUUSD and NAS100:
- Compares Sweep ON vs Sweep OFF
- Tests 1H EMA Stretch Guard thresholds (0.85, 1.0, 1.25, None)
- Tests 5M Chop Filter (flips in last 12 bars)
- Tests Killzone Hours [9, 13] vs Full Session Continuous
- Tests Dynamic 2H TP Sizing vs Fixed TP
- Evaluates Month-by-Month Killzone Efficacy
- Checks performance specifically on Tuesday, September 15, 2026
"""

import os
import sys
import time
from typing import Dict, List
import pandas as pd

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.strategy_optimizer.optimizer_engine import StrategyConfig
from experiments.strategy_optimizer.backtester import (
    BacktestDataset,
    simulate_strategy,
    calculate_comprehensive_metrics
)

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "experiments", "strategy_optimizer")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def define_parameter_grid() -> List[StrategyConfig]:
    configs = [
        # 1. Baseline Current Locked Config
        StrategyConfig(
            name="1_Baseline_Locked_SweepON_KZ_Blocked",
            use_sweep=True,
            max_h1_stretch=None,
            max_recent_flips=None,
            tp_model="fixed",
            blocked_hours=[9, 13]
        ),
        # 2. Pure DCC (Sweep OFF)
        StrategyConfig(
            name="2_Pure_DCC_SweepOFF_KZ_Blocked",
            use_sweep=False,
            max_h1_stretch=None,
            max_recent_flips=None,
            tp_model="fixed",
            blocked_hours=[9, 13]
        ),
        # 3. Full Session Continuous (No Dead Hours, to audit KZ by month)
        StrategyConfig(
            name="3_SweepON_Full_Session_No_KZ_Block",
            use_sweep=True,
            max_h1_stretch=None,
            max_recent_flips=None,
            tp_model="fixed",
            blocked_hours=[]
        ),
        StrategyConfig(
            name="4_SweepOFF_Full_Session_No_KZ_Block",
            use_sweep=False,
            max_h1_stretch=None,
            max_recent_flips=None,
            tp_model="fixed",
            blocked_hours=[]
        ),
        # 5. 1H EMA Stretch Guard Variations (with Sweep OFF)
        StrategyConfig(
            name="5_SweepOFF_Stretch_0.85ATR",
            use_sweep=False,
            max_h1_stretch=0.85,
            max_recent_flips=None,
            tp_model="fixed",
            blocked_hours=[]
        ),
        StrategyConfig(
            name="6_SweepOFF_Stretch_1.00ATR",
            use_sweep=False,
            max_h1_stretch=1.00,
            max_recent_flips=None,
            tp_model="fixed",
            blocked_hours=[]
        ),
        # 7. 1H EMA Stretch Guard Variations (with Sweep ON)
        StrategyConfig(
            name="7_SweepON_Stretch_0.85ATR",
            use_sweep=True,
            max_h1_stretch=0.85,
            max_recent_flips=None,
            tp_model="fixed",
            blocked_hours=[]
        ),
        StrategyConfig(
            name="8_SweepON_Stretch_1.00ATR",
            use_sweep=True,
            max_h1_stretch=1.00,
            max_recent_flips=None,
            tp_model="fixed",
            blocked_hours=[]
        ),
        # 8. 5M Chop Consolidation Filter (max 1 flip in 12 bars)
        StrategyConfig(
            name="9_SweepOFF_ChopFilter_Max1Flip",
            use_sweep=False,
            max_h1_stretch=None,
            max_recent_flips=1,
            tp_model="fixed",
            blocked_hours=[]
        ),
        StrategyConfig(
            name="10_SweepON_ChopFilter_Max1Flip",
            use_sweep=True,
            max_h1_stretch=None,
            max_recent_flips=1,
            tp_model="fixed",
            blocked_hours=[]
        ),
        # 9. Dynamic 2H TP Sizing
        StrategyConfig(
            name="11_SweepOFF_Dynamic2H_TP",
            use_sweep=False,
            max_h1_stretch=None,
            max_recent_flips=None,
            tp_model="dynamic_2h",
            blocked_hours=[]
        ),
        StrategyConfig(
            name="12_SweepON_Dynamic2H_TP",
            use_sweep=True,
            max_h1_stretch=None,
            max_recent_flips=None,
            tp_model="dynamic_2h",
            blocked_hours=[]
        ),
        # 10. Synergistic High-Conviction Configurations
        StrategyConfig(
            name="13_Synergy_SweepOFF_Stretch085_Chop1_DynTP",
            use_sweep=False,
            max_h1_stretch=0.85,
            max_recent_flips=1,
            tp_model="dynamic_2h",
            blocked_hours=[]
        ),
        StrategyConfig(
            name="14_Synergy_SweepON_Stretch085_Chop1_DynTP",
            use_sweep=True,
            max_h1_stretch=0.85,
            max_recent_flips=1,
            tp_model="dynamic_2h",
            blocked_hours=[]
        ),
        StrategyConfig(
            name="15_Synergy_SweepOFF_Stretch100_Chop1_FixedTP",
            use_sweep=False,
            max_h1_stretch=1.00,
            max_recent_flips=1,
            tp_model="fixed",
            blocked_hours=[]
        )
    ]
    return configs


def run_full_grid():
    t_start = time.time()
    print("=" * 100)
    print("      DCC INSTITUTIONAL STRATEGY PARAMETER OPTIMIZATION GRID (2026)")
    print("      Testing Multi-Factor Dimensions across Gold (XAUUSD) & Nasdaq (NAS100)")
    print("=" * 100)

    # 1. Warm up in-memory dataset cache
    print("\n[Phase 1/3] Loading multi-timeframe parquet datasets into RAM...")
    BacktestDataset.get_data("XAUUSD")
    BacktestDataset.get_data("NAS100")

    configs = define_parameter_grid()
    print(f"\n[Phase 2/3] Executing {len(configs)} configurations on XAUUSD and NAS100...")

    results = []
    killzone_monthly_records = []

    for idx, cfg in enumerate(configs, 1):
        print(f"[{idx:02d}/{len(configs):02d}] Evaluating: {cfg.name}...")
        t0 = time.time()

        # Simulate both symbols
        df_xau = simulate_strategy("XAUUSD", cfg, allow_overnight=True)
        df_nas = simulate_strategy("NAS100", cfg, allow_overnight=True)

        # Merge for combined portfolio performance
        df_all = pd.concat([df_xau, df_nas]).sort_values('entry_dt').reset_index(drop=True)

        m_xau = calculate_comprehensive_metrics(df_xau)
        m_nas = calculate_comprehensive_metrics(df_nas)
        m_comb = calculate_comprehensive_metrics(df_all)

        res_row = {
            "config_id": idx,
            "name": cfg.name,
            "use_sweep": cfg.use_sweep,
            "max_h1_stretch": cfg.max_h1_stretch if cfg.max_h1_stretch else "None",
            "max_recent_flips": cfg.max_recent_flips if cfg.max_recent_flips else "None",
            "tp_model": cfg.tp_model,
            "blocked_hours": str(cfg.blocked_hours),
            # Combined Portfolio Metrics
            "comb_trades": m_comb["total_trades"],
            "comb_pnl": m_comb["net_pnl"],
            "comb_pnl_pct": m_comb["pnl_pct"],
            "comb_wr": m_comb["win_rate"],
            "comb_pf": m_comb["profit_factor"],
            "comb_max_dd_pct": m_comb["overall_max_dd_pct"],
            "comb_max_dd_dollars": m_comb["overall_max_dd_dollars"],
            "comb_daily_dd_pct": m_comb["worst_daily_dd_pct"],
            "comb_daily_dd_dollars": m_comb["worst_daily_dd_dollars"],
            # Individual Symbol Highlights
            "xau_trades": m_xau["total_trades"],
            "xau_pnl": m_xau["net_pnl"],
            "xau_wr": m_xau["win_rate"],
            "nas_trades": m_nas["total_trades"],
            "nas_pnl": m_nas["net_pnl"],
            "nas_wr": m_nas["win_rate"],
            # Sep 15 Forensic Outcome
            "sep15_trades": m_comb["sep15_trades"],
            "sep15_pnl": m_comb["sep15_pnl"]
        }
        results.append(res_row)

        # Capture killzone breakdown for continuous configurations
        if cfg.name in ["3_SweepON_Full_Session_No_KZ_Block", "4_SweepOFF_Full_Session_No_KZ_Block"]:
            for m, kz in m_comb["killzone_stats"].items():
                killzone_monthly_records.append({
                    "config": cfg.name,
                    "month": m,
                    "kz_trades": kz["kz_trades"],
                    "kz_pnl": kz["kz_pnl"],
                    "non_kz_pnl": kz["non_kz_pnl"],
                    "kz_verdict": "PROFITABLE (Keep)" if kz["kz_pnl"] > 0 else "DESTRUCTIVE (Block)"
                })

        print(f"       -> Done in {time.time()-t0:.2f}s | PnL: ${m_comb['net_pnl']:+,.2f} | WR: {m_comb['win_rate']:.1f}% | Max DD: {m_comb['overall_max_dd_pct']:.2f}% | Worst Daily DD: {m_comb['worst_daily_dd_pct']:.2f}% | Sep15: ${m_comb['sep15_pnl']:+,.2f}")

    # 3. Export to CSV & Generate Report
    print("\n[Phase 3/3] Generating optimization scorecards and analysis reports...")
    df_results = pd.DataFrame(results)
    csv_path = os.path.join(OUTPUT_DIR, "GRID_RESULTS.csv")
    df_results.to_csv(csv_path, index=False)
    print(f"Exported complete grid results to {csv_path}")

    # Export Killzone Month-by-Month breakdown
    df_kz = pd.DataFrame(killzone_monthly_records)
    kz_csv_path = os.path.join(OUTPUT_DIR, "KILLZONE_MONTHLY_AUDIT.csv")
    df_kz.to_csv(kz_csv_path, index=False)
    print(f"Exported monthly killzone audit to {kz_csv_path}")

    # Print Best Ranking Table
    print("\n" + "=" * 115)
    print("                      TOP 5 CONFIGURATIONS BY MAXIMUM NET PROFIT")
    print("=" * 115)
    top_pnl = df_results.sort_values('comb_pnl', ascending=False).head(5)
    print(top_pnl[['name', 'comb_trades', 'comb_pnl', 'comb_wr', 'comb_pf', 'comb_max_dd_pct', 'comb_daily_dd_pct', 'sep15_pnl']].to_string(index=False))

    print("\n" + "=" * 115)
    print("                    TOP 5 CONFIGURATIONS BY LOWEST MAX DRAWDOWN (PROP FIRM SAFE)")
    print("=" * 115)
    top_dd = df_results[df_results['comb_trades'] >= 50].sort_values('comb_max_dd_pct', ascending=True).head(5)
    print(top_dd[['name', 'comb_trades', 'comb_pnl', 'comb_wr', 'comb_pf', 'comb_max_dd_pct', 'comb_daily_dd_pct', 'sep15_pnl']].to_string(index=False))

    print(f"\nTotal Grid Execution completed in {time.time()-t_start:.2f} seconds.")


if __name__ == "__main__":
    run_full_grid()
