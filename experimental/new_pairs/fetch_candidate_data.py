"""
Data Fetcher for Experimental Candidate Pairs.
Downloads and caches M5, H1, and H2 rates from MT5 for 2026 into experimental/new_pairs/cache/.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
import MetaTrader5 as mt5
import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

CANDIDATES = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "GER40", "US30"]


def fetch_all_candidate_rates():
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")

    print("=" * 80)
    print("      DOWNLOADING HISTORICAL RATES FOR CANDIDATE CHOP-HEDGE PAIRS")
    print("=" * 80)

    start_date = datetime(2025, 12, 1, tzinfo=timezone.utc)
    end_date = datetime(2026, 9, 10, tzinfo=timezone.utc)

    for sym in CANDIDATES:
        print(f"\n>>> Fetching {sym} <<<")
        # Ensure symbol is selected in Market Watch
        mt5.symbol_select(sym, True)

        # 1. M5 Bars
        m5_rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, start_date, end_date)
        if m5_rates is None or len(m5_rates) == 0:
            print(f"  [WARN] Could not fetch M5 rates for {sym}")
            continue
        df_m5 = pd.DataFrame(m5_rates)
        m5_path = os.path.join(CACHE_DIR, f"m5_{sym}.parquet")
        df_m5.to_parquet(m5_path, index=False)
        print(f"  M5: {len(df_m5):,} bars -> {m5_path}")

        # 2. H1 Bars
        h1_rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, start_date, end_date)
        if h1_rates is not None and len(h1_rates) > 0:
            df_1h = pd.DataFrame(h1_rates)
            h1_path = os.path.join(CACHE_DIR, f"h1_{sym}.parquet")
            df_1h.to_parquet(h1_path, index=False)
            print(f"  H1: {len(df_1h):,} bars -> {h1_path}")

        # 3. H2 Bars
        h2_rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H2, start_date, end_date)
        if h2_rates is not None and len(h2_rates) > 0:
            df_2h = pd.DataFrame(h2_rates)
            h2_path = os.path.join(CACHE_DIR, f"h2_{sym}.parquet")
            df_2h.to_parquet(h2_path, index=False)
            print(f"  H2: {len(df_2h):,} bars -> {h2_path}")

    print("\n" + "=" * 80)
    print("           ALL CANDIDATE DATA CACHED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    fetch_all_candidate_rates()
