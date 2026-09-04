"""
DCC Strategy Core Engine
Implements the multi-timeframe rules extracted from DCC Capital:
- 2H Map: Swing High/Low, Daily Open, Target Room
- 1H Bias: EMA(9) vs EMA(20), 1H EMA20 Level, ADX(14) >= 25, ATR(14)
- 5M Execution: PFG Entry Model (Pullback -> Flip -> Go Filters), Session VWAP
- Risk Management: SL = 0.9 * 1H ATR, TP = 1.5x - 2.0x SL, Session Timing Filters
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


class SignalType(Enum):
    NONE = 0
    BUY = 1
    SELL = -1


@dataclass
class TradeSignal:
    timestamp: datetime
    signal_type: SignalType
    symbol: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    sl_distance: float
    tp_distance: float
    atr_1h: float
    adx_1h: float
    vwap_5m: float
    ema20_1h_level: float
    swing_high_2h: float
    swing_low_2h: float
    daily_open: float
    reason: str


class DCCEngine:
    def __init__(
        self,
        ema_fast_period: int = 9,
        ema_slow_period: int = 20,
        adx_period: int = 14,
        adx_min_threshold: float = 25.0,
        atr_period: int = 14,
        atr_sl_multiplier: float = 0.9,
        risk_reward_ratio: float = 1.5,
        swing_lookback_2h: int = 20,
        check_2h_room: bool = True,
        use_daily_open_filter: bool = True,
        london_session_start: time = time(6, 0),
        london_session_end: time = time(9, 0),
        ny_session_start: time = time(12, 0),
        ny_session_end: time = time(14, 0),
        avoid_monday_london: bool = True,
        avoid_friday_ny: bool = True,
        avoid_nfp_friday: bool = True,
        eod_exit_time: time = time(21, 0),
    ):
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period
        self.adx_period = adx_period
        self.adx_min_threshold = adx_min_threshold
        self.atr_period = atr_period
        self.atr_sl_multiplier = atr_sl_multiplier
        self.risk_reward_ratio = risk_reward_ratio
        self.swing_lookback_2h = swing_lookback_2h
        self.check_2h_room = check_2h_room
        self.use_daily_open_filter = use_daily_open_filter

        # Session timing (UTC)
        self.london_session_start = london_session_start
        self.london_session_end = london_session_end
        self.ny_session_start = ny_session_start
        self.ny_session_end = ny_session_end
        self.avoid_monday_london = avoid_monday_london
        self.avoid_friday_ny = avoid_friday_ny
        self.avoid_nfp_friday = avoid_nfp_friday
        self.eod_exit_time = eod_exit_time

    @staticmethod
    def calc_ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df['high']
        low = df['low']
        close = df['close']
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        # RMA (Wilder's smoothing)
        atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
        return atr

    @staticmethod
    def calc_adx(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
        high = df['high']
        low = df['low']
        close = df['close']
        prev_high = high.shift(1)
        prev_low = low.shift(1)
        prev_close = close.shift(1)

        plus_dm = high - prev_high
        minus_dm = prev_low - low

        plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
        minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        alpha = 1.0 / period
        atr = tr.ewm(alpha=alpha, adjust=False).mean()
        plus_di = 100.0 * (pd.Series(plus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / atr)
        minus_di = 100.0 * (pd.Series(minus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / atr)

        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
        adx = dx.ewm(alpha=alpha, adjust=False).mean()
        return adx, plus_di, minus_di

    @staticmethod
    def calc_session_vwap(df_5m: pd.DataFrame) -> pd.Series:
        """
        Calculates intraday Session VWAP resetting at 00:00 UTC each day.
        df_5m must have a DatetimeIndex in UTC and columns 'typical_price' (or high/low/close) and 'volume'.
        """
        if 'typical_price' not in df_5m.columns:
            typical_price = (df_5m['high'] + df_5m['low'] + df_5m['close']) / 3.0
        else:
            typical_price = df_5m['typical_price']

        vol = df_5m['volume']
        # If volume is 0 everywhere, fall back to 1.0
        if vol.sum() == 0:
            vol = pd.Series(1.0, index=df_5m.index)

        pv = typical_price * vol

        # Group by UTC calendar date
        dates = df_5m.index.date
        cum_pv = pv.groupby(dates).cumsum()
        cum_vol = vol.groupby(dates).cumsum()

        vwap = cum_pv / (cum_vol + 1e-9)
        return vwap

    def is_session_active(self, dt: datetime) -> bool:
        """
        Check if the timestamp falls inside allowed trading hours:
        London: 06:00 - 09:00 UTC
        New York: 12:00 - 14:00 UTC
        Excluding:
        - Monday London (trends need time to develop)
        - Friday NY (preparing for weekly close)
        - NFP Friday (first Friday of month)
        """
        t = dt.time()
        weekday = dt.weekday()  # 0=Monday, 4=Friday, 5=Sat, 6=Sun

        if weekday >= 5:
            return False

        # NFP Friday check (First Friday of month)
        if self.avoid_nfp_friday and weekday == 4:
            if dt.day <= 7:
                return False

        # Monday London check
        in_london = (self.london_session_start <= t < self.london_session_end)
        if in_london:
            if self.avoid_monday_london and weekday == 0:
                return False
            return True

        # Friday New York check
        in_ny = (self.ny_session_start <= t < self.ny_session_end)
        if in_ny:
            if self.avoid_friday_ny and weekday == 4:
                return False
            return True

        return False

    def prepare_data(
        self,
        df_5m: pd.DataFrame,
        df_1h: pd.DataFrame,
        df_2h: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Computes all multi-timeframe indicators and merges them onto the 5M DataFrame
        without lookahead bias (using strictly past closed 1H and 2H bars).
        """
        df_5m = df_5m.copy().sort_index()
        df_1h = df_1h.copy().sort_index()
        df_2h = df_2h.copy().sort_index()

        # 1. 5M Indicators
        df_5m['ema9_5m'] = self.calc_ema(df_5m['close'], self.ema_fast_period)
        df_5m['ema20_5m'] = self.calc_ema(df_5m['close'], self.ema_slow_period)
        df_5m['vwap_5m'] = self.calc_session_vwap(df_5m)

        # 2. 1H Indicators
        df_1h['ema9_1h'] = self.calc_ema(df_1h['close'], self.ema_fast_period)
        df_1h['ema20_1h'] = self.calc_ema(df_1h['close'], self.ema_slow_period)
        df_1h['adx_1h'], _, _ = self.calc_adx(df_1h, self.adx_period)
        df_1h['atr_1h'] = self.calc_atr(df_1h, self.atr_period)

        # Bias on 1H: 1 = Bullish, -1 = Bearish, 0 = Neutral
        df_1h['bias_1h'] = np.where(
            df_1h['ema9_1h'] > df_1h['ema20_1h'], 1,
            np.where(df_1h['ema9_1h'] < df_1h['ema20_1h'], -1, 0)
        )

        # 3. 2H Indicators (Swing High/Low, Daily Open)
        df_2h['swing_high_2h'] = df_2h['high'].rolling(self.swing_lookback_2h, min_periods=5).max()
        df_2h['swing_low_2h'] = df_2h['low'].rolling(self.swing_lookback_2h, min_periods=5).min()

        # Daily Open computed on 5M (first open of each UTC day)
        daily_open_map = df_5m.groupby(df_5m.index.date)['open'].first()
        df_5m['daily_open'] = df_5m.index.date
        df_5m['daily_open'] = df_5m['daily_open'].map(daily_open_map)

        # 4. Merge 1H and 2H into 5M without lookahead bias
        # Each 5M bar only knows the values of the 1H / 2H bar that CLOSED before it.
        # Shift 1H and 2H by 1 bar before reindexing/forward-filling.
        df_1h_shifted = df_1h[['ema9_1h', 'ema20_1h', 'adx_1h', 'atr_1h', 'bias_1h']].shift(1)
        df_2h_shifted = df_2h[['swing_high_2h', 'swing_low_2h']].shift(1)

        # Reindex to 5M using merge_asof (backward lookup)
        df_5m_merged = pd.merge_asof(
            df_5m,
            df_1h_shifted,
            left_index=True,
            right_index=True,
            direction='backward'
        )
        df_5m_merged = pd.merge_asof(
            df_5m_merged,
            df_2h_shifted,
            left_index=True,
            right_index=True,
            direction='backward'
        )

        # Store the horizontal 1H 20 EMA level (from latest closed 1H bar)
        df_5m_merged['ema20_1h_level'] = df_5m_merged['ema20_1h']

        return df_5m_merged

    def evaluate_bar(
        self,
        prev_bar: pd.Series,
        curr_bar: pd.Series,
        symbol: str = "XAUUSD"
    ) -> Optional[TradeSignal]:
        """
        Evaluates a newly closed 5M bar for a PFG setup:
        - Pull: 9 EMA was counter to bias
        - Flip: 9 EMA crossed 20 EMA in bias direction
        - Go: ADX >= 25, price vs VWAP, price vs 1H EMA20 level, 2H room, session filter
        """
        curr_time: datetime = curr_bar.name
        if not self.is_session_active(curr_time):
            return None

        bias_1h = curr_bar['bias_1h']
        if pd.isna(bias_1h) or bias_1h == 0:
            return None

        adx_1h = curr_bar['adx_1h']
        if pd.isna(adx_1h) or adx_1h < self.adx_min_threshold:
            return None

        atr_1h = curr_bar['atr_1h']
        if pd.isna(atr_1h) or atr_1h <= 0:
            return None

        close = curr_bar['close']
        vwap = curr_bar['vwap_5m']
        ema20_1h_lvl = curr_bar['ema20_1h_level']
        daily_open = curr_bar['daily_open']
        swing_h = curr_bar['swing_high_2h']
        swing_l = curr_bar['swing_low_2h']

        sl_distance = self.atr_sl_multiplier * atr_1h
        tp_distance = self.risk_reward_ratio * sl_distance

        # Detect 5M EMA Flips (Crossovers on bar close)
        prev_fast = prev_bar['ema9_5m']
        prev_slow = prev_bar['ema20_5m']
        curr_fast = curr_bar['ema9_5m']
        curr_slow = curr_bar['ema20_5m']

        is_bullish_flip = (prev_fast <= prev_slow) and (curr_fast > curr_slow)
        is_bearish_flip = (prev_fast >= prev_slow) and (curr_fast < curr_slow)

        # LONG SETUP
        if bias_1h == 1 and is_bullish_flip:
            # Go Checklist
            # 1. Price above VWAP
            if close <= vwap:
                return None
            # 2. Price above 1H 20 EMA Level
            if close <= ema20_1h_lvl:
                return None
            # 3. Daily open filter (optional/recommended defense)
            if self.use_daily_open_filter and pd.notna(daily_open) and close <= daily_open:
                return None
            # 4. Room to 2H Swing High
            if self.check_2h_room and pd.notna(swing_h):
                room = swing_h - close
                if room < tp_distance:
                    return None  # Obstacle too close

            sl = close - sl_distance
            tp = close + tp_distance

            return TradeSignal(
                timestamp=curr_time,
                signal_type=SignalType.BUY,
                symbol=symbol,
                entry_price=close,
                stop_loss=sl,
                take_profit=tp,
                risk_reward=self.risk_reward_ratio,
                sl_distance=sl_distance,
                tp_distance=tp_distance,
                atr_1h=atr_1h,
                adx_1h=adx_1h,
                vwap_5m=vwap,
                ema20_1h_level=ema20_1h_lvl,
                swing_high_2h=swing_h if pd.notna(swing_h) else 0.0,
                swing_low_2h=swing_l if pd.notna(swing_l) else 0.0,
                daily_open=daily_open if pd.notna(daily_open) else 0.0,
                reason="DCC Long: Bullish 1H bias + 5M PFG Flip + VWAP/EMA20/ADX confirmed"
            )

        # SHORT SETUP
        elif bias_1h == -1 and is_bearish_flip:
            # Go Checklist
            # 1. Price below VWAP
            if close >= vwap:
                return None
            # 2. Price below 1H 20 EMA Level
            if close >= ema20_1h_lvl:
                return None
            # 3. Daily open filter
            if self.use_daily_open_filter and pd.notna(daily_open) and close >= daily_open:
                return None
            # 4. Room to 2H Swing Low
            if self.check_2h_room and pd.notna(swing_l):
                room = close - swing_l
                if room < tp_distance:
                    return None  # Obstacle too close

            sl = close + sl_distance
            tp = close - tp_distance

            return TradeSignal(
                timestamp=curr_time,
                signal_type=SignalType.SELL,
                symbol=symbol,
                entry_price=close,
                stop_loss=sl,
                take_profit=tp,
                risk_reward=self.risk_reward_ratio,
                sl_distance=sl_distance,
                tp_distance=tp_distance,
                atr_1h=atr_1h,
                adx_1h=adx_1h,
                vwap_5m=vwap,
                ema20_1h_level=ema20_1h_lvl,
                swing_high_2h=swing_h if pd.notna(swing_h) else 0.0,
                swing_low_2h=swing_l if pd.notna(swing_l) else 0.0,
                daily_open=daily_open if pd.notna(daily_open) else 0.0,
                reason="DCC Short: Bearish 1H bias + 5M PFG Flip + VWAP/EMA20/ADX confirmed"
            )

        return None
