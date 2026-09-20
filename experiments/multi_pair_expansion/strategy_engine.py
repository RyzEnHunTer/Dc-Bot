"""
DCC Golden Strategy Engine for Multi-Pair Backtesting
Calculates 1H trend alignment, 1H ADX, Session VWAP, 2H room filter, and 5M flip entry triggers.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Any

class DCCStrategyEngine:
    def __init__(self, adx_threshold: float = 20.0, atr_multiplier: float = 0.9, rr_ratio: float = 2.0):
        self.adx_threshold = adx_threshold
        self.atr_multiplier = atr_multiplier
        self.rr_ratio = rr_ratio

    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df['high']
        low = df['low']
        close_prev = df['close'].shift(1)
        
        tr1 = high - low
        tr2 = (high - close_prev).abs()
        tr3 = (low - close_prev).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    @staticmethod
    def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df['high']
        low = df['low']
        close = df['close']
        
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        atr = tr.rolling(period).mean()
        plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(period).mean() / (atr + 1e-9))
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(period).mean() / (atr + 1e-9))
        
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
        adx = dx.rolling(period).mean()
        return adx

    @staticmethod
    def calculate_session_vwap(df: pd.DataFrame) -> pd.Series:
        """
        Calculates daily Session VWAP resetting at 00:00 UTC daily.
        """
        typical_price = (df['high'] + df['low'] + df['close']) / 3.0
        volume = df['tick_volume'] if 'tick_volume' in df.columns else pd.Series(1, index=df.index)
        
        dates = df.index.date
        vwap = pd.Series(index=df.index, dtype=float)
        
        for d in np.unique(dates):
            mask = dates == d
            tp_d = typical_price[mask]
            vol_d = volume[mask]
            cum_vol = vol_d.cumsum()
            cum_tp_vol = (tp_d * vol_d).cumsum()
            vwap[mask] = cum_tp_vol / (cum_vol + 1e-9)
            
        return vwap

    def prepare_data(self, df_m5: pd.DataFrame, df_h1: pd.DataFrame) -> pd.DataFrame:
        """
        Merges 1H indicators into M5 data without lookahead bias.
        """
        # H1 Indicators
        h1 = df_h1.copy()
        h1['h1_ema9'] = self.calculate_ema(h1['close'], 9)
        h1['h1_ema20'] = self.calculate_ema(h1['close'], 20)
        h1['h1_atr'] = self.calculate_atr(h1, 14)
        h1['h1_adx'] = self.calculate_adx(h1, 14)
        
        # Shift H1 indicators by 1 to guarantee zero lookahead bias
        h1_shifted = h1[['h1_ema9', 'h1_ema20', 'h1_atr', 'h1_adx']].shift(1)
        
        # Merge into M5 using asof/forward-fill
        m5 = df_m5.copy()
        m5 = pd.merge_asof(m5, h1_shifted, left_index=True, right_index=True, direction='backward')
        
        # M5 Indicators
        m5['m5_ema9'] = self.calculate_ema(m5['close'], 9)
        m5['m5_ema20'] = self.calculate_ema(m5['close'], 20)
        m5['session_vwap'] = self.calculate_session_vwap(m5)
        
        # 2H Room Filter (24 bars of 5M = 2 hours)
        m5['swing_high_2h'] = m5['high'].rolling(24).max().shift(1)
        m5['swing_low_2h'] = m5['low'].rolling(24).min().shift(1)
        
        return m5

    def scan_signals(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Scans for DCC bar-close 5M flip signals that align with 1H trend and filters.
        """
        signals = []
        
        for i in range(1, len(df)):
            curr = df.iloc[i]
            prev = df.iloc[i - 1]
            time = df.index[i]
            
            # Require warm-up
            if pd.isna(curr['h1_ema9']) or pd.isna(curr['h1_atr']) or pd.isna(curr['h1_adx']):
                continue
            
            h1_atr = curr['h1_atr']
            sl_dist = h1_atr * self.atr_multiplier
            if sl_dist <= 0:
                continue

            # 1H ADX Filter
            if curr['h1_adx'] < self.adx_threshold:
                continue
                
            close_price = curr['close']
            vwap = curr['session_vwap']
            
            # Long Criteria:
            # 1. 1H Trend: 9 EMA > 20 EMA
            # 2. 5M Flip: previous close <= 20 EMA, current close > 9 EMA and close > 20 EMA
            # 3. Session VWAP: close > VWAP
            if curr['h1_ema9'] > curr['h1_ema20']:
                is_long_flip = (prev['close'] <= prev['m5_ema20']) and (curr['close'] > curr['m5_ema9']) and (curr['close'] > curr['m5_ema20'])
                if is_long_flip and (close_price > vwap):
                    # 2H Room filter: check room to swing high 2h
                    swing_high = curr['swing_high_2h']
                    room_ok = True
                    if not pd.isna(swing_high) and swing_high > close_price:
                        room = swing_high - close_price
                        if room < (0.8 * sl_dist): # blocked by immediate overhead swing
                            room_ok = False
                            
                    if room_ok:
                        sl = close_price - sl_dist
                        tp = close_price + (sl_dist * self.rr_ratio)
                        signals.append({
                            'time': time,
                            'direction': 'BUY',
                            'entry_price': close_price,
                            'stop_loss': sl,
                            'take_profit': tp,
                            'sl_dist': sl_dist,
                            'h1_adx': curr['h1_adx'],
                            'h1_atr': h1_atr,
                            'vwap': vwap
                        })
            
            # Short Criteria:
            # 1. 1H Trend: 9 EMA < 20 EMA
            # 2. 5M Flip: previous close >= 20 EMA, current close < 9 EMA and close < 20 EMA
            # 3. Session VWAP: close < VWAP
            elif curr['h1_ema9'] < curr['h1_ema20']:
                is_short_flip = (prev['close'] >= prev['m5_ema20']) and (curr['close'] < curr['m5_ema9']) and (curr['close'] < curr['m5_ema20'])
                if is_short_flip and (close_price < vwap):
                    swing_low = curr['swing_low_2h']
                    room_ok = True
                    if not pd.isna(swing_low) and swing_low < close_price:
                        room = close_price - swing_low
                        if room < (0.8 * sl_dist):
                            room_ok = False
                            
                    if room_ok:
                        sl = close_price + sl_dist
                        tp = close_price - (sl_dist * self.rr_ratio)
                        signals.append({
                            'time': time,
                            'direction': 'SELL',
                            'entry_price': close_price,
                            'stop_loss': sl,
                            'take_profit': tp,
                            'sl_dist': sl_dist,
                            'h1_adx': curr['h1_adx'],
                            'h1_atr': h1_atr,
                            'vwap': vwap
                        })
                        
        return signals
