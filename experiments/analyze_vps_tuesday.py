import pandas as pd

df15 = pd.read_csv(r'd:\FOREX\DC\logs\vps logs\market_calculations_20260915.csv')
signals15 = df15[df15['decision'].str.startswith('FIRED')]

print("=================== 2026-09-15 SIGNALS ===================")
for idx, row in signals15.iterrows():
    print(f"Signal at index {idx}:")
    print(f"  Symbol: {row['symbol']}")
    print(f"  Decision: {row['decision']}")
    print(f"  UTC Time: {row['timestamp_utc']} | IST: {row['timestamp_ist']}")
    print(f"  Close / Planned Entry: {row['planned_entry']}")
    print(f"  Planned SL: {row['planned_sl']} (Dist: {abs(row['planned_entry'] - row['planned_sl']):.2f})")
    print(f"  Planned TP1: {row['planned_tp1']} (1:1.5R)")
    print(f"  Planned TP2: {row['planned_tp2']} (1:3.0R)")
    print(f"  Planned Lots: {row['planned_lots']}")
    print(f"  Reason: {row['reason']}\n")
