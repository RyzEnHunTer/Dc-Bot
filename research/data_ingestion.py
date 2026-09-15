"""
Data Ingestion Pipeline: Streams high-resolution tick CSVs from MT5 export
and partitions them into daily compressed Parquet files in data_cache/.
Optimized for memory efficiency and high-speed tick replay.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from typing import Optional

import pandas as pd
import numpy as np


def ingest_tick_csv(
    csv_path: str,
    symbol: str,
    output_dir: Optional[str] = None,
    start_date_str: str = "2026.01.01",
    end_date_str: str = "2026.06.30",
    chunksize: int = 250_000,
):
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_cache")
    print(f"\n=======================================================")
    print(f"Ingesting Tick Data: {symbol}")
    print(f"Source: {csv_path}")
    print(f"Target Range: {start_date_str} to {end_date_str}")
    print(f"Output Directory: {output_dir}")
    print(f"=======================================================")

    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Source CSV not found: {csv_path}")

    # Usecols needed for simulation: DATE, TIME, BID, ASK
    col_names = ['<DATE>', '<TIME>', '<BID>', '<ASK>']
    
    t0 = time.time()
    total_ticks_processed = 0
    total_ticks_saved = 0
    days_saved = set()

    current_day_date = None
    current_day_buffer = []
    last_known_bid = None
    last_known_ask = None

    def flush_day(date_val, buffer_list):
        if not buffer_list:
            return 0
        df_day = pd.concat(buffer_list, ignore_index=True)
        date_clean = date_val.replace('.', '')
        out_file = os.path.join(output_dir, f"ticks_{symbol}_{date_clean}.parquet")
        
        # Select and format final columns
        df_day = df_day[['time_dt', 'bid', 'ask']]
        df_day.to_parquet(out_file, compression='snappy', index=False)
        days_saved.add(date_val)
        return len(df_day)

    # Read in chunks
    reader = pd.read_csv(
        csv_path,
        sep='\t',
        usecols=col_names,
        chunksize=chunksize,
        dtype={'<DATE>': str, '<TIME>': str, '<BID>': float, '<ASK>': float},
        low_memory=False
    )

    for chunk_idx, chunk in enumerate(reader):
        total_ticks_processed += len(chunk)

        # Check start & end date of chunk
        min_date = chunk['<DATE>'].iloc[0]
        max_date = chunk['<DATE>'].iloc[-1]

        # If entire chunk is past end_date, stop streaming!
        if min_date > end_date_str:
            print(f"Reached date {min_date} > {end_date_str}. Stopping ingestion for {symbol}.")
            break

        # Filter chunk rows within target range
        mask = (chunk['<DATE>'] >= start_date_str) & (chunk['<DATE>'] <= end_date_str)
        chunk = chunk.loc[mask].copy()

        if len(chunk) == 0:
            continue

        # Forward fill bid & ask across chunk boundaries
        if last_known_bid is not None and pd.isna(chunk['<BID>'].iloc[0]):
            chunk['<BID>'].iloc[0] = last_known_bid
        if last_known_ask is not None and pd.isna(chunk['<ASK>'].iloc[0]):
            chunk['<ASK>'].iloc[0] = last_known_ask

        chunk['<BID>'] = chunk['<BID>'].ffill()
        chunk['<ASK>'] = chunk['<ASK>'].ffill()

        last_known_bid = chunk['<BID>'].iloc[-1]
        last_known_ask = chunk['<ASK>'].iloc[-1]

        # Build datetime
        chunk['time_dt'] = pd.to_datetime(
            chunk['<DATE>'] + ' ' + chunk['<TIME>'],
            format='%Y.%m.%d %H:%M:%S.%f',
            utc=True
        )
        chunk.rename(columns={'<BID>': 'bid', '<ASK>': 'ask'}, inplace=True)

        # Group by day
        for date_val, group in chunk.groupby('<DATE>'):
            if current_day_date is None:
                current_day_date = date_val
                current_day_buffer = [group]
            elif current_day_date == date_val:
                current_day_buffer.append(group)
            else:
                # Date changed! Flush previous day to parquet
                saved_count = flush_day(current_day_date, current_day_buffer)
                total_ticks_saved += saved_count
                if len(days_saved) % 10 == 0:
                    elapsed = time.time() - t0
                    print(f"[{symbol}] Saved {len(days_saved)} days ({total_ticks_saved:,} ticks) | Elapsed: {elapsed:.1f}s")
                
                current_day_date = date_val
                current_day_buffer = [group]

    # Flush remaining buffer
    if current_day_date is not None and current_day_buffer:
        saved_count = flush_day(current_day_date, current_day_buffer)
        total_ticks_saved += saved_count

    elapsed = time.time() - t0
    print(f"\n[SUCCESS] {symbol} Ingestion Completed!")
    print(f"Total Ticks Read:    {total_ticks_processed:,}")
    print(f"Total Ticks Cached:  {total_ticks_saved:,}")
    print(f"Trading Days Saved:  {len(days_saved)}")
    print(f"Total Time Taken:    {elapsed:.1f}s ({elapsed/60:.2f} mins)")


if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    xau_csv = os.path.join(base_dir, "Data", "XAUUSD_202601020100_202609032214.csv")
    nas_csv = os.path.join(base_dir, "Data", "NAS100_202601020100_202609032235.csv")

    # Ingest Nasdaq first (smaller, ~2.7 GB)
    if os.path.exists(nas_csv):
        ingest_tick_csv(nas_csv, "NAS100")
    
    # Ingest Gold (~7.5 GB)
    if os.path.exists(xau_csv):
        ingest_tick_csv(xau_csv, "XAUUSD")
