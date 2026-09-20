import pandas as pd
import os

path = 'logs/vps logs/market_calculations_20260915.csv'
if not os.path.exists(path):
    path = 'logs/vps_calculations_20260915.csv'
df = pd.read_csv(path)
t1 = df[(df['symbol'] == 'XAUUSD') & (df['timestamp_utc'].str.contains('12:20|14:35'))]
for _, r in t1.iterrows():
    dist = abs(r['close'] - r['h1_ema20_level'])
    ratio = dist / r['atr_1h']
    print(f"Time: {r['timestamp_utc']} | Close: {r['close']} | 1H EMA20: {r['h1_ema20_level']} | Dist: {dist:.2f} | 1H ATR: {r['atr_1h']:.2f} | Stretch: {ratio:.2f}x | ADX: {r['adx_1h']:.2f}")

t2 = df[(df['symbol'] == 'NAS100') & (df['timestamp_utc'].str.contains('16:30|18:35'))]
for _, r in t2.iterrows():
    dist = abs(r['close'] - r['h1_ema20_level'])
    ratio = dist / r['atr_1h']
    print(f"NAS100 Time: {r['timestamp_utc']} | Close: {r['close']} | 1H EMA20: {r['h1_ema20_level']} | Dist: {dist:.2f} | 1H ATR: {r['atr_1h']:.2f} | Stretch: {ratio:.2f}x | ADX: {r['adx_1h']:.2f}")
