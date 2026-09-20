"""
Experimental DCC Modular Strategy Engine for Parameter Optimization.
Strictly isolated in experiments/strategy_optimizer/ - DOES NOT TOUCH PRODUCTION FILES.

Supports configurable toggles:
- use_sweep: bool (5M Liquidity Sweep)
- max_h1_stretch: Optional[float] (Max distance from 1H 20 EMA normalized by 1H ATR)
- max_recent_flips: Optional[int] (Chop filter: max 5M EMA crossovers in lookback)
- tp_model: str ('fixed' or 'dynamic_2h')
- adx_min: float (Minimum 1H ADX threshold)
- blocked_hours: List[int] (e.g. [9, 13] or [])
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


@dataclass
class StrategyConfig:
    name: str
    use_sweep: bool = False
    max_h1_stretch: Optional[float] = None       # e.g. 0.85 means max 0.85 * ATR_1H
    max_recent_flips: Optional[int] = None      # e.g. 1 means max 1 flip in last 12 bars (60m)
    tp_model: str = "fixed"                     # 'fixed' or 'dynamic_2h'
    adx_min: float = 15.0                       # 15.0 or 25.0
    blocked_hours: Optional[List[int]] = None   # None or [9, 13]
    tp1_rr: float = 1.4
    tp2_rr: float = 2.1
    atr_sl_mult: float = 1.3                    # 1.3 for Gold, 1.4 for NAS


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
        # Bullish: find candidate fractal lows in [swing_start, pullback_start]
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
        # Bearish: find candidate fractal highs in [swing_start, pullback_start]
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


def count_recent_flips(df_m5: pd.DataFrame, idx: int, lookback_bars: int = 12) -> int:
    """
    Counts the number of 5M EMA crossovers in the last `lookback_bars`.
    Helps identify choppy sideways consolidation.
    """
    if idx < lookback_bars + 1:
        return 0
    start = idx - lookback_bars
    e9 = df_m5['ema9_5m'].iloc[start:idx+1].values
    e20 = df_m5['ema20_5m'].iloc[start:idx+1].values
    diff = e9 - e20
    # A flip occurs where sign changes
    flips = np.sum((diff[:-1] * diff[1:]) < 0)
    return int(flips)


def compute_dynamic_tp(
    entry_p: float,
    sl_dist: float,
    direction: int,
    swing_h_2h: float,
    swing_l_2h: float,
    default_tp1_rr: float = 1.4,
    default_tp2_rr: float = 2.1
) -> Tuple[float, float, float, float]:
    """
    Computes dynamic TP1 and TP2 based on available clean room to 2H obstacle.
    Returns: (tp1_p, tp2_p, tp1_rr, tp2_rr)
    """
    if sl_dist <= 0:
        return entry_p, entry_p, default_tp1_rr, default_tp2_rr

    if direction == 1:
        # Long: obstacle is 2H Swing High
        if swing_h_2h > entry_p:
            clean_room_pts = swing_h_2h - entry_p
            clean_rr = clean_room_pts / sl_dist
            
            if clean_rr >= default_tp2_rr:
                # Clean space for full 2.1R
                tp1_rr = default_tp1_rr
                tp2_rr = default_tp2_rr
            elif clean_rr >= 1.6:
                # Target TP2 before obstacle at 1.5R
                tp1_rr = 1.2
                tp2_rr = 1.5
            elif clean_rr >= 1.3:
                # Scalp target at 1.2R
                tp1_rr = 1.0
                tp2_rr = 1.2
            else:
                # Obstacle directly ahead (<1.3R), tight 1.0R
                tp1_rr = 0.8
                tp2_rr = 1.0
        else:
            tp1_rr = default_tp1_rr
            tp2_rr = default_tp2_rr

        tp1_p = entry_p + (tp1_rr * sl_dist)
        tp2_p = entry_p + (tp2_rr * sl_dist)

    else:
        # Short: obstacle is 2H Swing Low
        if 0 < swing_l_2h < entry_p:
            clean_room_pts = entry_p - swing_l_2h
            clean_rr = clean_room_pts / sl_dist
            
            if clean_rr >= default_tp2_rr:
                tp1_rr = default_tp1_rr
                tp2_rr = default_tp2_rr
            elif clean_rr >= 1.6:
                tp1_rr = 1.2
                tp2_rr = 1.5
            elif clean_rr >= 1.3:
                tp1_rr = 1.0
                tp2_rr = 1.2
            else:
                tp1_rr = 0.8
                tp2_rr = 1.0
        else:
            tp1_rr = default_tp1_rr
            tp2_rr = default_tp2_rr

        tp1_p = entry_p - (tp1_rr * sl_dist)
        tp2_p = entry_p - (tp2_rr * sl_dist)

    return tp1_p, tp2_p, tp1_rr, tp2_rr
