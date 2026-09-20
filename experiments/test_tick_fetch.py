import MetaTrader5 as mt5
from datetime import datetime, timedelta
import pandas as pd

if not mt5.initialize():
    print("MT5 Init Failed")
    exit(1)

symbol = "XAUUSD"
end_time = datetime.now()
start_time = end_time - timedelta(days=5)

print(f"Fetching ticks for {symbol} from {start_time.strftime('%Y-%m-%d')} to {end_time.strftime('%Y-%m-%d')}...")
ticks = mt5.copy_ticks_range(symbol, start_time, end_time, mt5.COPY_TICKS_ALL)

if ticks is None or len(ticks) == 0:
    print(f"No ticks returned for {symbol}! Last error:", mt5.last_error())
else:
    df_ticks = pd.DataFrame(ticks)
    df_ticks['time'] = pd.to_datetime(df_ticks['time_msc'], unit='ms')
    df_ticks['spread'] = (df_ticks['ask'] - df_ticks['bid']).round(3)
    
    print(f"Successfully fetched {len(df_ticks):,} ticks for {symbol}!")
    print(f"Date range: {df_ticks['time'].min()} -> {df_ticks['time'].max()}")
    print(f"Average spread: {df_ticks['spread'].mean():.3f} (Min: {df_ticks['spread'].min():.3f}, Max: {df_ticks['spread'].max():.3f})")
    print("\nSample ticks:")
    print(df_ticks[['time', 'bid', 'ask', 'spread', 'flags']].head())

mt5.shutdown()
