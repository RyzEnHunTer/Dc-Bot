import os
import pandas as pd

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
df = pd.read_csv(os.path.join(base_dir, 'reports', 'forex_tuning_grid_results.csv'))

pairs = ['EURUSD', 'GBPUSD', 'USDJPY', 'GBPJPY', 'AUDUSD']

print("=" * 100)
print("             DEEP DIVE: TOP PROFITABLE PARAMETER CONFIGURATIONS FOR FOREX")
print("=" * 100)

for sym in pairs:
    sub = df[(df['symbol'] == sym) & (df['total_trades'] >= 10)]
    sub_prof = sub.sort_values(by='net_pnl', ascending=False)
    print(f"\n--- {sym} (Top 3 Parameter Sets by Net Profit) ---")
    if sub_prof.empty:
        print("  No sets with >= 10 trades")
        continue
    for idx, r in sub_prof.head(3).iterrows():
        pnl = r['net_pnl']
        wr = r['win_rate']
        pf = r['profit_factor']
        dd = r['max_dd']
        trades = int(r['total_trades'])
        atr = r['atr_mult']
        sl = r['min_sl']
        tp1 = r['tp1']
        tp2 = r['tp2']
        adx = int(r['adx'])
        sess = r['session']
        room = r['room2h']
        print(f"  PnL: ${pnl:+9.2f} | WR: {wr:4.1f}% | PF: {pf:4.2f} | MaxDD: {dd:4.2f}% | Trades: {trades:2d} | ATR: {atr}x | MinSL: {sl:4.1f}p | TP: {tp1}R/{tp2}R | ADX>={adx} | Sess: {sess:4s} | Room: {room}")

print("=" * 100)
