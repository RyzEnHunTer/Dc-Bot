"""
DCC Strategy v1.2 "ApexHunter" Core Engine
==========================================
Version Hierarchy:
- v1.0: Baseline DCC (Standard PFG Trend Following Reference)
- v1.1: Early ApexHunter (TripleGuard with Hard Daily Killzone Pause)
- v1.2: DCC ApexHunter (Flagship Smart Killzone Stretch>=1.10x + Dual-Gear Engine)

Enhancements in ApexHunter v1.2:
1. [SMART KZ]  Smart Hybrid Killzone     : Hours 09:00 & 13:00 UTC allowed ONLY if Stretch >= 1.10x ATR
2. [FLOOR]     1H Anti-Chop Floor        : Stretch Ratio >= 0.40x ATR (kills consolidation chop)
3. [CEILING]   1H ADX Exhaustion Ceiling  : 1H ADX <= 45.0 (kills parabolic top/bottom traps)
4. [EXECUTION] 5M No-Chase Guard         : Chase Ratio <= 0.50x ATR (kills bloated candle chasing)
5. [BYPASS]    2H Swing Room Rule        : Permanently DISABLED (unshackles trend breakouts)
6. [CALENDAR]  Monday US Afternoon Shield: Rejects entries on Mondays between 14:00 and 18:00 UTC
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
class TradeSignalV12:
    timestamp: datetime
    signal_type: SignalType
    symbol: str
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_1: float
    risk_reward_2: float
    sl_distance: float
    tp1_distance: float
    tp2_distance: float
    atr_1h: float
    adx_1h: float
    stretch_ratio: float
    chase_ratio: float
    vwap_5m: float
    ema20_1h_level: float
    reason: str
    shield_diagnostics: Dict[str, bool] = field(default_factory=dict)


class DCCEngineV12:
    def __init__(
        self,
        symbol: str = "XAUUSD",
        ema_fast_period: int = 9,
        ema_slow_period: int = 20,
        adx_period: int = 14,
        adx_min_threshold: float = 15.0,
        max_1h_adx: float = 45.0,
        atr_period: int = 14,
        atr_sl_multiplier: float = 0.90,
        tp1_rr: float = 1.40,
        tp2_rr: float = 2.20,
        min_1h_stretch: float = 0.40,
        max_5m_chase: float = 0.50,
        check_2h_room: bool = False,
        use_daily_open_filter: bool = False,
        block_monday_pm: bool = True,
        session_start_hour: int = 6,
        session_end_hour: int = 19,
        dead_trap_hours: Optional[List[int]] = None,
    ):
        self.symbol = symbol
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period
        self.adx_period = adx_period
        self.adx_min_threshold = adx_min_threshold
        self.max_1h_adx = max_1h_adx
        self.atr_period = atr_period
        self.atr_sl_multiplier = atr_sl_multiplier
        self.tp1_rr = tp1_rr
        self.tp2_rr = tp2_rr

        # TripleGuard Volatility Shields
        self.min_1h_stretch = min_1h_stretch
        self.max_5m_chase = max_5m_chase
        self.check_2h_room = check_2h_room
        self.use_daily_open_filter = use_daily_open_filter

        # Calendar Shield
        self.block_monday_pm = block_monday_pm
        self.monday_pm_blocked_hours = [14, 15, 16, 17]
        self.dead_trap_hours = dead_trap_hours if dead_trap_hours is not None else [9, 13]

        # Session Timing (UTC)
        self.session_start_hour = session_start_hour
        self.session_end_hour = session_end_hour

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
        return tr.rolling(period, min_periods=period).mean()

    @staticmethod
    def calc_adx(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
        high = df['high']
        low = df['low']
        close = df['close']
        prev_high = high.shift(1)
        prev_low = low.shift(1)
        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_adx = tr.rolling(period, min_periods=period).mean()

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        plus_dm_s = pd.Series(plus_dm, index=df.index).rolling(period, min_periods=period).mean()
        minus_dm_s = pd.Series(minus_dm, index=df.index).rolling(period, min_periods=period).mean()

        plus_di = 100.0 * (plus_dm_s / (atr_adx + 1e-9))
        minus_di = 100.0 * (minus_dm_s / (atr_adx + 1e-9))

        dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
        adx = dx.rolling(period, min_periods=period).mean()
        return adx, plus_di, minus_di

    @staticmethod
    def calc_session_vwap(df_5m: pd.DataFrame) -> pd.Series:
        df = df_5m.copy()
        typical_price = (df['high'] + df['low'] + df['close']) / 3.0
        pv = typical_price * df['volume']
        dates = df.index.date
        cum_pv = pv.groupby(dates).cumsum()
        cum_vol = df['volume'].groupby(dates).cumsum()
        vwap = cum_pv / (cum_vol + 1e-9)
        return vwap

    @staticmethod
    def detect_liquidity_sweep_fast(
        df_m5: pd.DataFrame,
        idx: int,
        direction: int,
        swing_lookback: int = 20,
        pullback_window: int = 8
    ) -> Tuple[bool, float, float]:
        """
        Detection of 5M liquidity sweep on closed bar matching official fractal sweep algorithm.
        direction == 1: Bullish sweep of prior swing low and reclaim.
        direction == -1: Bearish sweep of prior swing high and reclaim.
        """
        if idx < swing_lookback + pullback_window:
            return False, 0.0, 0.0

        pullback_start = max(0, idx - pullback_window)
        swing_start = max(0, pullback_start - swing_lookback)
        if pullback_start <= swing_start:
            return False, 0.0, 0.0

        lows = df_m5['low'].values
        highs = df_m5['high'].values
        closes = df_m5['close'].values

        if direction == 1:
            c_lows = []
            for j in range(swing_start + 1, pullback_start):
                if lows[j] <= lows[j - 1] and lows[j] <= lows[j + 1]:
                    c_lows.append(lows[j])
            if not c_lows:
                c_lows = [float(np.min(lows[swing_start:pullback_start]))]

            c_close = closes[idx]
            min_p_low = float(np.min(lows[pullback_start:idx + 1]))
            for sw_low in c_lows:
                if min_p_low < sw_low and c_close > sw_low:
                    return True, float(sw_low), float(sw_low - min_p_low)
            return False, 0.0, 0.0
        else:
            c_highs = []
            for j in range(swing_start + 1, pullback_start):
                if highs[j] >= highs[j - 1] and highs[j] >= highs[j + 1]:
                    c_highs.append(highs[j])
            if not c_highs:
                c_highs = [float(np.max(highs[swing_start:pullback_start]))]

            c_close = closes[idx]
            max_p_high = float(np.max(highs[pullback_start:idx + 1]))
            for sw_high in c_highs:
                if max_p_high > sw_high and c_close < sw_high:
                    return True, float(sw_high), float(max_p_high - sw_high)
            return False, 0.0, 0.0

    @staticmethod
    def detect_liquidity_sweep(
        df_5m: pd.DataFrame,
        idx: int,
        direction: int,
        swing_lookback: int = 20,
        pullback_window: int = 8
    ) -> Tuple[bool, float, float]:
        """Detects whether recent price action swept swing highs/lows before the flip."""
        if idx < swing_lookback + pullback_window:
            return False, 0.0, 0.0

        if direction == 1:  # Bullish setup: look for liquidity sweep of swing low
            swing_zone = df_5m.iloc[idx - swing_lookback - pullback_window : idx - pullback_window]
            swing_low = float(swing_zone['low'].min())
            pullback_zone = df_5m.iloc[idx - pullback_window : idx + 1]
            pullback_low = float(pullback_zone['low'].min())
            if pullback_low < swing_low:
                pts_swept = swing_low - pullback_low
                return True, swing_low, pts_swept
        elif direction == -1:  # Bearish setup: look for liquidity sweep of swing high
            swing_zone = df_5m.iloc[idx - swing_lookback - pullback_window : idx - pullback_window]
            swing_high = float(swing_zone['high'].max())
            pullback_zone = df_5m.iloc[idx - pullback_window : idx + 1]
            pullback_high = float(pullback_zone['high'].max())
            if pullback_high > swing_high:
                pts_swept = pullback_high - swing_high
                return True, swing_high, pts_swept

        return False, 0.0, 0.0

    def prepare_data(
        self,
        df_5m: pd.DataFrame,
        df_1h: pd.DataFrame,
        df_2h: Optional[pd.DataFrame] = None
    ) -> pd.DataFrame:
        """
        Merges 5M, 1H, and 2H DataFrames without lookahead bias.
        Computes dynamic Stretch Ratio and Chase Ratio normalized by 1H ATR.
        """
        df_5m = df_5m.copy().sort_index()
        df_1h = df_1h.copy().sort_index()

        # 1. 5M Indicators
        df_5m['ema9_5m'] = self.calc_ema(df_5m['close'], self.ema_fast_period)
        df_5m['ema20_5m'] = self.calc_ema(df_5m['close'], self.ema_slow_period)
        df_5m['vwap_5m'] = self.calc_session_vwap(df_5m)

        # 2. 1H Indicators
        df_1h['ema9_1h'] = self.calc_ema(df_1h['close'], self.ema_fast_period)
        df_1h['ema20_1h'] = self.calc_ema(df_1h['close'], self.ema_slow_period)
        df_1h['adx_1h'], _, _ = self.calc_adx(df_1h, self.adx_period)
        df_1h['atr_1h'] = self.calc_atr(df_1h, self.atr_period)

        df_1h['bias_1h'] = np.where(
            df_1h['ema9_1h'] > df_1h['ema20_1h'], 1,
            np.where(df_1h['ema9_1h'] < df_1h['ema20_1h'], -1, 0)
        )

        # Shift 1H by 1 bar backward so 5M bars only look at strictly closed 1H bars
        df_1h_shifted = df_1h[['ema9_1h', 'ema20_1h', 'adx_1h', 'atr_1h', 'bias_1h']].shift(1)

        # Reindex to 5M using merge_asof backward
        df_merged = pd.merge_asof(
            df_5m,
            df_1h_shifted,
            left_index=True,
            right_index=True,
            direction='backward'
        )

        # 3. Dynamic Volatility Ratios (TripleGuard Core)
        dist_to_h1_ema20 = (df_merged['close'] - df_merged['ema20_1h']).abs()
        df_merged['stretch_ratio'] = dist_to_h1_ema20 / (df_merged['atr_1h'] + 1e-9)

        dist_to_5m_ema9 = (df_merged['close'] - df_merged['ema9_5m']).abs()
        df_merged['chase_ratio'] = dist_to_5m_ema9 / (df_merged['atr_1h'] + 1e-9)

        # 4. Optional 2H Swings (For logging/auditing only; Room Rule is bypassed)
        if df_2h is not None and len(df_2h) > 0:
            df_2h = df_2h.copy().sort_index()
            df_2h['swing_high_2h'] = df_2h['high'].rolling(20, min_periods=5).max()
            df_2h['swing_low_2h'] = df_2h['low'].rolling(20, min_periods=5).min()
            df_2h_shifted = df_2h[['swing_high_2h', 'swing_low_2h']].shift(1)
            df_merged = pd.merge_asof(
                df_merged,
                df_2h_shifted,
                left_index=True,
                right_index=True,
                direction='backward'
            )
        else:
            df_merged['swing_high_2h'] = np.nan
            df_merged['swing_low_2h'] = np.nan

        df_merged['ema20_1h_level'] = df_merged['ema20_1h']
        return df_merged

    def evaluate_bar(
        self,
        prev_bar: pd.Series,
        curr_bar: pd.Series,
        symbol: Optional[str] = None
    ) -> Optional[TradeSignalV12]:
        """
        Evaluates a newly closed 5M bar under DCC v1.2 TripleGuard rules:
        - 1H Bias
        - 5M EMA Crossover Flip
        - VWAP & 1H 20 EMA Level
        - Shield 1: 1H Anti-Chop Floor (Stretch >= 0.40)
        - Shield 2: 1H ADX Exhaustion Ceiling (ADX <= 45.0)
        - Shield 3: 5M No-Chase Guard (Chase <= 0.50)
        - Calendar Shield: Monday PM Block (14:00 - 18:00 UTC)
        """
        curr_time: datetime = curr_bar.name
        t_hour = curr_time.hour
        t_wday = curr_time.strftime('%A')
        sym = symbol if symbol else self.symbol

        # 1. Trading Session Hours (06:00 to 19:00 UTC) & Dead Trap Hours (09:00, 13:00 UTC)
        if not (self.session_start_hour <= t_hour < self.session_end_hour):
            return None

        # ApexHunter Smart Killzone Filter (Hours 09:00 & 13:00 UTC: skip if stretch < 1.10x)
        stretch_ratio = float(curr_bar['stretch_ratio']) if 'stretch_ratio' in curr_bar and not pd.isna(curr_bar['stretch_ratio']) else 1.0
        if self.dead_trap_hours and t_hour in self.dead_trap_hours:
            if stretch_ratio < 1.10:
                return None

        # 2. Calendar Shield: Monday Afternoon Trap Guardrail
        if self.block_monday_pm and t_wday == "Monday" and t_hour in self.monday_pm_blocked_hours:
            return None

        # 3. 1H Bias Confirmation
        bias_1h = curr_bar['bias_1h']
        if pd.isna(bias_1h) or bias_1h == 0:
            return None

        # 4. 1H ADX Baseline Threshold
        adx_1h = curr_bar['adx_1h']
        if pd.isna(adx_1h) or adx_1h < self.adx_min_threshold:
            return None

        # 5. 1H ATR Validation
        atr_1h = curr_bar['atr_1h']
        if pd.isna(atr_1h) or atr_1h <= 0:
            return None

        close = curr_bar['close']
        vwap = curr_bar['vwap_5m']
        ema20_1h_lvl = curr_bar['ema20_1h_level']
        stretch_ratio = float(curr_bar['stretch_ratio'])
        chase_ratio = float(curr_bar['chase_ratio'])

        # 6. Detect 5M EMA Flip
        prev_diff = float(prev_bar['ema9_5m']) - float(prev_bar['ema20_5m'])
        curr_diff = float(curr_bar['ema9_5m']) - float(curr_bar['ema20_5m'])

        is_bullish_flip = (bias_1h == 1) and (prev_diff <= 0) and (curr_diff > 0)
        is_bearish_flip = (bias_1h == -1) and (prev_diff >= 0) and (curr_diff < 0)

        if not (is_bullish_flip or is_bearish_flip):
            return None

        # 7. VWAP & 1H 20 EMA Level Filters
        if is_bullish_flip:
            if close <= vwap or close <= ema20_1h_lvl:
                return None
        elif is_bearish_flip:
            if close >= vwap or close >= ema20_1h_lvl:
                return None

        # 8. TRIPLEGUARD VOLATILITY SHIELDS
        shield_checks = {
            "anti_chop_floor": stretch_ratio >= self.min_1h_stretch,
            "adx_ceiling": adx_1h <= self.max_1h_adx,
            "no_chase_guard": chase_ratio <= self.max_5m_chase
        }

        # Check Shield 1: Anti-Chop Floor
        if not shield_checks["anti_chop_floor"]:
            return None

        # Check Shield 2: ADX Exhaustion Ceiling
        if not shield_checks["adx_ceiling"]:
            return None

        # Check Shield 3: No-Chase Guard
        if not shield_checks["no_chase_guard"]:
            return None

        # 9. Asymmetric Risk-Reward Calculation
        sl_distance = self.atr_sl_multiplier * atr_1h
        tp1_distance = self.tp1_rr * sl_distance
        tp2_distance = self.tp2_rr * sl_distance

        sig_type = SignalType.BUY if is_bullish_flip else SignalType.SELL
        sl = close - sl_distance if sig_type == SignalType.BUY else close + sl_distance
        tp1 = close + tp1_distance if sig_type == SignalType.BUY else close - tp1_distance
        tp2 = close + tp2_distance if sig_type == SignalType.BUY else close - tp2_distance

        dir_str = "Long" if sig_type == SignalType.BUY else "Short"
        reason = (
            f"DCC v1.2 {dir_str}: 1H Bias + 5M PFG Flip + VWAP/EMA20 + "
            f"TripleGuard Verified (Stretch: {stretch_ratio:.2f}x >= {self.min_1h_stretch}, "
            f"ADX: {adx_1h:.1f} <= {self.max_1h_adx}, Chase: {chase_ratio:.2f}x <= {self.max_5m_chase})"
        )

        return TradeSignalV12(
            timestamp=curr_time,
            signal_type=sig_type,
            symbol=sym,
            entry_price=close,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            risk_reward_1=self.tp1_rr,
            risk_reward_2=self.tp2_rr,
            sl_distance=sl_distance,
            tp1_distance=tp1_distance,
            tp2_distance=tp2_distance,
            atr_1h=atr_1h,
            adx_1h=adx_1h,
            stretch_ratio=stretch_ratio,
            chase_ratio=chase_ratio,
            vwap_5m=vwap,
            ema20_1h_level=ema20_1h_lvl,
            reason=reason,
            shield_diagnostics=shield_checks
        )
