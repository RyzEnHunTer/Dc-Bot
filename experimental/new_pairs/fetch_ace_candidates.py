import os
import sys
from datetime import datetime, timezone
import pandas as pd
import MetaTrader5 as mt5

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

CANDIDATES = ['JPN225', 'XAGUSD', 'EURJPY', 'AUDJPY', 'SPX500']

def fetch_data():
    if not mt5.initialize():
        print("MT5 initialization failed")
        return
        
    start_dt = datetime(2025, 12, 15, tzinfo=timezone.utc)
    end_dt = datetime(2026, 9, 9, tzinfo=timezone.utc)
    
    timeframes = {
        'm5': mt5.TIMEFRAME_M5,
        'h1': mt5.TIMEFRAME_H1,
        'h2': mt5.TIMEFRAME_H2
    }
    
    for sym in CANDIDATES:
        mt5.symbol_select(sym, True)
        for tf_name, tf_val in timeframes.items():
            cache_file = os.path.join(CACHE_DIR, f"{tf_name}_{sym}.parquet")
            if os.path.exists(cache_file):
                print(f"Already cached: {cache_file}")
                continue
                
            rates = mt5.copy_rates_range(sym, tf_val, start_dt, end_dt)
            if rates is None or len(rates) == 0:
                print(f"Failed to fetch {sym} {tf_name}")
                continue
                
            df = pd.DataFrame(rates)
            df.to_parquet(cache_file)
            print(f"Saved {sym} {tf_name}: {len(df)} bars -> {cache_file}")
            
    mt5.shutdown()
    print("Done fetching ace candidates.")

if __name__ == "__main__":
    fetch_data()
