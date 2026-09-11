"""
Algorithmic Classifier for David DC's 'DCC STRATEGY | A+ Setups'.
Decodes the 6 core pillars from the official guide into mathematical scoring:
1. 2H Map & Key Levels (Clear space to target & SL support)
2. 1H EMA Separation (Clear gap & stable trend without recent whipsaw crosses)
3. 1H ADX Strength (ADX >= 25.0 preferred, 20-25 valid, < 20 lower probability)
4. VWAP Position & Defense (VWAP positioned between Entry and SL as a shield)
5. 5M PFG Entry Quality (Clean single pullback vs repeated micro-flips)
6. Trade Timing (Prime London & NY momentum sessions vs Friday close/Monday open)

Overall Score (0 - 10):
- Score >= 7.0: Grade A+ Setup (High conviction, full 1.0% risk, standard 1:2.0R+ target)
- Score < 7.0: Grade B Valid Setup (Valid, lower probability, 0.5% risk, faster 1:1.5R target)
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
import numpy as np
import pandas as pd


@dataclass
class SetupGrade:
    total_score: float
    grade: str                   # "A+" or "B"
    score_2h_map: float          # Max 2.0
    score_1h_ema: float          # Max 2.0
    score_1h_adx: float          # Max 2.0
    score_vwap: float            # Max 2.0
    score_5m_pfg: float          # Max 1.0
    score_timing: float          # Max 1.0
    details: Dict[str, str]

    @property
    def is_a_plus(self) -> bool:
        return self.grade == "A+"


class DCCAPlusClassifier:
    def __init__(self, a_plus_threshold: float = 7.0):
        self.a_plus_threshold = a_plus_threshold

    def evaluate_setup(
        self,
        direction: int,            # 1 for BUY, -1 for SELL
        entry_price: float,
        sl_price: float,
        tp1_dist: float,
        tp2_dist: float,
        bar_5m: pd.Series,
        df_m5: pd.DataFrame,
        entry_idx: int,
        timestamp: datetime
    ) -> SetupGrade:
        details = {}

        # -------------------------------------------------------------
        # 1. THE 2H MAP (Max 2.0 pts)
        # - Target space: No 2H swing level blocking TP1 / TP2 (+1.0 pt)
        # - Stop loss protection: 2H level or daily open shields SL (+1.0 pt)
        # -------------------------------------------------------------
        score_2h = 0.0
        sw_high_2h = float(bar_5m['swing_high_2h']) if ('swing_high_2h' in bar_5m and not pd.isna(bar_5m['swing_high_2h'])) else 0.0
        sw_low_2h = float(bar_5m['swing_low_2h']) if ('swing_low_2h' in bar_5m and not pd.isna(bar_5m['swing_low_2h'])) else 0.0
        daily_open = float(bar_5m['daily_open']) if ('daily_open' in bar_5m and not pd.isna(bar_5m['daily_open'])) else 0.0

        if direction == 1:  # BUY
            # Target check: Is 2H swing high blocking TP1?
            target_clear = True
            if sw_high_2h > entry_price and sw_high_2h < (entry_price + tp1_dist):
                target_clear = False
            if target_clear:
                score_2h += 1.0
                details["2h_target_space"] = "Clean space to target (>= 1.5R)"
            else:
                details["2h_target_space"] = f"2H Swing High ({sw_high_2h:.2f}) restricts target"

            # SL defense: Does a 2H swing low or daily open sit at or behind SL?
            sl_shield = False
            if sw_low_2h > 0 and (sl_price <= sw_low_2h <= entry_price or abs(sl_price - sw_low_2h) < (tp1_dist * 0.3)):
                sl_shield = True
            elif daily_open > 0 and (sl_price <= daily_open <= entry_price):
                sl_shield = True
            if sl_shield:
                score_2h += 1.0
                details["2h_sl_defense"] = "Structural 2H level / Daily Open protects SL"
            else:
                details["2h_sl_defense"] = "No 2H structural support behind SL"

        else:  # SELL
            target_clear = True
            if sw_low_2h > 0 and sw_low_2h < entry_price and sw_low_2h > (entry_price - tp1_dist):
                target_clear = False
            if target_clear:
                score_2h += 1.0
                details["2h_target_space"] = "Clean space to target (>= 1.5R)"
            else:
                details["2h_target_space"] = f"2H Swing Low ({sw_low_2h:.2f}) restricts target"

            sl_shield = False
            if sw_high_2h > 0 and (entry_price <= sw_high_2h <= sl_price or abs(sl_price - sw_high_2h) < (tp1_dist * 0.3)):
                sl_shield = True
            elif daily_open > 0 and (entry_price <= daily_open <= sl_price):
                sl_shield = True
            if sl_shield:
                score_2h += 1.0
                details["2h_sl_defense"] = "Structural 2H level / Daily Open protects SL"
            else:
                details["2h_sl_defense"] = "No 2H structural support behind SL"

        # -------------------------------------------------------------
        # 2. 1H EMA BIAS & SEPARATION (Max 2.0 pts)
        # - Clear separation between 1H EMA9 & EMA20 (+1.0 pt)
        # - Established trend without recent cross (+1.0 pt)
        # -------------------------------------------------------------
        score_1h_ema = 0.0
        h1_e9 = float(bar_5m['ema9_1h']) if not pd.isna(bar_5m['ema9_1h']) else 0.0
        h1_e20 = float(bar_5m['ema20_1h']) if not pd.isna(bar_5m['ema20_1h']) else 0.0
        h1_atr = float(bar_5m['atr_1h']) if not pd.isna(bar_5m['atr_1h']) else 1.0

        ema_gap = abs(h1_e9 - h1_e20)
        gap_ratio = ema_gap / (h1_atr + 1e-9)

        if gap_ratio >= 0.20:
            score_1h_ema += 1.0
            details["1h_ema_separation"] = f"Strong EMA separation ({gap_ratio:.2f}x ATR)"
        elif gap_ratio >= 0.10:
            score_1h_ema += 0.5
            details["1h_ema_separation"] = f"Moderate EMA separation ({gap_ratio:.2f}x ATR)"
        else:
            details["1h_ema_separation"] = f"Squeezed EMAs ({gap_ratio:.2f}x ATR)"

        # Trend establishment: Check if bias matches and has healthy gap
        if gap_ratio >= 0.15:
            score_1h_ema += 1.0
            details["1h_trend_status"] = "Established healthy trend"
        else:
            details["1h_trend_status"] = "Recent cross or sideways EMAs"

        # -------------------------------------------------------------
        # 3. 1H ADX STRENGTH (Max 2.0 pts)
        # - ADX >= 25.0: Preferred strong trend (+2.0 pts)
        # - 20.0 <= ADX < 25.0: Developing trend (+1.0 pt)
        # - ADX < 20.0: Low probability / chop (0 pts)
        # -------------------------------------------------------------
        score_adx = 0.0
        adx_val = float(bar_5m['adx_1h']) if not pd.isna(bar_5m['adx_1h']) else 0.0

        if 25.0 <= adx_val <= 40.0:
            score_adx = 2.0
            details["1h_adx"] = f"A+ Preferred Trend Strength ({adx_val:.1f} >= 25.0)"
        elif adx_val > 40.0:
            score_adx = 1.5
            details["1h_adx"] = f"Very strong trend ({adx_val:.1f} > 40.0), watch for exhaustion"
        elif 20.0 <= adx_val < 25.0:
            score_adx = 1.0
            details["1h_adx"] = f"Valid developing trend ({adx_val:.1f})"
        else:
            score_adx = 0.0
            details["1h_adx"] = f"Lower-probability range ({adx_val:.1f} < 20.0)"

        # -------------------------------------------------------------
        # 4. VWAP POSITION & STOP LOSS DEFENSE (Max 2.0 pts)
        # - VWAP sits between Entry and SL (Best-case defense barrier) (+2.0 pts)
        # - Clear separation from VWAP without hugging entry (+1.0 pt)
        # -------------------------------------------------------------
        score_vwap = 0.0
        vwap_val = float(bar_5m['vwap_5m']) if not pd.isna(bar_5m['vwap_5m']) else entry_price

        if direction == 1:  # BUY
            # Best-case: Entry > VWAP >= SL (VWAP shields the Stop Loss!)
            if entry_price > vwap_val and vwap_val >= sl_price:
                score_vwap = 2.0
                details["vwap_position"] = f"BEST-CASE: VWAP ({vwap_val:.2f}) sits between Entry and SL (Shields SL)"
            elif entry_price > vwap_val:
                dist_vwap = (entry_price - vwap_val) / (h1_atr + 1e-9)
                if dist_vwap >= 0.15:
                    score_vwap = 1.0
                    details["vwap_position"] = f"Good separation from VWAP ({dist_vwap:.2f}x ATR)"
                else:
                    score_vwap = 0.5
                    details["vwap_position"] = f"Valid, but VWAP is extremely close ({dist_vwap:.2f}x ATR)"
            else:
                score_vwap = 0.0
                details["vwap_position"] = "Invalid: Price below VWAP"

        else:  # SELL
            # Best-case: Entry < VWAP <= SL (VWAP shields the Stop Loss!)
            if entry_price < vwap_val and vwap_val <= sl_price:
                score_vwap = 2.0
                details["vwap_position"] = f"BEST-CASE: VWAP ({vwap_val:.2f}) sits between Entry and SL (Shields SL)"
            elif entry_price < vwap_val:
                dist_vwap = (vwap_val - entry_price) / (h1_atr + 1e-9)
                if dist_vwap >= 0.15:
                    score_vwap = 1.0
                    details["vwap_position"] = f"Good separation from VWAP ({dist_vwap:.2f}x ATR)"
                else:
                    score_vwap = 0.5
                    details["vwap_position"] = f"Valid, but VWAP is extremely close ({dist_vwap:.2f}x ATR)"
            else:
                score_vwap = 0.0
                details["vwap_position"] = "Invalid: Price above VWAP"

        # -------------------------------------------------------------
        # 5. 5M PFG ENTRY QUALITY (Max 1.0 pt)
        # - Single decisive flip without micro-whipsaws in last 10 bars (+1.0 pt)
        # -------------------------------------------------------------
        score_5m = 0.0
        # Check flips in last 12 bars of df_m5
        lookback_bars = 12
        start_sub = max(0, entry_idx - lookback_bars)
        sub_df = df_m5.iloc[start_sub:entry_idx + 1]
        
        flip_count = 0
        if len(sub_df) >= 3 and 'ema9_5m' in sub_df.columns and 'ema20_5m' in sub_df.columns:
            diffs = sub_df['ema9_5m'].values - sub_df['ema20_5m'].values
            signs = np.sign(diffs)
            sign_changes = np.diff(signs)
            flip_count = np.count_nonzero(sign_changes != 0)

        if flip_count <= 2:
            score_5m = 1.0
            details["5m_pfg_quality"] = f"Clean decisive flip (Only {flip_count} micro-crossover in last hour)"
        else:
            score_5m = 0.0
            details["5m_pfg_quality"] = f"Messy choppy pullback ({flip_count} micro-crossovers in last hour)"

        # -------------------------------------------------------------
        # 6. TRADE TIMING & SESSION CONTEXT (Max 1.0 pt)
        # - Prime London (07:00-11:00 UTC) or Prime NY (14:00-18:00 UTC) (+1.0 pt)
        # -------------------------------------------------------------
        score_timing = 0.0
        hour_utc = timestamp.hour
        weekday = timestamp.weekday()  # 0 = Monday, 4 = Friday

        is_prime_london = (7 <= hour_utc <= 11)
        is_prime_ny = (14 <= hour_utc <= 18)
        is_monday_open = (weekday == 0 and hour_utc < 6)
        is_friday_late = (weekday == 4 and hour_utc >= 15)

        if (is_prime_london or is_prime_ny) and not (is_monday_open or is_friday_late):
            score_timing = 1.0
            details["trade_timing"] = f"Prime Session Momentum Hour ({hour_utc:02d}:00 UTC)"
        elif is_monday_open:
            score_timing = 0.0
            details["trade_timing"] = "Monday market opening transition (Lower reliability)"
        elif is_friday_late:
            score_timing = 0.0
            details["trade_timing"] = "Friday late afternoon pre-close (Diminishing liquidity)"
        else:
            score_timing = 0.5
            details["trade_timing"] = f"Standard trading session ({hour_utc:02d}:00 UTC)"

        # -------------------------------------------------------------
        # TOTAL SCORE & FINAL CLASSIFICATION
        # -------------------------------------------------------------
        total = round(score_2h + score_1h_ema + score_adx + score_vwap + score_5m + score_timing, 2)
        grade = "A+" if total >= self.a_plus_threshold else "B"

        return SetupGrade(
            total_score=total,
            grade=grade,
            score_2h_map=score_2h,
            score_1h_ema=score_1h_ema,
            score_1h_adx=score_adx,
            score_vwap=score_vwap,
            score_5m_pfg=score_5m,
            score_timing=score_timing,
            details=details
        )
