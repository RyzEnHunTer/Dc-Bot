import pandas as pd

df = pd.read_csv(r"d:\FOREX\DC\reports\trades_log_strict_6month.csv")
df['entry_time'] = pd.to_datetime(df['entry_time'], format='ISO8601')
df['hour'] = df['entry_time'].dt.hour
df['date'] = df['entry_time'].dt.date

# Baseline without 9 and 13 UTC
sub = df[~df['hour'].isin([9, 13])].copy()

print(f"Total trades: {len(sub)}")
daily_counts = sub.groupby('date').size()
print(f"Trades per day: min={daily_counts.min()}, median={daily_counts.median()}, max={daily_counts.max()}, mean={daily_counts.mean():.2f}")

# Daily losses per day
daily_losses = sub[sub['net_pnl'] <= 0].groupby('date').size()
print(f"Losses per day: max={daily_losses.max()}, median={daily_losses.median()}, mean={daily_losses.mean():.2f}")

# Let's test combinations of max trades per day and max losses per day
for max_loss in [2, 3, None]:
    for max_trades in [3, 4, 5, None]:
        kept = []
        for d, grp in sub.groupby('date'):
            grp = grp.sort_values('entry_time')
            losses = 0
            trades = 0
            for _, row in grp.iterrows():
                if max_trades and trades >= max_trades:
                    continue
                if max_loss and losses >= max_loss:
                    continue
                kept.append(row)
                trades += 1
                if row['net_pnl'] <= 0:
                    losses += 1
        
        res = pd.DataFrame(kept).sort_values('exit_time').reset_index(drop=True)
        wins = len(res[res['net_pnl'] > 0])
        tot = len(res)
        wr = wins / tot * 100
        pnl = res['net_pnl'].sum()
        gp = res[res['net_pnl'] > 0]['net_pnl'].sum()
        gl = abs(res[res['net_pnl'] <= 0]['net_pnl'].sum())
        pf = gp / gl if gl > 0 else 999
        eq = 5000.0 + res['net_pnl'].cumsum()
        peak = eq.cummax()
        dd = ((peak - eq) / peak * 100).max()
        print(f"Loss Limit: {str(max_loss):4s} | Trade Limit: {str(max_trades):4s} -> Trades: {tot:3d} | WR: {wr:.1f}% | PF: {pf:.2f} | Net: +${pnl:,.2f} (+{pnl/50:.1f}%) | DD: {dd:.2f}%")
