import os
import glob
import pandas as pd

reports_dir = os.path.join(os.path.dirname(__file__), "reports")
files = sorted(glob.glob(os.path.join(reports_dir, "trades_*.csv")))

print(f"{'Symbol':<8} | {'Trades':<6} | {'Net PnL':<10} | {'Win Rate':<8} | {'PF':<5} | {'Feb':<9} | {'Jul':<9} | {'Aug':<9} | {'Chop(F+J+A)':<12}")
print("-" * 88)

for f in files:
    sym = os.path.basename(f).replace("trades_", "").replace(".csv", "")
    df = pd.read_csv(f)
    df['dt'] = pd.to_datetime(df['entry_time'])
    df['m'] = df['dt'].dt.strftime('%Y-%m')
    tot = df['net_pnl'].sum()
    n = len(df)
    wr = (df['net_pnl'] > 0).mean() * 100
    wins = df[df['net_pnl'] > 0]['net_pnl'].sum()
    losses = abs(df[df['net_pnl'] < 0]['net_pnl'].sum())
    pf = wins / losses if losses > 0 else 999.0
    feb = df[df['m'] == '2026-02']['net_pnl'].sum() if '2026-02' in df['m'].values else 0.0
    jul = df[df['m'] == '2026-07']['net_pnl'].sum() if '2026-07' in df['m'].values else 0.0
    aug = df[df['m'] == '2026-08']['net_pnl'].sum() if '2026-08' in df['m'].values else 0.0
    chop = feb + jul + aug
    print(f"{sym:<8} | {n:6d} | ${tot:+9.2f} | {wr:7.1f}% | {pf:5.2f} | ${feb:+8.2f} | ${jul:+8.2f} | ${aug:+8.2f} | ${chop:+10.2f}")
