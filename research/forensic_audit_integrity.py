import pandas as pd

df = pd.read_csv(r"d:\FOREX\DC\reports\trades_log_with_3pct_circuit_breaker.csv")
df['entry_time'] = pd.to_datetime(df['entry_time'], format='ISO8601')
df['exit_time'] = pd.to_datetime(df['exit_time'], format='ISO8601')

print("=" * 70)
print("FORENSIC INTEGRITY AUDIT OF BACKTEST TRADES")
print("=" * 70)

# 1. Check for overlapping trades on the SAME symbol
for sym in ['XAUUSD', 'NAS100']:
    sym_df = df[df['symbol'] == sym].sort_values('entry_time').reset_index(drop=True)
    overlaps = []
    for i in range(len(sym_df) - 1):
        curr_exit = sym_df.loc[i, 'exit_time']
        next_entry = sym_df.loc[i + 1, 'entry_time']
        if next_entry < curr_exit:
            overlaps.append((sym_df.loc[i, 'trade_id'], sym_df.loc[i + 1, 'trade_id'], curr_exit, next_entry))
    
    print(f"[{sym}] Overlapping trades found: {len(overlaps)}")
    if len(overlaps) > 0:
        print(f"  First 3 overlaps: {overlaps[:3]}")

# 2. Check for concurrent portfolio trades (Max across BOTH symbols at any moment)
times = []
for _, row in df.iterrows():
    times.append((row['entry_time'], 1))
    times.append((row['exit_time'], -1))

times = sorted(times, key=lambda x: x[0])
concurrent = 0
max_concurrent = 0
for t, event in times:
    concurrent += event
    if concurrent > max_concurrent:
        max_concurrent = concurrent

print(f"\n[PORTFOLIO] Maximum concurrent positions active at any millisecond: {max_concurrent}")

# 3. Check Compounding Effect vs Flat (Non-Compounded) Return
flat_pnl = df['net_pnl'].sum()
flat_roi = (flat_pnl / 5000.0) * 100.0
compounded_pnl = df['compounded_net_pnl'].sum()
compounded_roi = (compounded_pnl / 5000.0) * 100.0

print(f"\n[RETURN DECOMPOSITION]")
print(f"  Flat / Linear Return (Fixed $50 risk per trade):  +${flat_pnl:,.2f} (+{flat_roi:.1f}%)")
print(f"  Compounded Return (1% of current equity per trade): +${compounded_pnl:,.2f} (+{compounded_roi:.1f}%)")
print(f"  Ratio (Compounding Multiplier):                     {compounded_roi / flat_roi:.2f}x")

# 4. Check Win Rate & Risk-to-Reward Realism
wins = df[df['compounded_net_pnl'] > 0]
losses = df[df['compounded_net_pnl'] <= 0]
print(f"\n[STATISTICAL METRICS]")
print(f"  Total Trades:        {len(df)}")
print(f"  Win Rate:            {len(wins) / len(df) * 100:.1f}%")
print(f"  Average Winner:      +${wins['net_pnl'].mean():.2f}")
print(f"  Average Loser:       -${abs(losses['net_pnl'].mean()):.2f}")
print(f"  Avg Win/Loss Ratio:  {wins['net_pnl'].mean() / abs(losses['net_pnl'].mean()):.2f}")

# 5. Check Trade Duration Realism
df['duration_min'] = (df['exit_time'] - df['entry_time']).dt.total_seconds() / 60.0
print(f"\n[EXECUTION REALISM]")
print(f"  Min Duration:        {df['duration_min'].min():.1f} min")
print(f"  Median Duration:     {df['duration_min'].median():.1f} min")
print(f"  Mean Duration:       {df['duration_min'].mean():.1f} min")
print(f"  Max Duration:        {df['duration_min'].max():.1f} min")
