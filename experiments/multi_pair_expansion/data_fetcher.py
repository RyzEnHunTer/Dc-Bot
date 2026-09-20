"""
Multi-Pair Data Fetcher for MT5
Fetches M5 candles, H1 candles, and high-resolution tick data with real broker spread.
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Tuple

class MT5DataFetcher:
    def __init__(self):
        self.initialized = False
        self._ensure_connection()

    def _ensure_connection(self):
        if not mt5.initialize():
            raise RuntimeError(f"MT5 Initialization failed: {mt5.last_error()}")
        self.initialized = True

    def get_candle_data(self, symbol: str, n_m5_bars: int = 20000) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Fetches M5 and H1 candles for the given symbol.
        """
        self._ensure_connection()
        
        # Select symbol in Market Watch
        if not mt5.symbol_select(symbol, True):
            raise ValueError(f"Symbol {symbol} cannot be selected in MT5 Market Watch")

        # Fetch M5
        rates_m5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n_m5_bars)
        if rates_m5 is None or len(rates_m5) == 0:
            raise ValueError(f"No M5 data returned for {symbol}: {mt5.last_error()}")
        
        df_m5 = pd.DataFrame(rates_m5)
        df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s')
        df_m5.set_index('time', inplace=True)
        df_m5.sort_index(inplace=True)

        # Calculate how many H1 bars we need to cover the M5 range + 200 bars warmup
        start_time = df_m5.index[0] - timedelta(days=30)
        rates_h1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, start_time, datetime.now())
        if rates_h1 is None or len(rates_h1) == 0:
            rates_h1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 5000)
            
        df_h1 = pd.DataFrame(rates_h1)
        df_h1['time'] = pd.to_datetime(df_h1['time'], unit='s')
        df_h1.set_index('time', inplace=True)
        df_h1.sort_index(inplace=True)

        return df_m5, df_h1

    def get_tick_data_range(self, symbol: str, start_time: datetime, end_time: datetime) -> Optional[pd.DataFrame]:
        """
        Fetches exact real broker ticks for the given time window.
        Returns DataFrame with columns: ['time', 'bid', 'ask', 'spread', 'flags']
        """
        self._ensure_connection()
        ticks = mt5.copy_ticks_range(symbol, start_time, end_time, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            return None
        
        df_ticks = pd.DataFrame(ticks)
        df_ticks['time'] = pd.to_datetime(df_ticks['time_msc'], unit='ms')
        df_ticks['spread'] = df_ticks['ask'] - df_ticks['bid']
        return df_ticks[['time', 'bid', 'ask', 'spread', 'flags']]

    def get_symbol_specs(self, symbol: str) -> dict:
        self._ensure_connection()
        info = mt5.symbol_info(symbol)
        if not info:
            raise ValueError(f"Symbol {symbol} info not found")
        return {
            'symbol': symbol,
            'digits': info.digits,
            'point': info.point,
            'spread': info.spread,
            'volume_min': info.volume_min,
            'trade_tick_size': info.trade_tick_size,
            'trade_contract_size': info.trade_contract_size
        }

    def close(self):
        if self.initialized:
            mt5.shutdown()
            self.initialized = False
