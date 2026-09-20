import os
import sys
import pandas as pd

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.strategy_optimizer.optimizer_engine import StrategyConfig
from experiments.strategy_optimizer.backtester import simulate_strategy, calculate_comprehensive_metrics

candidates = [
    StrategyConfig(
        name="Golden_A_SweepON_Stretch100_DynTP",
        use_sweep=True,
        max_h1_stretch=1.00,
        max_recent_flips=None,
        tp_model="dynamic_2h",
        blocked_hours=[]
    ),
    StrategyConfig(
        name="Golden_B_SweepON_Stretch100_FixedTP",
        use_sweep=True,
        max_h1_stretch=1.00,
        max_recent_flips=None,
        tp_model="fixed",
        blocked_hours=[]
    ),
    StrategyConfig(
        name="Golden_C_SweepON_Stretch125_DynTP",
        use_sweep=True,
        max_h1_stretch=1.25,
        max_recent_flips=None,
        tp_model="dynamic_2h",
        blocked_hours=[]
    )
]

for cfg in candidates:
    df_xau = simulate_strategy("XAUUSD", cfg, allow_overnight=True)
    df_nas = simulate_strategy("NAS100", cfg, allow_overnight=True)
    df_all = pd.concat([df_xau, df_nas]).sort_values('entry_dt').reset_index(drop=True)
    m = calculate_comprehensive_metrics(df_all)

    print(f"\n==================== {cfg.name} ====================")
    print(f"Total Trades: {m['total_trades']}")
    print(f"Net PnL: ${m['net_pnl']:+,.2f} ({m['pnl_pct']:+.1f}%)")
    print(f"Win Rate: {m['win_rate']:.1f}%")
    print(f"Profit Factor: {m['profit_factor']:.2f}")
    print(f"Overall Max DD: {m['overall_max_dd_pct']:.2f}% (${m['overall_max_dd_dollars']:,.2f})")
    print(f"Worst Daily DD: {m['worst_daily_dd_pct']:.2f}% (${m['worst_daily_dd_dollars']:,.2f})")
    print("Monthly Consistency:")
    for k, v in m['monthly_stats'].items():
        print(f"  {k} -> Trades: {v['trades']}, PnL: ${v['pnl']:+,.2f}, WR: {v['win_rate']:.1f}%")
