"""
MT5 Data Provider & Cacher
Downloads historical M5, H1, H2 bars and Tick data from MetaTrader 5,
with local caching for high-speed tick-level backtesting.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
import MetaTrader5 as mt5
import pandas as pd
import numpy as np


class MT5DataProvider:
    def __init__(self, cache_dir: str = "d:\\FOREX\\DC\\data_cache"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self._connected = False

    def connect(self) -> bool:
        if not self._connected:
            if not mt5.initialize():
                print(f"[MT5DataProvider] Failed to initialize MT5: {mt5.last_error()}")
                return False
            self._connected = True
        return True

    def disconnect(self):
        if self._connected:
            mt5.shutdown()
            self._connected = False

    def get_symbol_info(self, symbol: str = "XAUUSD") -> dict:
        self.connect()
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        if not info:
            raise ValueError(f"Could not retrieve symbol info for {symbol}")
        return {
            "name": info.name,
            "point": info.point,
            "digits": info.digits,
            "spread": info.spread,
            "trade_contract_size": info.trade_contract_size,
            "volume_min": info.volume_min,
            "volume_step": info.volume_step,
            "volume_max": info.volume_max,
        }

    def fetch_rates(
        self,
        symbol: str,
        timeframe: int,
        start_dt: datetime,
        end_dt: datetime
    ) -> pd.DataFrame:
        """
        Fetches OHLCV rates from MT5.
        Timeframe constants: mt5.TIMEFRAME_M5, mt5.TIMEFRAME_H1, mt5.TIMEFRAME_H2
        """
        self.connect()
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_range(symbol, timeframe, start_dt, end_dt)
        if rates is None or len(rates) == 0:
            raise ValueError(f"No rates returned for {symbol} tf={timeframe} from {start_dt} to {end_dt}")

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df.set_index('time', inplace=True)
        df.rename(columns={'tick_volume': 'volume'}, inplace=True)
        return df[['open', 'high', 'low', 'close', 'volume']]

    def fetch_multi_timeframe_rates(
        self,
        symbol: str,
        start_dt: datetime,
        end_dt: datetime,
        warmup_days: int = 20
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Fetches M5, H1, and H2 rates including warmup period.
        """
        warmup_start = start_dt - timedelta(days=warmup_days)
        print(f"[MT5DataProvider] Fetching M5, H1, H2 bars from {warmup_start.date()} to {end_dt.date()}...")

        df_m5 = self.fetch_rates(symbol, mt5.TIMEFRAME_M5, warmup_start, end_dt)
        df_1h = self.fetch_rates(symbol, mt5.TIMEFRAME_H1, warmup_start, end_dt)
        df_2h = self.fetch_rates(symbol, mt5.TIMEFRAME_H2, warmup_start, end_dt)

        print(f"[MT5DataProvider] Loaded {len(df_m5)} M5 bars, {len(df_1h)} H1 bars, {len(df_2h)} H2 bars.")
        return df_m5, df_1h, df_2h

    def fetch_ticks_day(
        self,
        symbol: str,
        target_date: datetime.date
    ) -> pd.DataFrame:
        """
        Fetches ticks for a single calendar day with disk caching.
        """
        cache_file = os.path.join(self.cache_dir, f"ticks_{symbol}_{target_date.strftime('%Y%m%d')}.parquet")
        if os.path.exists(cache_file):
            try:
                df = pd.read_parquet(cache_file)
                return df
            except Exception:
                pass

        self.connect()
        mt5.symbol_select(symbol, True)
        d_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
        d_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59)

        ticks = mt5.copy_ticks_range(symbol, d_start, d_end, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(ticks)
        df['time_dt'] = pd.to_datetime(df['time_msc'], unit='ms', utc=True)
        # Select key columns
        df = df[['time_dt', 'bid', 'ask', 'last', 'volume', 'flags']]
        # Save to parquet for subsequent fast access
        try:
            df.to_parquet(cache_file, compression='snappy')
        except Exception as e:
            print(f"[MT5DataProvider] Cache write failed: {e}")

        return df
