"""
Forensic Context Extractor for DCC Trading Strategy.
Extracts deep multi-timeframe features, tick excursions (MAE/MFE),
and microstructure parameters for any live or backtest trade.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import MetaTrader5 as mt5


@dataclass
class TradeForensicProfile:
    # Identifiers
    ticket: Optional[int]
    symbol: str
    direction: str  # "BUY" or "SELL"
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    realized_pnl: float
    pnl_r: float
    outcome: str  # "WIN_TP2", "WIN_TP1_BE", "LOSS_SL", "SCRATCH"

    # Pillar 1: HTF Trend & Exhaustion (1H Dimension)
    h1_bias: int
    h1_adx: float
    h1_adx_slope: float  # Change over recent 1H bars
    h1_atr: float
    h1_ema20: float
    h1_ema20_distance: float
    h1_ema20_stretch_ratio: float  # Distance / ATR_1H (Rubber-band multiplier)
    is_htf_overextended: bool  # stretch_ratio > 1.35
    is_adx_exhausted: bool  # ADX > 25 and slope < -0.5

    # Pillar 2: Liquidity Sweep Quality (5M Turtle Soup Dimension)
    sweep_detected: bool
    sweep_level: float
    swept_pts: float
    sweep_penetration_ratio: float  # swept_pts / ATR_5M
    is_major_swing_sweep: bool  # 2H swing vs minor 5M swing
    sweep_quality_score: float  # 0 to 100 LSQI

    # Pillar 3: Volume & Execution Microstructure (5M Dimension)
    rvol_5m: float  # Trigger volume / 20 SMA volume
    candle_body_ratio: float  # Body / Total range
    adverse_wick_ratio: float  # Wick fighting trade direction
    vwap_5m: float
    vwap_distance: float
    vwap_alignment: bool  # True if entry is correctly aligned with VWAP
    ema_gap_pts: float
    ema_gap_ratio: float  # Gap / ATR_5M

    # Pillar 4: Trajectory & Excursion Dynamics (MAE / MFE)
    mfe_pts: float
    mfe_r: float  # Maximum Favorable Excursion in R
    mae_pts: float
    mae_r: float  # Maximum Adverse Excursion in R
    bars_held: int
    bars_to_mfe: int
    near_miss_tp1: bool  # MFE >= 1.0R while TP1 >= 1.4R but ended in SL/BE
    near_miss_sl: bool  # MAE >= 0.85R before recovering
    instant_reversal: bool  # MFE < 0.25R and stopped out rapidly

    # Pillar 5: Macro & Friction
    session_phase: str  # "LONDON_OPEN", "OVERLAP", "LATE_NY", etc.
    spread_at_entry: float
    spread_to_sl_ratio: float  # Spread / SL distance %

    # Raw metrics bundle
    extra_metrics: Dict[str, Any] = field(default_factory=dict)


class ForensicContextExtractor:
    """Extracts forensic multi-timeframe profile for trades from CSV calculations and MT5 price streams."""

    def __init__(self, calculation_csv_path: Optional[str] = None):
        self.calc_df: Optional[pd.DataFrame] = None
        if calculation_csv_path and os.path.exists(calculation_csv_path):
            self.load_calculation_csv(calculation_csv_path)

    def load_calculation_csv(self, csv_path: str):
        """Loads and indexes a market calculations CSV."""
        df = pd.read_csv(csv_path)
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
        self.calc_df = df.sort_values("timestamp_utc")

    def extract_from_live_trade(
        self,
        symbol: str,
        ticket: int,
        direction: str,
        entry_time: datetime,
        entry_price: float,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
        exit_time: Optional[datetime] = None,
        exit_price: Optional[float] = None,
        realized_pnl: float = 0.0,
        calc_df: Optional[pd.DataFrame] = None
    ) -> TradeForensicProfile:
        """Extracts complete forensic autopsy for a live MT5 trade."""
        active_df = calc_df if calc_df is not None else self.calc_df

        # 1. Locate entry bar in market calculations
        entry_row = None
        if active_df is not None:
            sym_df = active_df[active_df["symbol"] == symbol]
            # Match closest bar at or immediately prior to entry_time
            matched = sym_df[sym_df["timestamp_utc"] <= entry_time]
            if not matched.empty:
                entry_row = matched.iloc[-1]

        # 2. Extract HTF & Microstructure from calculation bar
        h1_bias = int(entry_row["bias_1h"]) if entry_row is not None and "bias_1h" in entry_row else (1 if direction == "BUY" else -1)
        h1_adx = float(entry_row["adx_1h"]) if entry_row is not None and "adx_1h" in entry_row else 20.0
        h1_atr = float(entry_row["atr_1h"]) if entry_row is not None and "atr_1h" in entry_row else 15.0
        h1_ema20 = float(entry_row["h1_ema20_level"]) if entry_row is not None and "h1_ema20_level" in entry_row else entry_price

        # Calculate ADX slope from prior bars if available
        h1_adx_slope = 0.0
        if active_df is not None and entry_row is not None:
            prior_h1_bars = active_df[(active_df["symbol"] == symbol) & (active_df["timestamp_utc"] < entry_row["timestamp_utc"])]
            if len(prior_h1_bars) >= 12:  # ~1 hour back
                prev_adx = float(prior_h1_bars.iloc[-12].get("adx_1h", h1_adx))
                h1_adx_slope = round(h1_adx - prev_adx, 2)

        h1_ema20_dist = abs(entry_price - h1_ema20)
        stretch_ratio = round(h1_ema20_dist / max(0.1, h1_atr), 2)
        is_htf_overextended = stretch_ratio >= 1.35
        is_adx_exhausted = (h1_adx >= 24.0 and h1_adx_slope < -0.4)

        # Liquidity sweep features
        sweep_detected = bool(entry_row["sweep_detected"]) if entry_row is not None and "sweep_detected" in entry_row else False
        sweep_level = float(entry_row["sweep_level"]) if entry_row is not None and "sweep_level" in entry_row else 0.0
        swept_pts = float(entry_row["swept_pts"]) if entry_row is not None and "swept_pts" in entry_row else 0.0
        atr_5m = max(0.1, h1_atr * 0.35)
        sweep_penetration_ratio = round(swept_pts / atr_5m, 2)

        # Check if swept level matched a 2H swing level
        sw_low_2h = float(entry_row.get("swing_low_2h", 0.0)) if entry_row is not None else 0.0
        sw_high_2h = float(entry_row.get("swing_high_2h", 0.0)) if entry_row is not None else 0.0
        is_major_swing = (abs(sweep_level - sw_low_2h) < 2.0 or abs(sweep_level - sw_high_2h) < 2.0) if sweep_level > 0 else False

        # LSQI (Liquidity Sweep Quality Index): 0 to 100
        # Optimal sweep is 0.15x to 0.6x ATR with a major 2H swing
        lsqi = 50.0
        if sweep_detected:
            lsqi += 20.0
            if is_major_swing:
                lsqi += 20.0
            if 0.15 <= sweep_penetration_ratio <= 0.60:
                lsqi += 10.0
            elif sweep_penetration_ratio > 1.0:
                lsqi -= 20.0  # Runaway counter-trend blowout
        else:
            lsqi = 20.0
        lsqi = max(0.0, min(100.0, lsqi))

        # Microstructure features
        volume = float(entry_row.get("volume", 500.0)) if entry_row is not None else 500.0
        rvol = 1.0
        if active_df is not None and entry_row is not None:
            recent_vols = active_df[(active_df["symbol"] == symbol) & (active_df["timestamp_utc"] <= entry_row["timestamp_utc"])].tail(20)["volume"]
            if len(recent_vols) >= 5:
                rvol = round(volume / max(1.0, float(recent_vols.mean())), 2)

        c_open = float(entry_row.get("open", entry_price)) if entry_row is not None else entry_price
        c_high = float(entry_row.get("high", entry_price)) if entry_row is not None else entry_price
        c_low = float(entry_row.get("low", entry_price)) if entry_row is not None else entry_price
        c_close = float(entry_row.get("close", entry_price)) if entry_row is not None else entry_price
        tot_range = max(0.01, c_high - c_low)
        body_range = abs(c_close - c_open)
        body_ratio = round(body_range / tot_range, 2)

        if direction == "BUY":
            adv_wick = c_high - max(c_open, c_close)  # Upper wick rejecting highs
        else:
            adv_wick = min(c_open, c_close) - c_low  # Lower wick rejecting lows
        adv_wick_ratio = round(adv_wick / tot_range, 2)

        vwap_5m = float(entry_row.get("vwap_5m", entry_price)) if entry_row is not None else entry_price
        vwap_dist = round(abs(entry_price - vwap_5m), 2)
        vwap_aligned = (entry_price >= vwap_5m) if direction == "BUY" else (entry_price <= vwap_5m)

        ema_gap = float(entry_row.get("ema_gap", 1.0)) if entry_row is not None else 1.0
        ema_gap_ratio = round(ema_gap / atr_5m, 2)

        # 3. Compute Tick/Bar Excursion Dynamics (MAE / MFE)
        mfe_pts, mae_pts, mfe_r, mae_r, bars_held, bars_to_mfe = self._compute_excursion_metrics(
            symbol=symbol,
            direction=direction,
            entry_time=entry_time,
            entry_price=entry_price,
            sl_price=sl_price,
            exit_time=exit_time
        )

        # Classify excursion signals
        sl_dist = max(0.01, abs(entry_price - sl_price))
        near_miss_tp1 = (mfe_r >= 1.0 and realized_pnl <= 0.05)
        near_miss_sl = (mae_r >= 0.85 and realized_pnl > 0.0)
        instant_reversal = (mfe_r < 0.25 and realized_pnl < 0.0 and bars_held <= 4)

        # Outcome classification
        pnl_r = round(realized_pnl / max(1.0, (sl_dist * (100.0 if "XAU" in symbol else 10.0) * 0.04)), 2)
        if realized_pnl > 10.0:
            outcome = "WIN_TP2" if mfe_r >= 2.0 else "WIN_TP1_BE"
        elif abs(realized_pnl) <= 5.0 and mfe_r >= 1.2:
            outcome = "SCRATCH_BE"
        else:
            outcome = "LOSS_SL"

        # Session phase
        entry_h = entry_time.hour
        if 6 <= entry_h < 9:
            session_phase = "LONDON_OPEN"
        elif 9 <= entry_h < 12:
            session_phase = "LONDON_MORNING"
        elif 12 <= entry_h < 16:
            session_phase = "LONDON_NY_OVERLAP"
        elif 16 <= entry_h < 19:
            session_phase = "LATE_NY"
        else:
            session_phase = "ASIAN_ROLLOVER"

        spread = 0.35 if "XAU" in symbol else 1.8
        spread_to_sl = round((spread / sl_dist) * 100.0, 2)

        return TradeForensicProfile(
            ticket=ticket,
            symbol=symbol,
            direction=direction,
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=entry_price,
            exit_price=exit_price if exit_price is not None else entry_price,
            sl_price=sl_price,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            realized_pnl=realized_pnl,
            pnl_r=pnl_r,
            outcome=outcome,
            h1_bias=h1_bias,
            h1_adx=h1_adx,
            h1_adx_slope=h1_adx_slope,
            h1_atr=h1_atr,
            h1_ema20=h1_ema20,
            h1_ema20_distance=h1_ema20_dist,
            h1_ema20_stretch_ratio=stretch_ratio,
            is_htf_overextended=is_htf_overextended,
            is_adx_exhausted=is_adx_exhausted,
            sweep_detected=sweep_detected,
            sweep_level=sweep_level,
            swept_pts=swept_pts,
            sweep_penetration_ratio=sweep_penetration_ratio,
            is_major_swing_sweep=is_major_swing,
            sweep_quality_score=lsqi,
            rvol_5m=rvol,
            candle_body_ratio=body_ratio,
            adverse_wick_ratio=adv_wick_ratio,
            vwap_5m=vwap_5m,
            vwap_distance=vwap_dist,
            vwap_alignment=vwap_aligned,
            ema_gap_pts=ema_gap,
            ema_gap_ratio=ema_gap_ratio,
            mfe_pts=mfe_pts,
            mfe_r=mfe_r,
            mae_pts=mae_pts,
            mae_r=mae_r,
            bars_held=bars_held,
            bars_to_mfe=bars_to_mfe,
            near_miss_tp1=near_miss_tp1,
            near_miss_sl=near_miss_sl,
            instant_reversal=instant_reversal,
            session_phase=session_phase,
            spread_at_entry=spread,
            spread_to_sl_ratio=spread_to_sl
        )

    def _compute_excursion_metrics(
        self,
        symbol: str,
        direction: str,
        entry_time: datetime,
        entry_price: float,
        sl_price: float,
        exit_time: Optional[datetime] = None
    ) -> Tuple[float, float, float, float, int, int]:
        """Calculates Maximum Favorable Excursion and Maximum Adverse Excursion."""
        sl_dist = max(0.01, abs(entry_price - sl_price))

        # If MT5 is available, fetch actual M5 or tick trajectory
        end_time = exit_time if exit_time else datetime.now(timezone.utc)
        bars_held = 1
        bars_to_mfe = 0
        mfe_pts = 0.0
        mae_pts = 0.0

        if mt5.initialize():
            # Query rates between entry and exit
            rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, entry_time - timedelta(minutes=5), end_time + timedelta(minutes=5))
            if rates is not None and len(rates) > 0:
                df_rates = pd.DataFrame(rates)
                df_rates["time"] = pd.to_datetime(df_rates["time"], unit="s", utc=True)
                trade_rates = df_rates[(df_rates["time"] >= entry_time) & (df_rates["time"] <= end_time)]
                if trade_rates.empty:
                    trade_rates = df_rates

                bars_held = max(1, len(trade_rates))
                if direction == "BUY":
                    highs = trade_rates["high"].values
                    lows = trade_rates["low"].values
                    mfe_pts = max(0.0, float(np.max(highs) - entry_price))
                    mae_pts = max(0.0, float(entry_price - np.min(lows)))
                    bars_to_mfe = int(np.argmax(highs))
                else:
                    highs = trade_rates["high"].values
                    lows = trade_rates["low"].values
                    mfe_pts = max(0.0, float(entry_price - np.min(lows)))
                    mae_pts = max(0.0, float(np.max(highs) - entry_price))
                    bars_to_mfe = int(np.argmin(lows))

        mfe_r = round(mfe_pts / sl_dist, 2)
        mae_r = round(mae_pts / sl_dist, 2)
        return round(mfe_pts, 2), round(mae_pts, 2), mfe_r, mae_r, bars_held, bars_to_mfe
