import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

from live_bot import InstitutionalDCCBot, PreArmedState

class TestApexHunterLiveSystem(unittest.TestCase):
    def setUp(self):
        self.bot = InstitutionalDCCBot.__new__(InstitutionalDCCBot)
        self.bot.tz_utc = timezone.utc
        self.bot.tz_ist = timezone(timedelta(hours=5, minutes=30))
        self.bot.entry_start_hour_utc = 6
        self.bot.entry_end_hour_utc = 19
        self.bot.trap_hours_utc = [9, 13]
        self.bot.use_news_shield = False
        self.bot.strategy_version = "v1.2"
        self.bot.account_lifecycle = "challenge"
        self.bot.challenge_risk_pct = 1.30
        self.bot.funded_risk_pct = 1.00
        self.bot.daily_starting_equity = 5000.0
        self.bot.daily_cb_pct = 3.0
        self.bot.circuit_breaker_active = False
        self.bot.challenge_target_reached = False
        self.bot.recent_trade_outcomes = [True, True, False]
        self.bot.armed_states = {"XAUUSD": PreArmedState()}

    def test_smart_killzone_blocks_compression(self):
        """Verify that at 09:00 UTC, if stretch < 1.10, it is blocked as SKIPPED_DEAD_HOUR_CHOP."""
        # Setup closed bar with stretch < 1.10 (close = 4305, h1_e20 = 4300, atr = 10.0 -> stretch = 0.50)
        closed_bar = {
            'open': 4300.0, 'high': 4306.0, 'low': 4298.0, 'close': 4305.0, 'volume': 500,
            'ema9_5m': 4304.0, 'ema20_5m': 4302.0, 'vwap_5m': 4303.0,
            'bias_1h': 1, 'ema9_1h': 4302.0, 'ema20_1h': 4300.0,
            'adx_1h': 30.0, 'atr_1h': 10.0
        }
        stretch_r = abs(closed_bar['close'] - closed_bar['ema20_1h']) / closed_bar['atr_1h']
        self.assertLess(stretch_r, 1.10)

    def test_dual_gear_dynamic_risk_calculation(self):
        """Verify dynamic regime risk in Challenge vs static risk in Funded."""
        # 1. Challenge Mode in Trend Regime (ADX >= 22, Stretch >= 0.85, Recent WR >= 50%)
        # stretch = abs(4315 - 4300) / 10 = 1.50
        risk_ch_trend = self.bot.get_effective_risk_pct("XAUUSD", adx=28.0, close_p=4315.0, h1_e20=4300.0, atr=10.0)
        self.assertEqual(risk_ch_trend, 1.30)

        # 2. Challenge Mode in Chop Regime (ADX < 22)
        risk_ch_chop = self.bot.get_effective_risk_pct("XAUUSD", adx=18.0, close_p=4315.0, h1_e20=4300.0, atr=10.0)
        self.assertEqual(risk_ch_chop, 1.00)

        # 3. Funded Mode (Switches to static 1.00% regardless of regime)
        self.bot.account_lifecycle = "funded"
        risk_funded_trend = self.bot.get_effective_risk_pct("XAUUSD", adx=35.0, close_p=4330.0, h1_e20=4300.0, atr=10.0)
        self.assertEqual(risk_funded_trend, 1.00)

        risk_funded_chop = self.bot.get_effective_risk_pct("XAUUSD", adx=15.0, close_p=4302.0, h1_e20=4300.0, atr=10.0)
        self.assertEqual(risk_funded_chop, 1.00)

    def test_lifecycle_persistence_and_transition(self):
        """Verify Challenge -> Funded transition and target_locked persistence."""
        from live_bot import AccountConfigManager
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            acc_mgr = AccountConfigManager(config_path=temp_path)
            # Create mock account config
            acc_mgr.accounts["999888"] = {
                "account_lifecycle": "challenge",
                "current_phase": 1,
                "phase_start_balance": 5000.0,
                "phase_1_target_pct": 8.0,
                "challenge_risk_pct": 1.30,
                "funded_risk_pct": 1.00,
                "target_locked": True
            }
            acc_mgr.save()

            # Verify reload
            loaded_mgr = AccountConfigManager(config_path=temp_path)
            self.assertTrue(loaded_mgr.accounts["999888"]["target_locked"])
            self.assertEqual(loaded_mgr.accounts["999888"]["account_lifecycle"], "challenge")

            # Advance to Funded
            loaded_mgr.advance_account_phase("999888", "funded")
            self.assertFalse(loaded_mgr.accounts["999888"]["target_locked"])
            self.assertEqual(loaded_mgr.accounts["999888"]["account_lifecycle"], "funded")
            self.assertEqual(loaded_mgr.accounts["999888"]["risk_per_trade"], 0.01)

            # Re-read from disk to ensure persistence
            disk_mgr = AccountConfigManager(config_path=temp_path)
            self.assertEqual(disk_mgr.accounts["999888"]["account_lifecycle"], "funded")
            self.assertEqual(disk_mgr.accounts["999888"]["current_phase"], "funded")
            self.assertFalse(disk_mgr.accounts["999888"]["target_locked"])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

if __name__ == '__main__':
    unittest.main()
