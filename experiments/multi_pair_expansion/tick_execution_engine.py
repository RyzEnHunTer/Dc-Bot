"""
Tick-Level Execution Simulator
Simulates real broker execution, bid/ask spread costs, and exact tick-level SL/TP fills.
Falls back to M1 intrabar evaluation if historical ticks exceed broker tick cache.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

class TickExecutionSimulator:
    def __init__(self, data_fetcher):
        self.data_fetcher = data_fetcher

    def simulate_trade_with_ticks(self, symbol: str, signal: Dict[str, Any], max_hold_hours: int = 48) -> Dict[str, Any]:
        """
        Executes a trade signal against real broker ticks.
        """
        entry_time = signal['time']
        direction = signal['direction']
        sl = signal['stop_loss']
        tp = signal['take_profit']
        sl_dist = signal['sl_dist']
        
        end_window = entry_time + timedelta(hours=max_hold_hours)
        
        # Try fetching real ticks first
        ticks_df = self.data_fetcher.get_tick_data_range(symbol, entry_time, end_window)
        
        if ticks_df is not None and len(ticks_df) > 10:
            return self._simulate_on_ticks(ticks_df, signal)
        else:
            # Fallback to high-precision M1 intrabar simulation
            return self._simulate_on_m1(symbol, signal, end_window)

    def _simulate_on_ticks(self, ticks: pd.DataFrame, signal: Dict[str, Any]) -> Dict[str, Any]:
        direction = signal['direction']
        sl = signal['stop_loss']
        tp = signal['take_profit']
        sl_dist = signal['sl_dist']
        
        first_tick = ticks.iloc[0]
        entry_time = first_tick['time']
        
        # Real fill with broker spread
        if direction == 'BUY':
            fill_price = first_tick['ask']
            spread_paid = first_tick['ask'] - first_tick['bid']
        else:
            fill_price = first_tick['bid']
            spread_paid = first_tick['ask'] - first_tick['bid']
            
        exit_time = None
        exit_price = None
        outcome = 'TIMEOUT'
        r_multiple = 0.0
        
        # Tick evaluation loop
        bids = ticks['bid'].values
        asks = ticks['ask'].values
        times = ticks['time'].values
        
        if direction == 'BUY':
            for t_time, bid, ask in zip(times, bids, asks):
                # Check SL first for conservative risk evaluation
                if bid <= sl:
                    exit_time = t_time
                    exit_price = sl
                    outcome = 'LOSS'
                    r_multiple = -1.0
                    break
                elif bid >= tp:
                    exit_time = t_time
                    exit_price = tp
                    outcome = 'WIN'
                    r_multiple = 2.0
                    break
        else: # SELL
            for t_time, bid, ask in zip(times, bids, asks):
                if ask >= sl:
                    exit_time = t_time
                    exit_price = sl
                    outcome = 'LOSS'
                    r_multiple = -1.0
                    break
                elif ask <= tp:
                    exit_time = t_time
                    exit_price = tp
                    outcome = 'WIN'
                    r_multiple = 2.0
                    break
                    
        if outcome == 'TIMEOUT':
            last_tick = ticks.iloc[-1]
            exit_time = last_tick['time']
            exit_price = last_tick['bid'] if direction == 'BUY' else last_tick['ask']
            if direction == 'BUY':
                r_multiple = (exit_price - fill_price) / sl_dist
            else:
                r_multiple = (fill_price - exit_price) / sl_dist
                
        duration_minutes = (pd.to_datetime(exit_time) - pd.to_datetime(entry_time)).total_seconds() / 60.0
        
        return {
            'symbol': signal.get('symbol', ''),
            'entry_time': pd.to_datetime(entry_time),
            'direction': direction,
            'fill_price': fill_price,
            'sl': sl,
            'tp': tp,
            'sl_dist': sl_dist,
            'spread_paid': spread_paid,
            'exit_time': pd.to_datetime(exit_time),
            'exit_price': exit_price,
            'outcome': outcome,
            'r_multiple': round(r_multiple, 2),
            'duration_min': round(duration_minutes, 1),
            'source': 'TICK_FEED'
        }

    def _simulate_on_m1(self, symbol: str, signal: Dict[str, Any], end_window: datetime) -> Dict[str, Any]:
        """
        Fallback when ticks are outside broker cache: uses M1 candles with spread modeling.
        """
        import MetaTrader5 as mt5
        rates_m1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, signal['time'], end_window)
        if rates_m1 is None or len(rates_m1) == 0:
            return {
                'symbol': symbol,
                'entry_time': signal['time'],
                'direction': signal['direction'],
                'fill_price': signal['entry_price'],
                'outcome': 'ERROR_NO_DATA',
                'r_multiple': 0.0,
                'source': 'FAILED'
            }
            
        df_m1 = pd.DataFrame(rates_m1)
        df_m1['time'] = pd.to_datetime(df_m1['time'], unit='s')
        
        # Estimate spread from symbol specs
        specs = self.data_fetcher.get_symbol_specs(symbol)
        spread_pts = specs.get('spread', 10) * specs.get('point', 0.01)
        
        direction = signal['direction']
        sl = signal['stop_loss']
        tp = signal['take_profit']
        sl_dist = signal['sl_dist']
        
        entry_row = df_m1.iloc[0]
        fill_price = entry_row['open'] + (spread_pts if direction == 'BUY' else 0.0)
        
        outcome = 'TIMEOUT'
        exit_time = df_m1.iloc[-1]['time']
        exit_price = df_m1.iloc[-1]['close']
        r_multiple = 0.0
        
        for idx, row in df_m1.iterrows():
            high = row['high']
            low = row['low']
            
            if direction == 'BUY':
                # Bid high = high, Bid low = low
                if low <= sl:
                    outcome = 'LOSS'
                    exit_time = row['time']
                    exit_price = sl
                    r_multiple = -1.0
                    break
                elif high >= tp:
                    outcome = 'WIN'
                    exit_time = row['time']
                    exit_price = tp
                    r_multiple = 2.0
                    break
            else: # SELL
                # Ask high = high + spread, Ask low = low + spread
                ask_high = high + spread_pts
                ask_low = low + spread_pts
                if ask_high >= sl:
                    outcome = 'LOSS'
                    exit_time = row['time']
                    exit_price = sl
                    r_multiple = -1.0
                    break
                elif ask_low <= tp:
                    outcome = 'WIN'
                    exit_time = row['time']
                    exit_price = tp
                    r_multiple = 2.0
                    break
                    
        duration_minutes = (pd.to_datetime(exit_time) - pd.to_datetime(signal['time'])).total_seconds() / 60.0
        return {
            'symbol': symbol,
            'entry_time': signal['time'],
            'direction': direction,
            'fill_price': fill_price,
            'sl': sl,
            'tp': tp,
            'sl_dist': sl_dist,
            'spread_paid': spread_pts,
            'exit_time': pd.to_datetime(exit_time),
            'exit_price': exit_price,
            'outcome': outcome,
            'r_multiple': round(r_multiple, 2),
            'duration_min': round(duration_minutes, 1),
            'source': 'M1_SPREAD_SIM'
        }
