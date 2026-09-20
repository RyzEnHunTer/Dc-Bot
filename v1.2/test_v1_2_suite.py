"""
Comprehensive Unit Test Suite for DCC v1.2 TripleGuard Engine
=============================================================
Tests all shield boundaries, calendar filters, and signal generation.
"""

import unittest
from datetime import datetime, timezone
import pandas as pd
import numpy as np

import sys
import os
V12_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(V12_DIR)
if V12_DIR not in sys.path:
    sys.path.insert(0, V12_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dcc_engine_v1_2 import DCCEngineV12, SignalType
from config_v1_2 import SYMBOL_CONFIGS_V12


class TestDCCEngineV12(unittest.TestCase):
    def setUp(self):
        self.cfg_xau = SYMBOL_CONFIGS_V12["XAUUSD"]
        self.engine_xau = DCCEngineV12(
            symbol="XAUUSD",
            atr_sl_multiplier=self.cfg_xau.atr_sl_multiplier,
            tp1_rr=self.cfg_xau.tp1_rr,
            tp2_rr=self.cfg_xau.tp2_rr,
            min_1h_stretch=0.40,
            max_1h_adx=45.0,
            max_5m_chase=0.50,
            block_monday_pm=True
        )

    def _create_mock_bars(
        self,
        curr_time: datetime,
        bias_1h: int = 1,
        prev_diff: float = -0.5,
        curr_diff: float = 0.5,
        close: float = 4300.0,
        vwap: float = 4290.0,
        ema20_1h: float = 4280.0,
        atr_1h: float = 20.0,
        adx_1h: float = 30.0,
        stretch_ratio: float = 1.00,
        chase_ratio: float = 0.20
    ):
        prev_time = curr_time - pd.Timedelta(minutes=5)
        prev_bar = pd.Series({
            'ema9_5m': 4295.0,
            'ema20_5m': 4295.0 - prev_diff,
            'close': close - 2.0
        }, name=prev_time)

        curr_bar = pd.Series({
            'ema9_5m': 4298.0,
            'ema20_5m': 4298.0 - curr_diff,
            'close': close,
            'bias_1h': bias_1h,
            'vwap_5m': vwap,
            'ema20_1h_level': ema20_1h,
            'atr_1h': atr_1h,
            'adx_1h': adx_1h,
            'stretch_ratio': stretch_ratio,
            'chase_ratio': chase_ratio
        }, name=curr_time)

        return prev_bar, curr_bar

    def test_valid_bullish_signal_generation(self):
        """Test that a valid bullish setup passing all shields produces a TradeSignalV12."""
        # Tuesday 10:00 UTC (Normal trading hours)
        t = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
        prev_bar, curr_bar = self._create_mock_bars(curr_time=t)

        sig = self.engine_xau.evaluate_bar(prev_bar, curr_bar)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.signal_type, SignalType.BUY)
        self.assertEqual(sig.symbol, "XAUUSD")
        self.assertEqual(sig.entry_price, 4300.0)
        self.assertAlmostEqual(sig.sl_distance, 0.90 * 20.0, places=2)  # 18.0
        self.assertAlmostEqual(sig.stop_loss, 4300.0 - 18.0, places=2)  # 4282.0
        self.assertAlmostEqual(sig.take_profit_1, 4300.0 + (1.4 * 18.0), places=2)  # 4325.2
        self.assertAlmostEqual(sig.take_profit_2, 4300.0 + (2.2 * 18.0), places=2)  # 4339.6
        self.assertTrue(sig.shield_diagnostics["anti_chop_floor"])
        self.assertTrue(sig.shield_diagnostics["adx_ceiling"])
        self.assertTrue(sig.shield_diagnostics["no_chase_guard"])

    def test_shield1_anti_chop_floor_rejection(self):
        """Test that Stretch < 0.40 rejects the trade (Anti-Chop Floor)."""
        t = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
        prev_bar, curr_bar = self._create_mock_bars(curr_time=t, stretch_ratio=0.35)

        sig = self.engine_xau.evaluate_bar(prev_bar, curr_bar)
        self.assertIsNone(sig, "Trade with stretch < 0.40 must be rejected by Shield 1!")

    def test_shield2_adx_exhaustion_ceiling_rejection(self):
        """Test that ADX > 45.0 rejects the trade (Exhaustion Ceiling)."""
        t = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
        prev_bar, curr_bar = self._create_mock_bars(curr_time=t, adx_1h=48.5)

        sig = self.engine_xau.evaluate_bar(prev_bar, curr_bar)
        self.assertIsNone(sig, "Trade with ADX > 45 must be rejected by Shield 2!")

    def test_shield3_no_chase_guard_rejection(self):
        """Test that Chase Ratio > 0.50 rejects the trade (No-Chase Guard)."""
        t = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
        prev_bar, curr_bar = self._create_mock_bars(curr_time=t, chase_ratio=0.65)

        sig = self.engine_xau.evaluate_bar(prev_bar, curr_bar)
        self.assertIsNone(sig, "Trade with Chase > 0.50 must be rejected by Shield 3!")

    def test_monday_afternoon_trap_shield_blocking(self):
        """Test that Monday between 14:00 and 18:00 UTC is blocked."""
        # Monday Sep 14, 2026 at 15:30 UTC
        t = datetime(2026, 9, 14, 15, 30, tzinfo=timezone.utc)
        prev_bar, curr_bar = self._create_mock_bars(curr_time=t)

        sig = self.engine_xau.evaluate_bar(prev_bar, curr_bar)
        self.assertIsNone(sig, "Monday US afternoon (14:00 - 18:00 UTC) must be blocked!")

    def test_monday_morning_allowed(self):
        """Test that Monday morning (e.g. 07:30 UTC) is allowed to trade normally."""
        # Monday Sep 14, 2026 at 07:30 UTC
        t = datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc)
        prev_bar, curr_bar = self._create_mock_bars(curr_time=t)

        sig = self.engine_xau.evaluate_bar(prev_bar, curr_bar)
        self.assertIsNotNone(sig, "Monday London morning must be allowed!")

    def test_friday_trading_allowed(self):
        """Test that Friday during core session (e.g. 10:00 UTC) is allowed to trade normally."""
        # Friday Sep 18, 2026 at 10:00 UTC
        t = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
        prev_bar, curr_bar = self._create_mock_bars(curr_time=t)

        sig = self.engine_xau.evaluate_bar(prev_bar, curr_bar)
        self.assertIsNotNone(sig, "Friday core hours must be allowed!")

    def test_outside_session_rejection(self):
        """Test that bars outside 06:00 to 19:00 UTC are rejected."""
        # 04:00 UTC (Asian session)
        t = datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc)
        prev_bar, curr_bar = self._create_mock_bars(curr_time=t)

        sig = self.engine_xau.evaluate_bar(prev_bar, curr_bar)
        self.assertIsNone(sig)

    def test_dead_trap_hours_rejection(self):
        """Test that institutional dead trap hours (09:00 and 13:00 UTC) filter chop but allow momentum."""
        # 09:30 UTC with stretch < 1.10 (Chop trap -> Blocked)
        t9 = datetime(2026, 9, 15, 9, 30, tzinfo=timezone.utc)
        prev_bar, curr_bar = self._create_mock_bars(curr_time=t9, stretch_ratio=1.00)
        self.assertIsNone(self.engine_xau.evaluate_bar(prev_bar, curr_bar), "09:00 UTC dead hour with stretch < 1.10 must be blocked!")

        # 09:30 UTC with stretch >= 1.10 (ApexHunter Momentum -> Allowed)
        prev_bar_m, curr_bar_m = self._create_mock_bars(curr_time=t9, stretch_ratio=1.25)
        self.assertIsNotNone(self.engine_xau.evaluate_bar(prev_bar_m, curr_bar_m), "09:00 UTC momentum with stretch >= 1.10 must be allowed!")

        # 13:15 UTC with stretch < 1.10 (Chop trap -> Blocked)
        t13 = datetime(2026, 9, 15, 13, 15, tzinfo=timezone.utc)
        prev_bar, curr_bar = self._create_mock_bars(curr_time=t13, stretch_ratio=0.80)
        self.assertIsNone(self.engine_xau.evaluate_bar(prev_bar, curr_bar), "13:00 UTC dead hour with stretch < 1.10 must be blocked!")


if __name__ == "__main__":
    unittest.main()
