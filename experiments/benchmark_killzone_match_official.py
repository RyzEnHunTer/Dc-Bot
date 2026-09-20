import os
import sys
import pandas as pd
import numpy as np

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.strategy_optimizer.test_dynamic_distance_methods import load_annotated_official_trades, simulate_prop_firm_exact
from experiments.strategy_optimizer.backtester import BacktestDataset

# 1. Load official 471 trades (which has hours 9 & 13 paused)
df_official = load_annotated_official_trades()

# 2. Load the 42 KZ trades from Phase 1 (strict 6-month)
df_strict = pd.read_csv(os.path.join(PROJECT_ROOT, 'reports', 'archive_intermediate', 'trades_log_strict_6month.csv'))
df_strict['entry_dt'] = pd.to_datetime(df_strict['entry_time'], format='mixed', utc=True)
df_strict['exit_dt'] = pd.to_datetime(df_strict['exit_time'], format='mixed', utc=True)

eval_xau, _ = BacktestDataset.get_data('XAUUSD')
eval_nas, _ = BacktestDataset.get_data('NAS100')

kz_trades_p1 = []
for idx, row in df_strict.iterrows():
    h = row['entry_dt'].hour
    if h not in [9, 13]:
        continue
    sym = row['symbol']
    eval_bars = eval_xau if sym == 'XAUUSD' else eval_nas
    t = row['entry_dt']
    locs = eval_bars.index.get_indexer([t], method='pad')
    if locs[0] == -1:
        continue
    bar = eval_bars.iloc[locs[0]]
    close = float(bar['close'])
    ema9_5m = float(bar['ema9_5m'])
    atr_1h = float(bar['atr_1h'])
    adx_1h = float(bar.get('adx_1h', 0.0))
    stretch_ratio = float(bar['stretch_ratio'])
    dist_5m_e9 = abs(close - ema9_5m)
    chase_ratio = dist_5m_e9 / atr_1h if atr_1h > 0 else 0.0
    r_dict = row.to_dict()
    r_dict.update({
        'stretch_ratio': stretch_ratio,
        'adx_1h': adx_1h,
        'chase_ratio': chase_ratio
    })
    kz_trades_p1.append(r_dict)

df_kz_p1 = pd.DataFrame(kz_trades_p1)
print(f"Loaded and annotated {len(df_kz_p1)} Phase 1 Killzone trades (09:00 & 13:00 UTC).")

# 3. Load Phase 2 unpaused Killzone trades
from scratch.test_killzone_unpaused_phase2 import symbol_data, eval_times, run_sim
df_p2_unp = run_sim(allow_killzone=True)
df_p2_dt = pd.to_datetime(df_p2_unp['entry_time'])
df_p2_kz = df_p2_unp[df_p2_dt.dt.hour.isin([9, 13])].copy()
df_p2_kz['entry_dt'] = pd.to_datetime(df_p2_kz['entry_time'], utc=True)
df_p2_kz['exit_dt'] = pd.to_datetime(df_p2_kz['exit_time'], utc=True)

# Annotate Phase 2 KZ trades
kz_trades_p2 = []
for idx, row in df_p2_kz.iterrows():
    sym = row['symbol']
    eval_bars = eval_xau if sym == 'XAUUSD' else eval_nas
    t = row['entry_dt']
    locs = eval_bars.index.get_indexer([t], method='pad')
    if locs[0] == -1:
        continue
    bar = eval_bars.iloc[locs[0]]
    close = float(bar['close'])
    ema9_5m = float(bar['ema9_5m'])
    atr_1h = float(bar['atr_1h'])
    adx_1h = float(bar.get('adx_1h', 0.0))
    stretch_ratio = float(bar['stretch_ratio'])
    dist_5m_e9 = abs(close - ema9_5m)
    chase_ratio = dist_5m_e9 / atr_1h if atr_1h > 0 else 0.0
    r_dict = row.to_dict()
    r_dict.update({
        'stretch_ratio': stretch_ratio,
        'adx_1h': adx_1h,
        'chase_ratio': chase_ratio
    })
    kz_trades_p2.append(r_dict)

df_kz_p2 = pd.DataFrame(kz_trades_p2)
print(f"Loaded and annotated {len(df_kz_p2)} Phase 2 Killzone trades (09:00 & 13:00 UTC).")

# Combine all KZ trades
all_kz_trades = pd.concat([df_kz_p1, df_kz_p2], ignore_index=True)
print(f"Total Full-Year Killzone trades (09:00 & 13:00 UTC): {len(all_kz_trades)}")

# Check TripleGuard filter on Killzone trades
mask_kz_tg = (
    (all_kz_trades['stretch_ratio'] >= 0.40) &
    (all_kz_trades['adx_1h'] <= 45.0) &
    (all_kz_trades['chase_ratio'] <= 0.50)
)
kz_survivors = all_kz_trades[mask_kz_tg]
kz_blocked = all_kz_trades[~mask_kz_tg]
print(f"  * Blocked by TripleGuard: {len(kz_blocked)} | PnL: ${kz_blocked['net_pnl'].sum():.2f}")
print(f"  * Passing TripleGuard:    {len(kz_survivors)} | PnL: ${kz_survivors['net_pnl'].sum():.2f}")

# Build Full-Year Unpaused Dataset
df_unpaused_all = pd.concat([df_official, all_kz_trades], ignore_index=True)
df_unpaused_all.sort_values('entry_dt', inplace=True)
df_unpaused_all.reset_index(drop=True, inplace=True)
df_unpaused_all['weekday'] = df_unpaused_all['entry_dt'].dt.day_name()
df_unpaused_all['hour'] = df_unpaused_all['entry_dt'].dt.hour

# TripleGuard mask on unpaused dataset
mask_tg = (
    (df_unpaused_all['stretch_ratio'] >= 0.40) &
    (df_unpaused_all['adx_1h'] <= 45.0) &
    (df_unpaused_all['chase_ratio'] <= 0.50)
)
df_unp_tg = df_unpaused_all[mask_tg].copy()
is_mon_pm = (df_unp_tg['weekday'] == 'Monday') & (df_unp_tg['hour'].isin([14, 15, 17, 18]))
df_unp_opt2 = df_unp_tg[~is_mon_pm].copy()

# Canonical Option 2 from df_official (Benchmark reference)
df_official['weekday'] = df_official['entry_dt'].dt.day_name()
df_official['hour'] = df_official['entry_dt'].dt.hour
mask_tg_off = (
    (df_official['stretch_ratio'] >= 0.40) &
    (df_official['adx_1h'] <= 45.0) &
    (df_official['chase_ratio'] <= 0.50)
)
df_off_tg = df_official[mask_tg_off].copy()
is_mon_off = (df_off_tg['weekday'] == 'Monday') & (df_off_tg['hour'].isin([14, 15, 17, 18]))
df_can_opt2 = df_off_tg[~is_mon_off].copy()

# Run Prop Firm Simulation on exact benchmark engine
s_can = simulate_prop_firm_exact(df_can_opt2, 'Canonical Option 2 (Benchmark Ref: 9 & 13 Paused)')
s_unp = simulate_prop_firm_exact(df_unp_opt2, 'Unpaused Killzone v1.2 (Hours 9 & 13 ENABLED)')

results = [s_can, s_unp]
report_df = pd.DataFrame([{
    'Policy / Strategy Version': s['Candidate'],
    'Trades': s['Total Trades'],
    'Win Rate': s['Win Rate'],
    'Total Net PnL': s['Total Net PnL'],
    'Banked Cash (80%)': s['Banked Cash (80%)'],
    'Max Base DD': s['Max Base DD'],
    'Worst Single Day': s['Worst Single Day'],
    'Evaluation Pass': s['Challenge Pass'],
    'Prop Firm Status': s['Status']
} for s in results])

print("\n" + "=" * 115)
print("                OFFICIAL BENCHMARK COMPARISON: CANONICAL vs UNPAUSED KILLZONE")
print("=" * 115)
print(report_df.to_string(index=False))
print("=" * 115)
