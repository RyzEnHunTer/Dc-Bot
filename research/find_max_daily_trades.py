import pandas as pd

for fname, name in [
    (r"d:\FOREX\DC\reports\trades_log_strict_6month.csv", "6-Month Strict (All Hours)"),
    (r"d:\FOREX\DC\reports\trades_log_optimized_low_dd.csv", "6-Month Optimized (Skip 9, 13 UTC)")
]:
    df = pd.read_csv(fname)
    df['entry_time'] = pd.to_datetime(df['entry_time'], format='ISO8601')
    df['date'] = df['entry_time'].dt.date
    
    print("=" * 70)
    print(f"ANALYSIS FOR: {name}")
    print("=" * 70)
    
    daily_counts = df.groupby('date').size()
    max_count = daily_counts.max()
    max_dates = daily_counts[daily_counts == max_count].index.tolist()
    
    print(f"MAX TRADES IN A SINGLE DAY: {max_count} trades")
    print(f"Date(s) with max trades: {max_dates}\n")
    
    # Detailed breakdown of top days
    top_dates = daily_counts.sort_values(ascending=False).head(5)
    print("Top 5 Busiest Days:")
    for d, count in top_dates.items():
        day_trades = df[df['date'] == d]
        gold_c = len(day_trades[day_trades['symbol'] == 'XAUUSD'])
        nas_c = len(day_trades[day_trades['symbol'] == 'NAS100'])
        wins = len(day_trades[day_trades['net_pnl'] > 0])
        losses = len(day_trades[day_trades['net_pnl'] <= 0])
        day_pnl = day_trades['net_pnl'].sum()
        print(f"  Date: {d} | Total: {count:2d} trades (Gold: {gold_c}, Nasdaq: {nas_c}) | Wins: {wins}, Losses: {losses} | PnL: +${day_pnl:,.2f}" if day_pnl >= 0 else f"  Date: {d} | Total: {count:2d} trades (Gold: {gold_c}, Nasdaq: {nas_c}) | Wins: {wins}, Losses: {losses} | PnL: -${abs(day_pnl):,.2f}")
    
    print("\nDaily Trades Distribution across all 122 trading days:")
    dist = daily_counts.value_counts().sort_index()
    for n_trades, days in dist.items():
        print(f"  {n_trades} trade(s) in a day: {days:2d} days ({days / len(daily_counts) * 100:.1f}%)")
    print()
