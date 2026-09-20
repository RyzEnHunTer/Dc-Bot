"""
Syncs latest M5, H1, and H2 market rates from MetaTrader 5 into data_cache/
up to September 20, 2026 (closing the full trading week through Day 20).
"""

import os
import sys
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(PROJECT_ROOT, "data_cache")

def sync_rates():
    if not mt5.initialize():
        print(f"[ERROR] MT5 init failed: {mt5.last_error()}")
        sys.exit(1)

    symbols = ["XAUUSD", "NAS100"]
    timeframes = {
        "m5": mt5.TIMEFRAME_M5,
        "h1": mt5.TIMEFRAME_H1,
        "h2": mt5.TIMEFRAME_H2,
    }

    start_dt = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)

    for sym in symbols:
        mt5.symbol_select(sym, True)
        for tf_name, tf_const in timeframes.items():
            pq_path = os.path.join(CACHE_DIR, f"{tf_name}_bars_{sym}.parquet")
            print(f"Syncing {sym} {tf_name.upper()}...")

            rates = mt5.copy_rates_range(sym, tf_const, start_dt, end_dt)
            if rates is None or len(rates) == 0:
                print(f"[WARN] No rates returned for {sym} {tf_name}")
                continue

            df_new = pd.DataFrame(rates)
            
            if os.path.exists(pq_path):
                df_existing = pd.read_parquet(pq_path)
                df_combined = pd.concat([df_existing, df_new], ignore_index=True)
                df_combined.drop_duplicates(subset=['time'], keep='last', inplace=True)
                df_combined.sort_values('time', inplace=True)
                df_combined.reset_index(drop=True, inplace=True)
            else:
                df_combined = df_new

            df_combined.to_parquet(pq_path, index=False)
            t_min = pd.to_datetime(df_combined['time'].iloc[0], unit='s', utc=True)
            t_max = pd.to_datetime(df_combined['time'].iloc[-1], unit='s', utc=True)
            print(f"  -> Saved {len(df_combined)} bars to {pq_path}")
            print(f"  -> Time span: {t_min} to {t_max}")

    print("\n[SUCCESS] All parquet data caches synced through Day 20 (September 20, 2026)!")

if __name__ == '__main__':
    sync_rates()
